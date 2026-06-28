// Content replacement at the real runtime grant store.
//
// The executed event-VM (copy at ~0x567xxx; FUN_004472e0 is the dead twin)
// grants by:
//     0x567D15: mov ecx, [esi]      ; ecx = value
//     0x567D17: mov [eax], ecx      ; g_flags[idx] = value   <-- THE GRANT
//     0x567D19: jmp 0x5664c9
// where eax = &g_flags[idx]. We splice 0x567D17 *before* the write, let a C
// decision function inspect (idx, val), and have it return the address to write
// to. Returning the same address = pass through; returning a scratch sink =
// suppress (the vanilla item is never written — zero flash, unlike the external
// client's after-the-fact revert).
//
// Which items to suppress / which flags are checks is registered at runtime by
// the Python AP client over the socket bridge (hook_bridge.cpp). This is the
// in-process core of the randomizer: suppress vanilla, detect checks, and grant
// the player's real (networked) items via the bridge 'V' command.
//
// No ASLR (image base 0x400000) so the absolute address is valid at runtime.
#include <windows.h>
#include <cstdio>
#include "MinHook.h"

void mod_log(const char* fmt, ...);
void bridge_emit(const char* line);
void ap_on_check(int flag_idx);  // notify the embedded AP client (hook_ap.cpp)
extern bool g_loc_flag[0x200];   // registered randomized-location flags
extern bool g_supp_item[0x200];  // vanilla item indices to suppress

static const uintptr_t kGrantStore = 0x00567D17;  // mov [eax], ecx
static const uintptr_t kGFlagsBase = 0x0076B91C;
static void* g_orig = nullptr;

// Scratch sink for "suppress" (a grant redirected here never touches g_flags).
static int g_sink = 0;

// Returns the address the grant should actually write to (eax for the store).
// addr = &g_flags[idx] the VM intended; val = value about to be written.
//
// Bridge-driven: the Python AP client registers (via the socket bridge) which
// vanilla item indices to suppress and which location flags to watch.
//   * suppressed item -> redirect to a sink (vanilla never granted; the player
//     receives the AP item over the network, applied via the bridge 'V' cmd).
//   * registered location flag -> emit a check so the client sends a
//     LocationCheck; the flag itself still sets (location registers, chest opens).
extern "C" int* __cdecl DecideStore(int* addr, int val) {
    unsigned idx = (unsigned)(((uintptr_t)addr - kGFlagsBase) / 4);
    if (idx >= 0x200)
        return addr;  // script-local, not a g_flags grant — leave alone

    char buf[48];
    snprintf(buf, sizeof(buf), "G %X %d", idx, val);
    bridge_emit(buf);

    if (g_loc_flag[idx]) {  // a randomized location's flag is firing — a check
        snprintf(buf, sizeof(buf), "C %X", idx);
        bridge_emit(buf);     // legacy bridge (no-op if no socket client)
        ap_on_check((int)idx); // embedded AP client -> LocationChecks
        return addr;  // let the flag set; the chest/event plays normally
    }
    if (g_supp_item[idx] && val >= 1)  // vanilla content of a randomized loc
        return &g_sink;                // suppress (player gets the AP item)

    return addr;  // pass through unchanged
}

// Naked splice at 0x567D17 (pre-store). eax = &g_flags[idx], ecx = value.
// Call DecideStore(eax, ecx); it returns the (possibly redirected) target in
// eax. ecx (value) is preserved across the call, then the trampoline runs the
// original `mov [eax], ecx` with our chosen eax.
__declspec(naked) static void Hook_Grant() {
    __asm {
        pushfd
        push edx                 // edx is caller-clobbered by the C call
        push ecx                 // SAVE value (restored into ecx below)
        push ecx                 // arg2: val
        push eax                 // arg1: addr
        call DecideStore         // eax = target addr to store to
        add  esp, 8              // pop args
        pop  ecx                 // restore value into ecx
        pop  edx
        popfd
        jmp  dword ptr [g_orig]  // trampoline: mov [eax], ecx ; jmp 0x5664c9
    }
}

void hook_vm_install() {
    mod_log("hook_vm_install: begin (grant store @0x%X, override)", (unsigned)kGrantStore);
    MH_Initialize();  // may already be initialized by the D3D9 hook (returns 9)
    MH_STATUS c = MH_CreateHook((void*)kGrantStore, (void*)&Hook_Grant, &g_orig);
    MH_STATUS e = MH_EnableHook((void*)kGrantStore);
    mod_log("hook_vm_install: create=%d enable=%d (0=OK)", (int)c, (int)e);
}
