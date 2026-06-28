// Hook Direct3D9 EndScene/Reset and draw a Dear ImGui overlay in-game.
//
// Standard approach: spin up a throwaway device to read the IDirect3DDevice9
// vtable (EndScene = slot 42, Reset = slot 16), MinHook those slots, then render
// ImGui from inside the real EndScene. Input is captured by subclassing the
// game's window. Toggle the overlay with the INSERT key.
#include <windows.h>
#include <d3d9.h>
#include "MinHook.h"
#include "imgui.h"
#include "imgui_impl_dx9.h"
#include "imgui_impl_win32.h"

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace overlay { void draw(); }

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
    D3DDEVICE_CREATION_PARAMETERS cp{};
    dev->GetCreationParameters(&cp);
    g_hwnd = cp.hFocusWindow;
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui::StyleColorsDark();
    ImGui::GetIO().IniFilename = nullptr;  // don't write imgui.ini next to game
    ImGui_ImplWin32_Init(g_hwnd);
    ImGui_ImplDX9_Init(dev);
    o_WndProc = (WNDPROC)SetWindowLongPtr(g_hwnd, GWLP_WNDPROC, (LONG_PTR)hk_WndProc);
    g_imgui_ready = true;
}

static HRESULT WINAPI hk_EndScene(IDirect3DDevice9* dev) {
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

    IDirect3DDevice9* dev = nullptr;
    HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd,
                                   D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
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
    void* vt[119] = {};
    if (!get_device_vtable(vt, 119)) return;
    if (MH_Initialize() != MH_OK) return;

    MH_CreateHook(vt[42], (void*)&hk_EndScene, (void**)&o_EndScene);  // EndScene
    MH_CreateHook(vt[16], (void*)&hk_Reset, (void**)&o_Reset);        // Reset
    MH_EnableHook(MH_ALL_HOOKS);
}
