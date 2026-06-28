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
#include <cstring>
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
    if (g_supp_item[idx] && val >= 1) {    // vanilla content of a randomized loc
        // Skill items (bracelets): open a window so the event's skill-equip ops
        // are ALL no-op'd (Hook_Equip12C + the action-fn hooks below) -> the
        // vanilla skill is neither granted nor equipped -> no dangling/null
        // object -> no freeze/crash on LB. The received skill is equipped from
        // the menu later (g_flags=1, consistent), outside this window.
        if (idx == 0x74 || idx == 0x75 || idx == 0x76)
            g_skill_suppress_until = GetTickCount() + 600;
        return &g_sink;                    // suppress (player gets the AP item)
    }

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

// 0x573210 (called from the give-item op 0x116) is the floating give EFFECT, not
// the "Acquired" box (suppressing it leaves the box untouched). We keep it
// suppressed for randomized items so the vanilla floating effect doesn't play.
static int __cdecl popup_decide(int arg1, int arg2, int arg3) {
    int id = arg1;  // confirmed live: a1=0x59 == Panacea
    int supp = (id >= 0 && id < 0x200 && g_supp_item[id]) ? 1 : 0;
    return supp;
}

// --- relabel the native "Acquired <item> x<n>" box (VM sub-op 0xD5) --------- #
//
// The chest script does, in order: give-item op 0x116 (-> 0x573210, suppressed),
// then sets its location CHECK flag (0x64 g_flags[0x12E]=1), THEN the 0xD5 op
// which calls the box fn 0x574410(arg1, arg2=item id, arg3=name string). The box
// content fn 0x5781f0 byte-copies arg3 as the displayed NAME and uses arg2 to
// index the item-ART table. Because the check fires *before* the box, by box time
// the AP client knows the actually-placed item; it stashes the art id + name via
// set_pending_box(), and we overwrite arg2 (art) and arg3 (name string) so the
// box shows the REAL item. This works for foreign items too: pass the foreign
// name + a generic art id (no fake item-data needed).
static const uintptr_t kBoxFn = 0x00574410;
static void* g_orig_box = nullptr;
static volatile int g_box_art_id = -1;          // art id to show (-1 = keep)
static volatile unsigned long g_box_tick = 0;
static char g_box_name[128] = {0};
static volatile int g_box_name_set = 0;
// resolved each call by box_decide(), consumed by the naked stub:
static volatile int g_apply_art = -1;
static volatile uintptr_t g_apply_name = 0;

extern "C" void set_pending_box(int art_id, const char* name) {
    g_box_art_id = art_id;
    if (name && name[0]) {
        strncpy(g_box_name, name, sizeof(g_box_name) - 1);
        g_box_name[sizeof(g_box_name) - 1] = 0;
        g_box_name_set = 1;
    } else {
        g_box_name_set = 0;
    }
    g_box_tick = GetTickCount();
}

static void __cdecl box_decide() {
    g_apply_art = -1;
    g_apply_name = 0;
    if ((GetTickCount() - g_box_tick) >= 1500) return;  // stale -> leave vanilla
    g_apply_art = g_box_art_id;
    if (g_box_name_set) g_apply_name = (uintptr_t)g_box_name;
    mod_log("box: art=%d name='%s'", g_apply_art,
            g_box_name_set ? g_box_name : "(keep)");
}

// Entry: [esp]=ret, [esp+4]=arg1, [esp+8]=arg2 (item id/art), [esp+0xc]=arg3 (name).
__declspec(naked) static void Hook_Box() {
    __asm {
        pushad
        pushfd
        call box_decide
        popfd
        popad
        mov  eax, g_apply_art
        cmp  eax, 0
        jl   skip_art
        mov  dword ptr [esp + 8], eax    // overwrite arg2 (art id)
    skip_art:
        mov  eax, g_apply_name
        test eax, eax
        jz   skip_name
        mov  dword ptr [esp + 0xc], eax  // overwrite arg3 (name string ptr)
    skip_name:
        jmp  dword ptr [g_orig_box]
    }
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

// --- ABORT the whole vanilla skill event (no-op the altar pickup) ----------- #
//
// Chasing the individual skill-equip ops never converged (g_flags -> visual
// objects 0x12C -> equipped slot -> ...), so instead we end the ENTIRE event
// script the moment we know it's a skill pickup, keeping only the early part
// that already ran (crucially the location-check flag at script offset 26).
//
// The event-VM loop (top @0x5663E0) is:
//     edi = [ctx+0x1e4]            ; PC = program counter (script word index)
//     cmp edi, [descriptor+0xc]    ; PC vs script length
//     jae 0x56df7c                 ; PC >= length  -> script ends (natural exit)
// So pushing the PC ([ctx+0x1e4]) past the length makes the VM end the script on
// its next iteration via its OWN completion path — clean, no forced return.
//
// We trigger this at the first skill op, sub-op 0x12C @ script offset 40 (the
// 3 visual spawns), which runs right after the gives+check (offsets 14..26). Its
// handler is @0x56B8F3; we splice at 0x56B983 (after operand fetch, ctx still in
// edi). During the skill-suppress window we abort; otherwise pass through.
// Result: walk up to the altar, press A -> check fires (AP grants the item) ->
// event vanishes. No bubbles, no tutorials, no pickup dialog, no skill equip.
static const uintptr_t kEquip12C = 0x0056B983;
static void* g_orig_12c = nullptr;
static int g_skip12c = 0;

static int __cdecl skill_supp_active() {
    return (GetTickCount() < g_skill_suppress_until) ? 1 : 0;
}

// ctx = VM script context (edi in the loop). End the script + restore the event
// state flag the (now-skipped) tail would have set, so no cutscene lock lingers.
// Standard box position (from the event's own 0xD5 ops: 512.0, 121.5).
static float g_altar_box_pos[2] = {512.0f, 121.5f};

static void __cdecl abort_skill_event(void* ctx) {
    *(volatile unsigned long*)((unsigned char*)ctx + 0x1e4) = 0x7FFFFFFFul;
    *(volatile int*)(kGFlagsBase + 0xB9 * 4) = 1;  // event-state: mark finished
    // NOTE: manually triggering the native box here (0x574410 with this ctx) gets
    // STUCK — the box's dismiss is driven by the script's following dialog ops
    // (0xF2/0xF3), which the abort skips, so it never closes. The altar item
    // still arrives via AP + shows on the overlay. A proper in-event box needs a
    // different abort point (after the event's own Flabellum box) — TODO.
    (void)g_altar_box_pos;
    mod_log("skill event: aborted (PC->end), check already fired");
}

__declspec(naked) static void Hook_Equip12C() {
    __asm {
        pushad
        pushfd
        call skill_supp_active
        mov  g_skip12c, eax        // stash result (popad would clobber eax)
        popfd
        popad
        cmp  dword ptr g_skip12c, 0
        je   normal
        // ctx is [ebp-0x14] (edi was clobbered by the op's operand fetch). End the
        // script, then re-enter the loop tail with edi=ctx so 0x5664C9 sets
        // eax=ctx and edx=descriptor; the bounds check then ends the script.
        push dword ptr [ebp - 0x14]   // ctx
        call abort_skill_event
        add  esp, 4
        mov  edi, dword ptr [ebp - 0x14]   // restore edi = ctx for the loop tail
        mov  ecx, 0x005664C9               // loop tail: sets edx, eax=edi(ctx)
        jmp  ecx
    normal:
        jmp  dword ptr [g_orig_12c]   // relocated bytes -> resume event normally
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
    MH_STATUS cs = MH_CreateHook((void*)kEquip12C, (void*)&Hook_Equip12C, &g_orig_12c);
    MH_STATUS es = MH_EnableHook((void*)kEquip12C);
    MH_CreateHook((void*)kBoxFn, (void*)&Hook_Box, &g_orig_box);
    MH_EnableHook((void*)kBoxFn);
    mod_log("hook_vm_install: grant=%d/%d popup=%d/%d skill-abort=%d/%d (+box)",
            (int)c, (int)e, (int)cg, (int)eg, (int)cs, (int)es);
}
