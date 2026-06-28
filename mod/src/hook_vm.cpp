// Mid-function hook on the event-script VM's SET grant (sub-op 0x64).
//
// The VM (FUN_004472e0) executes `g_flags[op0] = op1` for sub-op 0x64 with this
// instruction at 0x004485B7:
//
//     0x4485A4: mov eax, [ebx]              ; op0 = index   (ebx = operand ptr)
//     0x4485AD: lea edx, [eax*4 + 0x76b91c] ; edx = &g_flags[idx]
//     0x4485B4: mov eax, [ebx + 4]          ; op1 = value
//     0x4485B7: mov [edx], eax              ; g_flags[idx] = val   <-- HOOK HERE
//     0x4485B9: mov eax, [ebp - 0x10]       ; (relocated with the store)
//     0x4485BC: jmp 0x4473e6                ; back to the dispatch loop
//
// This is how a chest grants its item (g_flags[item_id] = 1) and sets its
// box-open flag (g_flags[box_idx] = 1), how events/altars set their flags, etc.
// All item grants and location flags funnel through this single store, so one
// hook observes the entire grant stream — with no client-side polling, no
// flag-collision guessing, and (later) the ability to rewrite the value/index
// *before* the write to swap a vanilla item for an Archipelago one.
//
// Detect-only for now: log idx/val, then run the original store via MinHook's
// trampoline. The game's behaviour is unchanged.
//
// Addresses are absolute: the exe has no ASLR (image base 0x400000), so the
// runtime code lives at exactly these addresses.
#include <windows.h>
#include "MinHook.h"

void mod_log(const char* fmt, ...);

static const uintptr_t kSetStore = 0x004485B7;  // the `mov [edx], eax` grant

static void* g_orig_set = nullptr;  // MinHook trampoline (relocated store + resume)

// Light C handler. idx is recovered from edx (= &g_flags[idx]); val is eax.
// Only g_flags writes (idx < 0x200) reach this store — script-locals use a
// different store site — so no extra range filtering is needed.
static void __cdecl OnSetGrant(int idx, int val) {
    mod_log("VM SET g_flags[0x%X] = %d", idx, val);
}

// Naked detour spliced in by MinHook at kSetStore. Entered via JMP with live VM
// registers: edx = &g_flags[idx], eax = val, ebx = operand ptr. Save everything,
// log, restore, then jump to the trampoline (which runs the real store + resumes).
__declspec(naked) static void Hook_SetGrant() {
    __asm {
        pushad
        pushfd
        mov  ecx, edx            // ecx = &g_flags[idx]   (edx intact after pushad)
        sub  ecx, 0x76B91C       // ecx = idx*4
        sar  ecx, 2              // ecx = idx
        push eax                 // arg2: val             (eax intact after pushad)
        push ecx                 // arg1: idx
        call OnSetGrant          // __cdecl
        add  esp, 8
        popfd
        popad
        jmp  dword ptr [g_orig_set]   // original store + jmp back to 0x4485BC
    }
}

void hook_vm_install() {
    mod_log("hook_vm_install: begin (target 0x%X)", (unsigned)kSetStore);
    // MH_Initialize may already have run in the D3D9 hook; a second call returns
    // MH_ERROR_ALREADY_INITIALIZED (9), which is fine.
    MH_STATUS init = MH_Initialize();
    MH_STATUS create = MH_CreateHook((void*)kSetStore, (void*)&Hook_SetGrant,
                                     &g_orig_set);
    MH_STATUS enable = MH_EnableHook((void*)kSetStore);
    mod_log("hook_vm_install: init=%d create=%d enable=%d (0=OK, 9=already-init)",
            (int)init, (int)create, (int)enable);
}
