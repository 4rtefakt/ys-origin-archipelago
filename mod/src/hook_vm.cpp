// Content replacement at the real runtime grant store.
//
// The executed event-VM (copy at ~0x567xxx; FUN_004472e0 is the dead twin)
// grants by:
//     0x567D15: mov ecx, [esi]      ; ecx = value
//     0x567D17: mov [eax], ecx      ; g_flags[idx] = value   <-- THE GRANT
//     0x567D19: jmp 0x5664c9
// where eax = &g_flags[idx]. We splice 0x567D17 *before* the write, let a C
// decision function inspect (idx, val), and have it return the address to write
// to. Returning the same address = pass through; returning a *different*
// g_flags cell = swap the granted item; returning a scratch sink = suppress.
//
// This is the in-process, zero-flash content replacement the external client
// can only approximate after the fact. PROOF-OF-CONCEPT mapping for now: swap
// the first-2F-chest Panacea (0x59) for a Roda Fruit (0x57). The real
// per-location map will be fed in from the AP client (socket bridge) later.
//
// No ASLR (image base 0x400000) so the absolute address is valid at runtime.
#include <windows.h>
#include "MinHook.h"

void mod_log(const char* fmt, ...);

static const uintptr_t kGrantStore = 0x00567D17;  // mov [eax], ecx
static const uintptr_t kGFlagsBase = 0x0076B91C;
static void* g_orig = nullptr;

// Scratch sink for "suppress" (a grant redirected here never touches g_flags).
static int g_sink = 0;

// Returns the address the grant should actually write to (eax for the store).
// addr = &g_flags[idx] the VM intended; val = value about to be written.
extern "C" int* __cdecl DecideStore(int* addr, int val) {
    unsigned idx = (unsigned)(((uintptr_t)addr - kGFlagsBase) / 4);
    if (idx >= 0x200)
        return addr;  // script-local, not a g_flags grant — leave alone

    // --- PROOF-OF-CONCEPT replacement map (hardcoded; bridge-fed later) ----- #
    if (idx == 0x59) {  // chest's Celcetan Panacea -> grant a Roda Fruit instead
        mod_log("REPLACE g_flags[0x59]=%d -> grant Roda Fruit (0x57) instead", val);
        return (int*)(kGFlagsBase + 0x57 * 4);
    }
    // ----------------------------------------------------------------------- #

    mod_log("GRANT g_flags[0x%X] = %d", idx, val);
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
