// Proxy for the system dinput8.dll: we load the real one and forward every
// export the game uses, so dropping our DLL next to yso_win.exe is transparent.
//
// Params are kept ABI-compatible but header-free (REFIID/REFCLSID are pointers,
// LPVOID* is void**), so we don't need the DirectInput SDK headers here.
#include <windows.h>
#include <objbase.h>   // REFCLSID / REFIID for the COM-shaped exports

static HMODULE g_real = nullptr;

extern "C" void input_on_dinput8_created(void* idi8);  // hook_input.cpp

void proxy_load_real() {
    if (g_real) return;
    char path[MAX_PATH];
    UINT n = GetSystemDirectoryA(path, MAX_PATH);   // ...\Windows\System32 (WoW64-redirected to SysWOW64 for 32-bit)
    lstrcpyA(path + n, "\\dinput8.dll");
    g_real = LoadLibraryA(path);
}

static FARPROC real(const char* name) {
    if (!g_real) proxy_load_real();
    return g_real ? GetProcAddress(g_real, name) : nullptr;
}

extern "C" {

HRESULT WINAPI DirectInput8Create(HINSTANCE hinst, DWORD ver, const void* riid,
                                  void** out, void* outer) {
    typedef HRESULT(WINAPI* fn)(HINSTANCE, DWORD, const void*, void**, void*);
    static fn p = (fn)real("DirectInput8Create");
    if (!p) return E_FAIL;
    HRESULT hr = p(hinst, ver, riid, out, outer);
    if (SUCCEEDED(hr) && out && *out)
        input_on_dinput8_created(*out);   // hook CreateDevice -> GetDeviceState
    return hr;
}

HRESULT WINAPI DllCanUnloadNow() {
    typedef HRESULT(WINAPI* fn)();
    static fn p = (fn)real("DllCanUnloadNow");
    return p ? p() : S_FALSE;
}

HRESULT WINAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID* out) {
    typedef HRESULT(WINAPI* fn)(REFCLSID, REFIID, LPVOID*);
    static fn p = (fn)real("DllGetClassObject");
    return p ? p(rclsid, riid, out) : E_FAIL;
}

HRESULT WINAPI DllRegisterServer() {
    typedef HRESULT(WINAPI* fn)();
    static fn p = (fn)real("DllRegisterServer");
    return p ? p() : E_FAIL;
}

HRESULT WINAPI DllUnregisterServer() {
    typedef HRESULT(WINAPI* fn)();
    static fn p = (fn)real("DllUnregisterServer");
    return p ? p() : E_FAIL;
}

void* WINAPI GetdfDIJoystick() {
    typedef void* (WINAPI* fn)();
    static fn p = (fn)real("GetdfDIJoystick");
    return p ? p() : nullptr;
}

}  // extern "C"
