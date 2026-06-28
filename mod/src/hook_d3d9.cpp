// Hook Direct3D9 EndScene/Reset and draw a Dear ImGui overlay in-game.
//
// Standard approach: spin up a throwaway device to read the IDirect3DDevice9
// vtable (EndScene = slot 42, Reset = slot 16), MinHook those slots, then render
// ImGui from inside the real EndScene. Input is captured by subclassing the
// game's window. Toggle the overlay with the INSERT key.
#include <windows.h>
#include <d3d9.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include "MinHook.h"
#include "imgui.h"
#include "imgui_impl_dx9.h"
#include "imgui_impl_win32.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

// --- diagnostics: append-only log at %TEMP%\yso_ap_mod.log -------------------
// The file handle is kept open: the VM grant hook is a hot path, so a
// fopen/fclose per line would lag the game. Single-threaded callers (the VM and
// render both run on the game's main thread), so no locking needed.
static char g_logpath[MAX_PATH] = "";
static FILE* g_logf = nullptr;
void mod_log(const char* fmt, ...) {
    if (!g_logf) {
        DWORD n = GetTempPathA(MAX_PATH, g_logpath);
        lstrcpyA(g_logpath + n, "yso_ap_mod.log");
        g_logf = fopen(g_logpath, "a");
        if (!g_logf) return;
    }
    va_list ap; va_start(ap, fmt);
    vfprintf(g_logf, fmt, ap);
    va_end(ap);
    fputc('\n', g_logf);
    fflush(g_logf);
}

namespace overlay { void draw(); }

// Large font for the overlay's item names (~5x the default), built crisp at its
// native pixel size. Read by overlay.cpp.
ImFont* g_overlay_big_font = nullptr;

using EndScene_t = HRESULT(WINAPI*)(IDirect3DDevice9*);
using Reset_t = HRESULT(WINAPI*)(IDirect3DDevice9*, D3DPRESENT_PARAMETERS*);

static EndScene_t o_EndScene = nullptr;
static Reset_t o_Reset = nullptr;
static WNDPROC o_WndProc = nullptr;
static HWND g_hwnd = nullptr;
static bool g_imgui_ready = false;
static bool g_show = true;

static LRESULT CALLBACK hk_WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_KEYDOWN && wp == VK_INSERT)
        g_show = !g_show;
    if (g_show && ImGui_ImplWin32_WndProcHandler(hwnd, msg, wp, lp))
        return 1;  // ImGui swallowed the input while the overlay is open
    return CallWindowProc(o_WndProc, hwnd, msg, wp, lp);
}

static void imgui_init(IDirect3DDevice9* dev) {
    mod_log("imgui_init: begin (dev=%p)", (void*)dev);
    D3DDEVICE_CREATION_PARAMETERS cp{};
    dev->GetCreationParameters(&cp);
    g_hwnd = cp.hFocusWindow;
    mod_log("imgui_init: hFocusWindow=%p", (void*)g_hwnd);
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui::StyleColorsDark();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;  // don't write imgui.ini next to game
    io.Fonts->AddFontDefault();  // font[0]: small UI text (header/status)
    // font[1]: large item-name font (~5x). Prefer the GAME's own font
    // (release\yso_ins04.dat — a real TTF with glyf outlines, the one used in the
    // "Acquired" boxes), derived from the game exe's folder; fall back to system
    // fonts. ASCII range only (default) so the CJK atlas stays small.
    char gamefont[MAX_PATH] = "";
    if (GetModuleFileNameA(nullptr, gamefont, MAX_PATH)) {
        char* slash = strrchr(gamefont, '\\');
        if (slash) lstrcpyA(slash + 1, "release\\yso_ins04.dat");
    }
    const char* fonts[] = {gamefont,
                           "C:\\Windows\\Fonts\\segoeui.ttf",
                           "C:\\Windows\\Fonts\\arial.ttf"};
    for (const char* f : fonts) {
        if (!f[0]) continue;
        g_overlay_big_font = io.Fonts->AddFontFromFileTTF(f, 38.0f);
        if (g_overlay_big_font) { mod_log("imgui_init: big font %s", f); break; }
    }
    if (!g_overlay_big_font) g_overlay_big_font = io.Fonts->AddFontDefault();
    ImGui_ImplWin32_Init(g_hwnd);
    ImGui_ImplDX9_Init(dev);
    o_WndProc = (WNDPROC)SetWindowLongPtr(g_hwnd, GWLP_WNDPROC, (LONG_PTR)hk_WndProc);
    g_imgui_ready = true;
    mod_log("imgui_init: done (WndProc subclassed, ready)");
}

static bool g_logged_first_endscene = false;

static HRESULT WINAPI hk_EndScene(IDirect3DDevice9* dev) {
    if (!g_logged_first_endscene) {
        g_logged_first_endscene = true;
        mod_log("hk_EndScene: first call (dev=%p)", (void*)dev);
    }
    if (!g_imgui_ready)
        imgui_init(dev);
    if (g_show) {
        ImGui_ImplDX9_NewFrame();
        ImGui_ImplWin32_NewFrame();
        ImGui::NewFrame();
        overlay::draw();
        ImGui::EndFrame();
        ImGui::Render();
        ImGui_ImplDX9_RenderDrawData(ImGui::GetDrawData());
    }
    return o_EndScene(dev);
}

static HRESULT WINAPI hk_Reset(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp) {
    if (g_imgui_ready)
        ImGui_ImplDX9_InvalidateDeviceObjects();
    HRESULT hr = o_Reset(dev, pp);
    if (g_imgui_ready)
        ImGui_ImplDX9_CreateDeviceObjects();
    return hr;
}

// Read the device vtable by creating a temporary device.
static bool get_device_vtable(void** vtable, size_t count) {
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    mod_log("get_device_vtable: Direct3DCreate9 -> %p", (void*)d3d);
    if (!d3d) return false;

    WNDCLASSEXA wc{sizeof(wc)};
    wc.lpfnWndProc = DefWindowProcA;
    wc.hInstance = GetModuleHandle(nullptr);
    wc.lpszClassName = "yso_ap_probe";
    RegisterClassExA(&wc);
    HWND wnd = CreateWindowA(wc.lpszClassName, "", WS_OVERLAPPEDWINDOW,
                             0, 0, 8, 8, nullptr, nullptr, wc.hInstance, nullptr);

    D3DPRESENT_PARAMETERS pp{};
    pp.Windowed = TRUE;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = wnd;
    // Explicit 1x1 backbuffer: the tiny probe window has ~0 client area, so
    // letting D3D infer the size (0) yields D3DERR_INVALIDCALL. D3DFMT_UNKNOWN
    // is valid for a windowed device (uses the desktop format).
    pp.BackBufferWidth = 1;
    pp.BackBufferHeight = 1;
    pp.BackBufferFormat = D3DFMT_UNKNOWN;
    pp.BackBufferCount = 1;

    IDirect3DDevice9* dev = nullptr;
    // Try software then hardware vertex processing (some drivers reject one).
    HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd,
                                   D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
    if (FAILED(hr) || !dev) {
        mod_log("get_device_vtable: SW-VP CreateDevice hr=0x%08lX; retrying HW-VP", hr);
        hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd,
                               D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &dev);
    }
    mod_log("get_device_vtable: CreateDevice hr=0x%08lX dev=%p", hr, (void*)dev);
    bool ok = false;
    if (SUCCEEDED(hr) && dev) {
        memcpy(vtable, *reinterpret_cast<void***>(dev), count * sizeof(void*));
        dev->Release();
        ok = true;
    }
    if (d3d) d3d->Release();
    DestroyWindow(wnd);
    UnregisterClassA(wc.lpszClassName, wc.hInstance);
    return ok;
}

void hook_d3d9_install() {
    mod_log("hook_d3d9_install: begin");
    void* vt[119] = {};
    if (!get_device_vtable(vt, 119)) {
        mod_log("hook_d3d9_install: get_device_vtable FAILED -> aborting");
        return;
    }
    mod_log("hook_d3d9_install: vtable ok (EndScene=%p Reset=%p)", vt[42], vt[16]);
    if (MH_Initialize() != MH_OK) {
        mod_log("hook_d3d9_install: MH_Initialize FAILED");
        return;
    }
    MH_STATUS s1 = MH_CreateHook(vt[42], (void*)&hk_EndScene, (void**)&o_EndScene);
    MH_STATUS s2 = MH_CreateHook(vt[16], (void*)&hk_Reset, (void**)&o_Reset);
    MH_STATUS s3 = MH_EnableHook(MH_ALL_HOOKS);
    mod_log("hook_d3d9_install: CreateHook EndScene=%d Reset=%d Enable=%d (0=OK)",
            (int)s1, (int)s2, (int)s3);
}
