// DLL entry: load the real dinput8 (so the proxy is transparent) and kick off
// the D3D9 hook on a worker thread (doing it inside DllMain is unsafe).
#include <windows.h>

void proxy_load_real();
void hook_d3d9_install();
void hook_vm_install();
void mod_log(const char* fmt, ...);

static DWORD WINAPI init_thread(LPVOID) {
    mod_log("init_thread: started, installing hooks");
    hook_d3d9_install();   // overlay
    hook_vm_install();     // event-VM grant interception (detect-only)
    return 0;
}

BOOL APIENTRY DllMain(HMODULE mod, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(mod);
        mod_log("DllMain: DLL_PROCESS_ATTACH");
        proxy_load_real();
        mod_log("DllMain: proxy_load_real done, spawning init thread");
        CreateThread(nullptr, 0, init_thread, nullptr, 0, nullptr);
    }
    return TRUE;
}
