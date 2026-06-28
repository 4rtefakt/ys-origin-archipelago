// DLL entry: load the real dinput8 (so the proxy is transparent) and kick off
// the D3D9 hook on a worker thread (doing it inside DllMain is unsafe).
#include <windows.h>

void proxy_load_real();
void hook_d3d9_install();

static DWORD WINAPI init_thread(LPVOID) {
    hook_d3d9_install();
    return 0;
}

BOOL APIENTRY DllMain(HMODULE mod, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(mod);
        proxy_load_real();
        CreateThread(nullptr, 0, init_thread, nullptr, 0, nullptr);
    }
    return TRUE;
}
