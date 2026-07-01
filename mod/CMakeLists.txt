# Ys Origin Archipelago — native in-game mod (Phase B, milestone 1).
#
# Builds a 32-bit `dinput8.dll` proxy that injects into yso_win.exe (it imports
# dinput8), hooks Direct3D9, and draws a Dear ImGui overlay in-game.
#
# Build (MSVC, from this dir):
#   cmake -A Win32 -B build
#   cmake --build build --config Release
# Output: build/Release/dinput8.dll  -> copy next to yso_win.exe.
#
# MUST be a 32-bit build (the game is 32-bit). With MSVC use `-A Win32`; in the
# Visual Studio IDE pick the **x86** configuration.

cmake_minimum_required(VERSION 3.20)
project(yso_ap_mod C CXX)   # C too: MinHook's sources are C

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "Configure a 32-bit build (cmake -A Win32, or the x86 IDE config). The game is 32-bit.")
endif()

include(FetchContent)

# --- MinHook (function detours) ------------------------------------------- #
# The v1.3.3 tag predates MinHook's CMake support, so just fetch the source and
# compile its C files straight into our DLL (robust, no extra target).
FetchContent_Declare(minhook
    GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
    GIT_TAG        v1.3.3)
FetchContent_GetProperties(minhook)
if(NOT minhook_POPULATED)
    FetchContent_Populate(minhook)
endif()
set(MINHOOK_SRC
    ${minhook_SOURCE_DIR}/src/buffer.c
    ${minhook_SOURCE_DIR}/src/hook.c
    ${minhook_SOURCE_DIR}/src/trampoline.c
    ${minhook_SOURCE_DIR}/src/hde/hde32.c
    ${minhook_SOURCE_DIR}/src/hde/hde64.c)

# --- Dear ImGui (no upstream CMakeLists -> populate, then compile sources) -- #
FetchContent_Declare(imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG        v1.91.5)
FetchContent_GetProperties(imgui)
if(NOT imgui_POPULATED)
    FetchContent_Populate(imgui)
endif()
set(IMGUI_DIR ${imgui_SOURCE_DIR})

# --- Archipelago C++ client (apclientpp) + its header-only deps ------------ #
# Self-contained AP networking inside the mod (no Python at runtime). All of the
# deps below are header-only; TLS for wss:// is provided by OpenSSL (found below).
FetchContent_Declare(json GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3)
FetchContent_Declare(asio GIT_REPOSITORY https://github.com/chriskohlhoff/asio.git
    GIT_TAG asio-1-28-0)
FetchContent_Declare(websocketpp GIT_REPOSITORY https://github.com/zaphoyd/websocketpp.git
    GIT_TAG 0.8.2)
FetchContent_Declare(wswrap GIT_REPOSITORY https://github.com/black-sliver/wswrap.git
    GIT_TAG main)
FetchContent_Declare(valijson GIT_REPOSITORY https://github.com/tristanpenman/valijson.git
    GIT_TAG v1.0.1)
FetchContent_Declare(apclientpp GIT_REPOSITORY https://github.com/black-sliver/apclientpp.git
    GIT_TAG main)
foreach(dep json asio websocketpp wswrap valijson apclientpp)
    FetchContent_GetProperties(${dep})
    if(NOT ${dep}_POPULATED)
        FetchContent_Populate(${dep})
    endif()
endforeach()
set(AP_INCLUDE_DIRS
    ${json_SOURCE_DIR}/single_include
    ${asio_SOURCE_DIR}/asio/include
    ${websocketpp_SOURCE_DIR}
    ${wswrap_SOURCE_DIR}/include
    ${valijson_SOURCE_DIR}/include
    ${apclientpp_SOURCE_DIR})

# --- OpenSSL (TLS for wss://, e.g. archipelago.gg) ------------------------- #
# Static 32-bit libs from vcpkg (x86-windows-static-md: static OpenSSL + dynamic
# CRT to match this DLL) -> self-contained, no extra DLLs to ship. Configure with
#   -DOPENSSL_ROOT_DIR=D:/vcpkg/installed/x86-windows-static-md
# wswrap loads CA certs from the Windows ROOT store (crypt32), so no cacert bundle.
set(OPENSSL_USE_STATIC_LIBS TRUE)
find_package(OpenSSL REQUIRED)

add_library(dinput8 SHARED
    src/dllmain.cpp
    src/proxy_dinput8.cpp
    src/hook_d3d9.cpp
    src/hook_vm.cpp
    src/hook_ap.cpp
    src/hook_bridge.cpp
    src/hook_watch.cpp
    src/hook_input.cpp
    src/hook_saveredir.cpp
    src/overlay.cpp
    src/chat.cpp
    src/menu.cpp
    src/exports.def
    ${IMGUI_DIR}/imgui.cpp
    ${IMGUI_DIR}/imgui_draw.cpp
    ${IMGUI_DIR}/imgui_tables.cpp
    ${IMGUI_DIR}/imgui_widgets.cpp
    ${IMGUI_DIR}/backends/imgui_impl_dx9.cpp
    ${IMGUI_DIR}/backends/imgui_impl_win32.cpp
    ${MINHOOK_SRC}
)
target_include_directories(dinput8 PRIVATE
    src ${IMGUI_DIR} ${IMGUI_DIR}/backends ${minhook_SOURCE_DIR}/include
    ${AP_INCLUDE_DIRS})
target_link_libraries(dinput8 PRIVATE
    d3d9 dxguid ws2_32 crypt32 OpenSSL::SSL OpenSSL::Crypto)

# apclientpp / asio / websocketpp build knobs. SSL is ON (wss:// support); only
# zlib compression is disabled (not header-only).
target_compile_definitions(dinput8 PRIVATE
    ASIO_STANDALONE
    _WEBSOCKETPP_CPP11_STL_
    WSWRAP_NO_COMPRESSION   # avoid zlib (not header-only)
    WIN32_LEAN_AND_MEAN
    _WIN32_WINNT=0x0600)
# MSVC needs accurate __cplusplus and big object files for asio/json/websocketpp.
target_compile_options(dinput8 PRIVATE /Zc:__cplusplus /bigobj)

# Name the artifact exactly dinput8.dll (no "lib" prefix on MinGW).
set_target_properties(dinput8 PROPERTIES PREFIX "" OUTPUT_NAME "dinput8")
