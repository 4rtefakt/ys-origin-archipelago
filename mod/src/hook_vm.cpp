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

// While GetTickCount() < this, skip skill-object init (suppress a vanilla event's
// auto-equip of a randomized skill). Set when a skill item's grant is suppressed.
static volatile unsigned long g_skill_suppress_until = 0;

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
    // SKILL items (bracelets 0x74/0x75/0x76): do NOT suppress for now. Suppressing
    // them leaves the event's auto-equipped skill object dangling (g_flags=-1) ->
    // freeze/crash. Until the equipped-skill slot is mapped (to un-equip safely),
    // skills stay vanilla (granted+equipped consistently, fully usable). They are
    // not randomized yet; everything else is.
    if (idx == 0x74 || idx == 0x75 || idx == 0x76)
        return addr;

    if (g_supp_item[idx] && val >= 1)    // vanilla content of a randomized loc
        return &g_sink;                  // suppress (player gets the AP item)

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

// --- suppress the native "Acquired X" popup for randomized (suppressed) items #
//
// The chest's VM sub-op 0x116 (give-item) calls the give/popup native function
// 0x573210(this=ecx, arg1, arg2=item id, arg3) — __thiscall, 3 args (ret 0xC).
// (arg2 = the give-item operand0 / item id, from `push edi` at the call site.)
// We can't relabel it (it fires before the box flag, so the location/AP item
// isn't known), but we CAN suppress it for items in the suppress set so the
// game stops claiming "Acquired <vanilla>". The overlay is the source of truth.
static const uintptr_t kGiveItemFn = 0x00573210;
static void* g_orig_give = nullptr;

static int __cdecl popup_decide(int arg1, int arg2, int arg3) {
    // The item id is arg1 (confirmed live: a1=0x59 == Panacea).
    int id = arg1;
    int supp = (id >= 0 && id < 0x200 && g_supp_item[id]) ? 1 : 0;
    mod_log("popup give: id=0x%X suppress=%d", id, supp);
    return supp;
}

// Function-entry hook. Entry stack: [esp]=ret, [+4]=arg1, [+8]=arg2, [+0xc]=arg3;
// ecx=this. Suppress -> return early cleaning 3 args (ret 0xC); else pass through.
__declspec(naked) static void Hook_GiveItemFn() {
    __asm {
        push ecx                       // save this
        push dword ptr [esp + 0x10]    // arg3
        push dword ptr [esp + 0x10]    // arg2
        push dword ptr [esp + 0x10]    // arg1
        call popup_decide
        add  esp, 0xC
        pop  ecx                       // restore this
        test eax, eax
        jnz  do_suppress
        jmp  dword ptr [g_orig_give]   // pass through to the real give/popup fn
    do_suppress:
        xor  eax, eax
        ret  0xC
    }
}

// --- suppress the vanilla event's skill-object init (no dangling -> no freeze) #
// FUN_004448F0(ecx=skill_index) inits a skill object/state at 0x76e438+idx*16 and
// returns &that. During the suppress window we skip the init and just return the
// state pointer (caller stores it, but with no dangling heap object behind it).
static const uintptr_t kSkillInitFn = 0x004448F0;
static void* g_orig_skillinit = nullptr;

static int __cdecl skill_init_skip() {
    return (GetTickCount() < g_skill_suppress_until) ? 1 : 0;
}

__declspec(naked) static void Hook_SkillInit() {
    __asm {
        push ecx                          // save skill_index (thiscall this)
        call skill_init_skip
        pop  ecx
        test eax, eax
        jnz  do_skip
        jmp  dword ptr [g_orig_skillinit] // run the real init
    do_skip:
        mov  eax, ecx                     // return &skill_state[skill_index]
        shl  eax, 4                       //   = 0x76e438 + skill_index*16
        add  eax, 0x76E438
        ret
    }
}

void hook_vm_install() {
    mod_log("hook_vm_install: begin (grant store @0x%X, override)", (unsigned)kGrantStore);
    MH_Initialize();  // may already be initialized by the D3D9 hook (returns 9)
    MH_STATUS c = MH_CreateHook((void*)kGrantStore, (void*)&Hook_Grant, &g_orig);
    MH_STATUS e = MH_EnableHook((void*)kGrantStore);
    MH_STATUS cg = MH_CreateHook((void*)kGiveItemFn, (void*)&Hook_GiveItemFn,
                                 &g_orig_give);
    MH_STATUS eg = MH_EnableHook((void*)kGiveItemFn);
    // NOTE: skill-init suppression hook (kSkillInitFn / Hook_SkillInit) is NOT
    // installed — skipping FUN_004448F0 crashes (the caller needs the object it
    // allocates). Kept for reference; skills are left vanilla for now.
    (void)&Hook_SkillInit; (void)kSkillInitFn; (void)&skill_init_skip;
    mod_log("hook_vm_install: grant=%d/%d popup=%d/%d", (int)c, (int)e, (int)cg, (int)eg);
}
