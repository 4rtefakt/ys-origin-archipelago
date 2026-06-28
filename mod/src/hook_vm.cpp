// Detect-only hook on the REAL runtime grant store.
//
// The game ships two copies of the event-script interpreter. Static tools (and
// the earlier Ghidra pass) decompiled the copy at FUN_004472e0 (~0x447E), but a
// hardware-watchpoint trace proved the copy that actually runs at runtime is the
// one at ~0x567xxx. Its sub-op 0x64 (set) handler resolves &g_flags[idx] into
// eax via a helper (0x5659e0, fed base 0x76b91c + index) and stores the value:
//
//     0x567D15: mov ecx, [esi]      ; ecx = value
//     0x567D17: mov [eax], ecx      ; g_flags[idx] = value   <-- THE GRANT
//     0x567D19: jmp 0x5664c9        ; (data bp traps here, after the write)
//
// Chests grant their item AND set their box-open flag through this single store
// (the watchpoint caught both g_flags[0x59]=1 and g_flags[0x12E]=1 here). So one
// hook observes every grant. We hook 0x567D19 (a clean 5-byte jmp, post-store):
// eax still = &g_flags[idx], ecx still = value. Detect-only — log, then resume
// through MinHook's trampoline. (Override later: hook 0x567D17 instead and
// rewrite ecx/eax before the write.)
//
// No ASLR (image base 0x400000) so this absolute address is valid at runtime.
#include <windows.h>
#include "MinHook.h"

void mod_log(const char* fmt, ...);

static const uintptr_t kGrant = 0x00567D19;  // just after `mov [eax], ecx`
static void* g_orig_grant = nullptr;

static void __cdecl OnGrant(int idx, int val) {
    if ((unsigned)idx < 0x200)
        mod_log("GRANT g_flags[0x%X] = %d", idx, val);
}

// At 0x567D19: eax = &g_flags[idx], ecx = value (both intact after the store).
__declspec(naked) static void Hook_Grant() {
    __asm {
        pushad
        pushfd
        mov  edx, eax            // edx = &g_flags[idx]
        sub  edx, 0x76B91C
        sar  edx, 2              // edx = idx
        push ecx                 // val
        push edx                 // idx
        call OnGrant
        add  esp, 8
        popfd
        popad
        jmp  dword ptr [g_orig_grant]   // trampoline: original jmp 0x5664c9
    }
}

void hook_vm_install() {
    mod_log("hook_vm_install: begin (grant store @0x%X)", (unsigned)kGrant);
    MH_Initialize();  // may already be initialized by the D3D9 hook (returns 9)
    MH_STATUS c = MH_CreateHook((void*)kGrant, (void*)&Hook_Grant, &g_orig_grant);
    MH_STATUS e = MH_EnableHook((void*)kGrant);
    mod_log("hook_vm_install: create=%d enable=%d (0=OK)", (int)c, (int)e);
}
