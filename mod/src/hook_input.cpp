// Freeze the game's own input while the Archipelago connect form is open, so the
// title menu doesn't navigate/confirm behind it.
//
// The title menu reads the keyboard through a DirectInput device
// (IDirectInputDevice8::GetDeviceState) — confirmed live: freezing only
// GetAsyncKeyState/GetKeyState left the menu still moving. So the real freeze is
// a detour on GetDeviceState that zeroes the returned buffer while capturing;
// that covers keyboard AND gamepad (both are DI devices). We also keep the
// GetAsyncKeyState/GetKeyState detours (belt-and-suspenders for any key the game
// reads that way).
//
// Our own menu input comes from window messages (WM_KEYDOWN / WM_CHAR via the
// WndProc subclass), which are unaffected by these detours.
#include <windows.h>
#include "MinHook.h"

void mod_log(const char* fmt, ...);
namespace apmenu { bool is_capturing(); }
namespace apchat { bool is_capturing(); }
static inline bool ap_capturing() {
    return apmenu::is_capturing() || apchat::is_capturing();
}

// ---- race-safe MinHook init (D3D hook, input hooks and the DirectInput proxy
// can all reach MinHook from different threads) ------------------------------
static volatile LONG g_mh_state = 0;   // 0 uninit, 1 initializing, 2 ready
void mh_ensure_init() {
    if (InterlockedCompareExchange(&g_mh_state, 1, 0) == 0) {
        MH_Initialize();
        InterlockedExchange(&g_mh_state, 2);
    } else {
        while (InterlockedCompareExchange(&g_mh_state, 2, 2) != 2) Sleep(0);
    }
}

// ---- GetAsyncKeyState / GetKeyState ----------------------------------------
using GAKS_t = SHORT(WINAPI*)(int);
using GKS_t  = SHORT(WINAPI*)(int);
static GAKS_t o_GetAsyncKeyState = nullptr;
static GKS_t  o_GetKeyState = nullptr;

static SHORT WINAPI hk_GetAsyncKeyState(int vk) {
    if (ap_capturing()) return 0;
    return o_GetAsyncKeyState(vk);
}
static SHORT WINAPI hk_GetKeyState(int vk) {
    if (ap_capturing()) return 0;
    return o_GetKeyState(vk);
}

// ---- DirectInput device GetDeviceState (the real menu-input freeze) ---------
// COM methods are __stdcall with `this` as the first argument. We model the
// interfaces header-free (void* self, opaque GUID/IUnknown pointers).
using GetDeviceState_t = HRESULT(WINAPI*)(void* self, DWORD cb, void* data);
using CreateDevice_t   = HRESULT(WINAPI*)(void* self, const void* rguid,
                                          void** dev, void* outer);
static GetDeviceState_t o_GetDeviceState = nullptr;
static CreateDevice_t   o_CreateDevice = nullptr;
static bool g_dev_state_hooked = false;

static HRESULT WINAPI hk_GetDeviceState(void* self, DWORD cb, void* data) {
    HRESULT hr = o_GetDeviceState(self, cb, data);
    // Freeze ONLY the keyboard (its DI state is a 256-byte DIK array; 0 == no key
    // down). Do NOT blank joystick/mouse buffers: an analog stick's neutral is a
    // mid-range value, so zeroing it reads as "full up" and scrolls the menu.
    if (ap_capturing() && data && cb == 256) memset(data, 0, cb);
    return hr;
}

// IDirectInputDevice8 vtable slot 9 = GetDeviceState. All device instances from
// the same dinput8 share one vtable, so hooking once (first device) covers all.
static HRESULT WINAPI hk_CreateDevice(void* self, const void* rguid,
                                      void** dev, void* outer) {
    HRESULT hr = o_CreateDevice(self, rguid, dev, outer);
    if (SUCCEEDED(hr) && dev && *dev && !g_dev_state_hooked) {
        void** vt = *reinterpret_cast<void***>(*dev);
        if (MH_CreateHook(vt[9], (void*)&hk_GetDeviceState,
                          (void**)&o_GetDeviceState) == MH_OK &&
            MH_EnableHook(vt[9]) == MH_OK) {
            g_dev_state_hooked = true;
            mod_log("input: hooked IDirectInputDevice8::GetDeviceState (%p)", vt[9]);
        } else {
            mod_log("input: FAILED to hook GetDeviceState");
        }
    }
    return hr;
}

// Called from the dinput8 proxy right after a real IDirectInput8 is created,
// BEFORE the game calls CreateDevice — so we catch every device it makes.
extern "C" void input_on_dinput8_created(void* idi8) {
    if (!idi8) return;
    mh_ensure_init();
    void** vt = *reinterpret_cast<void***>(idi8);   // IDirectInput8 vtable
    // slot 3 = CreateDevice
    if (MH_CreateHook(vt[3], (void*)&hk_CreateDevice, (void**)&o_CreateDevice) == MH_OK
        && MH_EnableHook(vt[3]) == MH_OK)
        mod_log("input: hooked IDirectInput8::CreateDevice (%p)", vt[3]);
    else
        mod_log("input: FAILED to hook CreateDevice");
}

// Called from init_thread. GAKS/GKS are safe to hook up front.
void input_hooks_install() {
    mh_ensure_init();
    HMODULE u = GetModuleHandleA("user32.dll");
    if (!u) { mod_log("input: user32 not loaded?"); return; }
    void* pA = (void*)GetProcAddress(u, "GetAsyncKeyState");
    void* pK = (void*)GetProcAddress(u, "GetKeyState");
    MH_STATUS a1 = MH_CreateHook(pA, (void*)&hk_GetAsyncKeyState, (void**)&o_GetAsyncKeyState);
    MH_STATUS a2 = MH_CreateHook(pK, (void*)&hk_GetKeyState, (void**)&o_GetKeyState);
    MH_EnableHook(pA); MH_EnableHook(pK);
    mod_log("input: GAKS hook=%d GKS hook=%d", (int)a1, (int)a2);
}
