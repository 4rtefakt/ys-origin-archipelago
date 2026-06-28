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
project(yso_ap_mod CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "Configure a 32-bit build (cmake -A Win32, or the x86 IDE config). The game is 32-bit.")
endif()

include(FetchContent)

# --- MinHook (function detours) ------------------------------------------- #
FetchContent_Declare(minhook
    GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
    GIT_TAG        v1.3.3)
FetchContent_MakeAvailable(minhook)

# --- Dear ImGui (no upstream CMakeLists -> populate, then compile sources) -- #
FetchContent_Declare(imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG        v1.91.5)
FetchContent_GetProperties(imgui)
if(NOT imgui_POPULATED)
    FetchContent_Populate(imgui)
endif()
set(IMGUI_DIR ${imgui_SOURCE_DIR})

add_library(dinput8 SHARED
    src/dllmain.cpp
    src/proxy_dinput8.cpp
    src/hook_d3d9.cpp
    src/overlay.cpp
    src/exports.def
    ${IMGUI_DIR}/imgui.cpp
    ${IMGUI_DIR}/imgui_draw.cpp
    ${IMGUI_DIR}/imgui_tables.cpp
    ${IMGUI_DIR}/imgui_widgets.cpp
    ${IMGUI_DIR}/backends/imgui_impl_dx9.cpp
    ${IMGUI_DIR}/backends/imgui_impl_win32.cpp
)
target_include_directories(dinput8 PRIVATE
    src ${IMGUI_DIR} ${IMGUI_DIR}/backends)
target_link_libraries(dinput8 PRIVATE minhook d3d9 dxguid)

# Name the artifact exactly dinput8.dll (no "lib" prefix on MinGW).
set_target_properties(dinput8 PROPERTIES PREFIX "" OUTPUT_NAME "dinput8")
