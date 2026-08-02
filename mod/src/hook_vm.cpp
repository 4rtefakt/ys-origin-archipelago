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
extern bool g_statue_lock[0x200];// locked statue activation flags (suppress purify)

// The two VM instructions that can write a g_flags item cell.
//
//   0x64 Flag_SetInt  handler @0x567CDC:  ... call FUN_005659e0 (&g_flags[idx])
//                                          mov  [eax], ecx        <- kGrantStore
//   0x67 Flag_AddInt  handler @0x567D78:  ... call FUN_005659e0 (&g_flags[idx])
//                                          add  [esi], ecx        <- kGrantAdd
//
// (Both verified by raw disassembly of yso_win.exe; CleriaCore
// EVENTVM_HANDLERS_2 §1 documents the shared slot accessor FUN_005659e0.)
//
// Only the 0x64 store used to be hooked, which left `+=` grants completely
// invisible: the player got the AP item AND the vanilla one. That is not
// hypothetical — disassembling all 2225 scripts of the XSO corpus finds 14
// chests that grant with 0x67, and one of them is progression:
//   S_COMMON\BOXCRERIA.XSO         -> 0x58 Cleria Ore     (weapon-upgrade tier!)
//   6x S_*/S_BOX*.XSO              -> 0x57 Roda Fruit
//   7x S_*/S_BOX*.XSO              -> 0x59 Celcetan Panacea
// Their box/location flags are set with 0x64, so DETECTION always worked and
// only the grant leaked — which is exactly how the bug presented in the wild.
static const uintptr_t kGrantStore = 0x00567D17;  // mov [eax], ecx  (0x64)
static const uintptr_t kGrantAdd   = 0x00567DB3;  // add [esi], ecx  (0x67)
static const uintptr_t kGFlagsBase = 0x0076B91C;
static void* g_orig = nullptr;
static void* g_orig_add = nullptr;

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
    if (g_statue_lock[idx] && val >= 1) {  // locked goddess statue being purified
        // Suppress the activation write -> the statue stays wrapped in darkness
        // (no warp/heal/save) until its unlock item arrives. The statue CHECK is
        // detected by scene-method when locks are on, so nothing is lost here.
        mod_log("statue: suppressed purification of g_flags[0x%X] (locked)", idx);
        g_sink = 0;    // the 0x67 add-form redirects here too; don't accumulate
        return &g_sink;
    }
    if (g_supp_item[idx] && val >= 1) {    // vanilla content of a randomized loc
        // Skill items (bracelets): open a window so the event's skill-equip ops
        // are ALL no-op'd (Hook_Equip12C + the action-fn hooks below) -> the
        // vanilla skill is neither granted nor equipped -> no dangling/null
        // object -> no freeze/crash on LB. The received skill is equipped from
        // the menu later (g_flags=1, consistent), outside this window.
        if (idx == 0x74 || idx == 0x75 || idx == 0x76)
            g_skill_suppress_until = GetTickCount() + 600;
        g_sink = 0;    // the 0x67 add-form redirects here too; don't accumulate
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

// Same decision as DecideStore, for the `add [esi], ecx` form of the grant.
// Returns 1 when the increment must not happen. Delegating keeps ONE copy of the
// suppress/location/statue policy — DecideStore already reports a check, emits
// the bridge line, and returns the sink for anything it wants stopped, so "did
// it redirect?" is exactly "should this add be dropped?".
extern "C" int __cdecl DecideAdd(int* addr, int val) {
    return DecideStore(addr, val) != addr ? 1 : 0;
}

// Set by the stub below on the game's main thread (the only thread that runs the
// event VM), consumed two instructions later — same single-threaded pattern as
// the g_apply_art box hook.
static int g_add_suppress = 0;

// Naked splice at 0x567DB3 (pre-store). esi = &g_flags[idx], ecx = addend.
//
// A suppressed add zeroes ECX rather than redirecting ESI to the sink: ESI is
// the handler's live slot pointer and the dispatch tail is reached by a JMP
// straight after this instruction, so leaving ESI exactly as the game set it is
// the change with no reachable side effects. `add [esi], 0` writes the cell's
// own value back — a true no-op. Flags are clobbered by the cmp/xor, which is
// safe because the very next instruction (the relocated ADD) redefines them.
__declspec(naked) static void Hook_GrantAdd() {
    __asm {
        pushfd
        pushad
        push ecx                     // arg2: val (the addend)
        push esi                     // arg1: addr
        call DecideAdd
        add  esp, 8
        mov  g_add_suppress, eax
        popad
        popfd
        cmp  dword ptr [g_add_suppress], 0
        je   pass
        xor  ecx, ecx                // suppressed -> add [esi], 0
    pass:
        jmp  dword ptr [g_orig_add]  // trampoline: add [esi],ecx ; jmp 0x5664c9
    }
}

// --- vanilla statue blessing shop: charge the SEED's price ------------------ #
//
// Each blessing is one S_COMMON/GROWnn.XSO, and its price is a baked immediate
// used at THREE places (CleriaCore build/menu_re/BLESSING_SHOP.md):
//
//   0x0056A184  push [eax]        the 0xdd Menu_AddSpShop entry -> what you SEE
//   0x00567C43  call FUN_005659e0 the 0x61 affordability compare -> MAY you buy
//   0x00567E45  sub [esi],ecx     the 0x69 deduction             -> what you PAY
//
// Only the F5 overlay shop used the seed's prices, so the goddess statue kept
// selling at vanilla ones: "SP cost reduction in shop doesn't seem to work" and
// "progression balancing does not seem to affect Vanilla SP". All three sites
// have to move together — re-pricing the display alone makes the menu lie, and
// re-pricing the deduction alone still forces you to AFFORD the vanilla price
// (fatal for the 500,000 SP entry).
//
// The lookup is keyed on the VANILLA price because that is the only thing these
// sites know: the blessing's 0xAF index does not appear in the script until
// after the money has moved. The world pins the three colliding vanilla prices
// (30000, 8000, 20000, each shared by two blessings) to a single randomized
// price so the key stays unambiguous.
//
// The armor/leggings upgrades are deliberately NOT caught by any of this, and
// not by accident: GROWAR/GROWLE compare with `0x62` (flag vs flag, SP against
// the scaling cost cell g_flags[0xDA] that `0xb0 SetRavalCostToFlag` fills) and
// never use `0x61`/`0x69` at all. So their ladder — 100/300/1000/3000/6000/12000
// — keeps running vanilla even though two of those rungs collide with real
// blessing prices, which a price-keyed hook would otherwise have re-priced.
extern "C" int ap_substitute_bless_price(int vanilla);   // hook_ap.cpp
extern "C" void ap_bless_relabel(char* buf, int vanilla);   // hook_ap.cpp
extern "C" int  ap_on_blessing_purchase(int index);        // hook_ap.cpp
extern "C" int  ap_bless_compare_price(int vanilla);       // hook_ap.cpp
extern "C" int  ap_bless_hide_price(int vanilla);          // hook_ap.cpp
extern "C" void ap_note_gear_kind(int kind);               // hook_ap.cpp

static const uintptr_t kBlessCmp  = 0x00567C43;  // call FUN_005659e0 (0x61)
static const uintptr_t kBlessSub  = 0x00567E45;  // sub [esi], ecx    (0x69)
static const uintptr_t kBlessMenu = 0x0056A184;  // push [eax]        (0xdd)
// The 0xAF grant dispatch. EAX already holds the blessing INDEX here, and every
// case jumps to the shared tail 0x5664C9, so this one splice both detects the
// purchase and can skip the grant for all 26 blessings.
//   00568D5B  mov  eax,[eax]        ; EAX = blessing index
//   00568D5D  cmp  eax,0x21         <- spliced (3 bytes) + ja rel32 (6) = 9
//   00568D66  jmp  [eax*4+0x56E6F0]
// Right after the price sprintf, before it is concatenated onto the label:
//   0056A192  call FUN_0040a460        ; sprintf(priceBuf, "%d", price)
//   0056A197  lea  edx,[ebp-0x2CC]     <- spliced (6 bytes)
// Blanking priceBuf here makes the concat append nothing, which is what lets a
// bought row read "- [Done]" with no number after it.
// 0xb0 SetRavalCostToFlag: op0 is 0 (armor) or 1 (leggings), and the script runs
// it immediately before adding that gear row to the menu. Capturing it is what
// lets the relabel identify a row that carries no vanilla price.
//   0056920E  cmp dword ptr [eax],0x0   <- spliced (3 bytes) + jnz rel32 (6)
static const uintptr_t kRavalCost = 0x0056920E;
static void* g_orig_ravalcost = nullptr;

static const uintptr_t kBlessPriceStr = 0x0056A197;
static void* g_orig_blesspricestr = nullptr;

static const uintptr_t kBlessGrant = 0x00568D5D;
static const uintptr_t kVmTail     = 0x005664C9;  // where every 0xAF case lands
static void* g_orig_blessgrant = nullptr;
static int g_bless_skip = 0;
static const uintptr_t kSpShadow  = kGFlagsBase + 0xD8 * 4;  // 0x76BC7C
static void* g_orig_blesscmp  = nullptr;
static void* g_orig_blesssub  = nullptr;
static void* g_orig_blessmenu = nullptr;

// Scratch the menu hook points the push at. Main-thread only (the event VM), so
// a single slot is enough — same assumption as the box-relabel hook above.
static int g_bless_menu_price = 0;
static int g_bless_vanilla = 0;

// 0x61 affordability. Spliced ON the operand-accessor call, where the flag index
// and base are already pushed: [esp] = 0x76b91c, [esp+4] = index. EDI holds op2,
// the price. Overwriting EDI is safe — this handler already clobbered it at
// 0x567C0E and the dispatcher re-establishes it after the tail jump.
__declspec(naked) static void Hook_BlessCmp() {
    __asm {
        pushfd
        pushad                          // esp -= 32; orig [esp+4] is now [esp+40]
        mov  eax, [esp + 40]            // the flag index being compared
        cmp  eax, 0xD8                  // the SP (zenny shadow) cell?
        jne  bc_done
        push edi                        // arg: the vanilla price
        call ap_bless_compare_price     // unaffordable if already bought
        add  esp, 4
        mov  [esp], eax                 // pushad slot 0 = saved EDI
    bc_done:
        popad
        popfd
        jmp  dword ptr [g_orig_blesscmp]
    }
}

// 0x69 deduction: esi = &g_flags[idx], ecx = amount to subtract.
__declspec(naked) static void Hook_BlessSub() {
    __asm {
        pushfd
        pushad
        cmp  esi, kSpShadow
        jne  bs_done
        push ecx                        // arg: the vanilla price
        call ap_substitute_bless_price
        add  esp, 4
        mov  [esp + 24], eax            // pushad slot 6 = saved ECX
    bs_done:
        popad
        popfd
        jmp  dword ptr [g_orig_blesssub]
    }
}

// 0xdd menu entry: the next instruction pushes [eax] into the price sprintf.
// Point eax at our scratch instead of writing through it — [eax] is the script's
// own operand table and a write there would persist for the rest of the run.
// Clobbering eax is free: the very next instruction (0x56A186) reloads it.
// Also RELABELS the row. By this instruction the script's own label has already
// been copied into the handler's buffer at [ebp-0x3F8] (the strcpy loop at
// 0x56A162) and the formatted price has not been appended yet, so overwriting
// the buffer here swaps the name and still gets " - [SP:]nnn" added after it.
// EBP is the handler's frame and untouched by pushfd/pushad, so it is valid.
__declspec(naked) static void Hook_BlessMenu() {
    __asm {
        pushfd
        pushad
        mov  eax, [eax]                 // the vanilla price operand
        mov  g_bless_vanilla, eax       // keep it: it keys BOTH substitutions
        push eax
        call ap_substitute_bless_price
        add  esp, 4
        mov  g_bless_menu_price, eax
        lea  eax, [ebp - 0x3F8]         // the label buffer the script filled
        push g_bless_vanilla
        push eax
        call ap_bless_relabel           // no-op for rows we don't recognise
        add  esp, 8
        popad
        popfd
        lea  eax, g_bless_menu_price    // push [eax] now reads our price
        jmp  dword ptr [g_orig_blessmenu]
    }
}

__declspec(naked) static void Hook_RavalCost() {
    __asm {
        pushfd
        pushad
        mov  eax, [eax]                 // op0: 0 = armor, 1 = leggings
        push eax
        call ap_note_gear_kind
        add  esp, 4
        popad
        popfd
        jmp  dword ptr [g_orig_ravalcost]
    }
}

__declspec(naked) static void Hook_BlessPriceStr() {
    __asm {
        pushfd
        pushad
        push g_bless_vanilla            // same row the menu hook just handled
        call ap_bless_hide_price
        add  esp, 4
        test eax, eax
        jz   bp_done
        mov  byte ptr [ebp - 0x2CC], 0  // empty the formatted price string
    bp_done:
        popad
        popfd
        jmp  dword ptr [g_orig_blesspricestr]
    }
}

// 0xAF blessing grant. Reports the purchase as a check, and when the seed shuffles
// blessing EFFECTS into the item pool, skips the vanilla grant entirely by jumping
// to the dispatch tail instead of the jump table.
__declspec(naked) static void Hook_BlessGrant() {
    __asm {
        pushfd
        pushad
        push eax                        // the blessing index
        call ap_on_blessing_purchase
        add  esp, 4
        mov  g_bless_skip, eax
        popad
        popfd
        cmp  dword ptr [g_bless_skip], 0
        jne  bg_skip
        jmp  dword ptr [g_orig_blessgrant]   // trampoline: cmp/ja, then the table
    bg_skip:
        mov  eax, kVmTail
        jmp  eax                        // straight to the shared tail: no grant
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

// --- Catch-up EXP multiplier ----------------------------------------------- #
//
// The EXP-award (FUN_004fa4a0) computes earned EXP in xmm1 (boost[0x76a5fc] *
// base_exp[enemy+0x1c] * difficulty, floored to a min), then:
//     0x4FA525  addss xmm1, [0x76A748]   ; earned + current EXP
//     0x4FA52D  movss [0x76A748], xmm1   ; store EXP
//     0x4FA535  call 0x420C40            ; process level-ups
// We splice 0x4FA525 to multiply the earned EXP (xmm1) by g_exp_factor BEFORE the
// add: 1.0 = byte-for-byte vanilla (keeps the game's own up-to-1.99 boost), >1 =
// catch-up when you're under-leveled for the floor (set by the AP poll loop). The
// game's own level-up processor runs right after, so leveling cascades safely.
float g_exp_factor = 1.0f;
static const uintptr_t kExpAdd = 0x004FA525;
static void* g_orig_expadd = nullptr;

__declspec(naked) static void Hook_ExpAdd() {
    __asm {
        mulss xmm1, dword ptr [g_exp_factor]   // earned *= factor
        jmp   dword ptr [g_orig_expadd]        // addss xmm1,[0x76a748] ; -> 0x4FA52D
    }
}

// SECOND EXP-add path: enemy kills route earned EXP through a different branch
// (maxss vs the difficulty floor, then addss) that ALSO stores to 0x76A748 and
// runs the level-up recompute. 0x4FA525 alone never touched kill EXP, so the
// multiplier felt like a no-op; splice this identically so it reaches kills.
//     0x5226C7  addss xmm1, [0x76A748]   ; earned + current EXP
//     0x5226CF  movss [0x76A748], xmm1   ; store
//     0x5226D7  call 0x420C40            ; level-ups
static const uintptr_t kExpAdd2 = 0x005226C7;
static void* g_orig_expadd2 = nullptr;
__declspec(naked) static void Hook_ExpAdd2() {
    __asm {
        mulss xmm1, dword ptr [g_exp_factor]   // earned *= factor
        jmp   dword ptr [g_orig_expadd2]       // addss xmm1,[0x76a748] ; -> 0x5226CF
    }
}

// THIRD EXP-add path: yet another award branch (reads EXP into xmm0, builds the
// earned amount in xmm1 via const/gauge math, adds it). This is the one regular
// enemy kills actually use. xmm1 is the final earned at 0x4FCC07, so multiply it
// there before the add. (MinHook relocates the addss + the following store.)
//     0x4FCC07  addss xmm0, xmm1        ; EXP + earned
//     0x4FCC0B  movss [0x76A748], xmm0  ; store
static const uintptr_t kExpAdd3 = 0x004FCC07;
static void* g_orig_expadd3 = nullptr;
__declspec(naked) static void Hook_ExpAdd3() {
    __asm {
        mulss xmm1, dword ptr [g_exp_factor]   // earned *= factor
        jmp   dword ptr [g_orig_expadd3]       // addss xmm0,xmm1 ; -> 0x4FCC0B store
    }
}

// --- Cutscene fast-forward (hold a key to blow through cutscenes) ---------- #
//
// The event VM drives cutscenes; the thing that makes them slow is the 0xF2
// frame-WAIT op (handler 0x56DE74): a countdown at ctx+0x1e8 that yields back to
// the frame loop (jns -> 0x56DF91) until it goes negative, then advances. We zero
// the countdown so each wait elapses in a single frame -> camera pans, pauses and
// animation holds collapse, and a cutscene that's mostly timed waits blows past.
//
// Gated on a HELD key (g_cutscene_ff), NOT always-on, on purpose: dialog ADVANCE
// is a separate op (0xF3 -> 0x5741B0) shared with interactive event dialog
// (shops, NPC choices, save prompts), so auto-dismissing it globally would break
// menus. Holding the key is an explicit "I'm skipping" — the player taps the
// game's own confirm to advance text while waits are skipped. (A New-Game-scoped
// auto-skip + dialog auto-advance is the follow-up, alongside the force-spawn.)
volatile bool g_cutscene_ff = false;          // set from the poll loop (key state)

static const uintptr_t kWaitOp   = 0x0056DE74;  // 0xF2 frame-wait handler entry
static const uintptr_t kWaitDone = 0x0056DE98;  // its "wait elapsed" path (zeros
                                                // ctx+0x1e8, runs the clean epilogue)
static void* g_orig_wait = nullptr;

// Entry: edi = VM ctx (set by the dispatch). When fast-forwarding, jump into the
// handler's own "wait elapsed" path with ecx=ctx so the op completes this frame
// (the PC was already advanced by the dispatch, so the next op runs next frame).
__declspec(naked) static void Hook_Wait() {
    __asm {
        cmp  byte ptr [g_cutscene_ff], 0
        je   passthrough
        mov  ecx, edi                  // ecx = ctx (kWaitDone does mov eax,ecx ...)
        mov  eax, kWaitDone            // 0x56DE98: [ctx+0x1e8]=0 ; epilogue ; ret
        jmp  eax
    passthrough:
        jmp  dword ptr [g_orig_wait]   // trampoline -> original 0xF2 handler
    }
}

// Cutscenes spend most of their time in the "wait for a subsystem to finish" ops
// (actor move 0x5742B0, camera/effect 0x574310/0x574330, dialog-advance 0x5741B0,
// ...). They all share the tail @0x56A4AC: a check fn returns al = "done?"; al!=0
// -> 0x56DB1D (op completes), al==0 -> re-run the op (keep waiting). Forcing al
// nonzero while fast-forwarding makes every such wait complete this frame, so
// camera pans / character moves / fades all blow past (the 0xF2 hook only covered
// pure frame-counter waits = dialog pacing). The done path (0x56DB1D) just
// continues the op loop, so this is a clean "treat the wait as finished".
static const uintptr_t kWaitTail = 0x0056A4AC;   // test al,al ; jne 0x56DB1D ...
static void* g_orig_waittail = nullptr;

// Scene-load wait (op handler 0x574310) reports "done" only when both streaming
// managers *[0x730194] and *[0x730170] are idle. Forcing the shared tail "done"
// WHILE a load is pending would let the script charge past the load -> crash. So
// the tail FF self-guards: if either manager is busy, fall through and wait
// normally (only non-load waits get collapsed).
__declspec(naked) static void Hook_WaitTail() {
    __asm {
        cmp  byte ptr [g_cutscene_ff], 0
        je   passthrough
        push eax                          // preserve the check fn's al
        mov  eax, dword ptr [0x730194]
        cmp  dword ptr [eax], 0
        jne  loading
        mov  eax, dword ptr [0x730170]
        cmp  dword ptr [eax], 0
        jne  loading
        pop  eax                          // restore al
        mov  al, 1                        // not loading -> force the wait done
        jmp  dword ptr [g_orig_waittail]
    loading:
        pop  eax                          // restore al -> wait normally for the load
    passthrough:
        jmp  dword ptr [g_orig_waittail]  // test al,al ; jne 0x56DB1D ; ...
    }
}

// Decide whether to fast-forward this tick (called each tick from the AP poll
// loop). AUTO during the New-Game intro: the intro plays as scene 2 with scene-0
// interludes, so we arm a window when scene 2 appears and disarm once a real room
// (>=1000) loads. Holding Right Ctrl is an extra manual override for any other
// cutscene. The load-guard above keeps even an always-armed window crash-safe.
extern "C" void request_force_spawn();           // hook_ap.cpp (test hotkey)
static const uintptr_t kCurScene = 0x0076C100;   // g_flags[0x1F9]

// Player-facing cutscene-skip mode, set from the F8 Archipelago menu and
// persisted in yso_ap.cfg. Three players asked for a skip in the release thread
// and the one constraint everyone agreed on was "as long as it's optional", so
// it is a mode rather than an always-on:
//   0 = off      — never fast-forward (the New-Game intro still does, see below)
//   1 = hold     — fast-forward while Right Ctrl is held (the old behaviour)
//   2 = auto     — fast-forward every cutscene wait, no key needed
// The New-Game intro window is NOT part of this: it is load-bearing for the
// random-start force-spawn warp, so it fast-forwards in every mode.
extern "C" int g_cutscene_skip_mode = 1;

extern "C" void cutscene_ff_poll() {
    static bool intro = false;
    int scene = *(volatile int*)kCurScene;
    if (scene == 2) intro = true;          // New-Game intro cutscene seen
    else if (scene >= 1000) intro = false; // reached a real room -> stop
    bool key = g_cutscene_skip_mode == 1 &&
               (GetAsyncKeyState(VK_RCONTROL) & 0x8000) != 0;
    g_cutscene_ff = intro || key || g_cutscene_skip_mode == 2;

    // F9 (edge-triggered) = manually force-spawn, for testing the warp without
    // replaying the intro.
    static bool f9_prev = false;
    bool f9 = (GetAsyncKeyState(VK_F9) & 0x8000) != 0;
    if (f9 && !f9_prev) request_force_spawn();
    f9_prev = f9;
}

// --- Intro-movie skip (shippable, no file renames) ------------------------- #
//
// The opening AVIs (release/yso_logo.avi, yso_op.avi, yso_pro.avi + the
// yso_ins01-03.dat inserts) play before any player entity exists, so the
// force-spawn warp can't skip them. The engine, however, skips a MISSING movie
// gracefully (proven). So we hook CreateFileW/A and report these specific files
// as not-found — the movies vanish, every other file opens normally. Endings
// (yso_ed*) and yso_ins04 (not a movie) are left alone.
typedef HANDLE (WINAPI* CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef HANDLE (WINAPI* CreateFileA_t)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
static CreateFileW_t g_orig_cfw = nullptr;
static CreateFileA_t g_orig_cfa = nullptr;

// The exact set the game can open, read out of the movie path tables at
// 0x6a05b0..0x6a0650 (one table per language, 8 entries each, indexed by the
// media object's +0x294):
//
//   0  release\<lang>_pro.avi   prologue — en_/fr_/de_/sp_/it_ prefixed!
//   1  release\yso_op.avi       opening
//   2  release\yso_ins01.dat    insert movies
//   3  release\yso_ins02.dat
//   4  release\yso_ins03.dat
//   5  release\yso_logo.avi     Falcom logo
//   6  release\yso_ed01.dat     ENDING — never block (goal detection)
//   7  release\yso_ed02.dat     ENDING — never block
//
// The prologue was missed: the filter looked for "yso_pro", but the game asks
// for "en_pro.avi" (or the local-language equivalent), which never matches.
// yso_ins04 is deliberately absent — it is font data, not a movie (8 MB, with a
// .fot sibling), and blocking it would break text.
static bool is_intro_movie(const char* low) {
    return strstr(low, "yso_logo") || strstr(low, "yso_op")
        || strstr(low, "yso_ins01") || strstr(low, "yso_ins02")
        || strstr(low, "yso_ins03")
        || strstr(low, "_pro.avi");     // en_/fr_/de_/sp_/it_/yso_ prologue
}
static bool blocked_a(const char* p) {
    if (!p) return false;
    char low[520]; int i = 0;
    for (; p[i] && i < (int)sizeof(low) - 1; i++) low[i] = (char)tolower((unsigned char)p[i]);
    low[i] = 0;
    return is_intro_movie(low);
}
static bool blocked_w(const wchar_t* p) {
    if (!p) return false;
    char low[520]; int i = 0;
    for (; p[i] && i < (int)sizeof(low) - 1; i++) low[i] = (char)towlower(p[i]);
    low[i] = 0;
    return is_intro_movie(low);
}
static HANDLE WINAPI Hook_CreateFileW(LPCWSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                      DWORD c, DWORD f, HANDLE t) {
    if (blocked_w(n)) {
        mod_log("movie: blocked (W) — reported not-found");
        SetLastError(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE_VALUE;
    }
    return g_orig_cfw(n, a, s, sa, c, f, t);
}
// KERNELBASE's CreateFileW needs its own trampoline: it is a DIFFERENT function
// from kernel32's forwarder, so it cannot share g_orig_cfw.
static CreateFileW_t g_orig_cfw_kb = nullptr;
static HANDLE WINAPI Hook_CreateFileW_KB(LPCWSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                         DWORD c, DWORD f, HANDLE t) {
    if (blocked_w(n)) {
        mod_log("movie: blocked (KERNELBASE W) — reported not-found");
        SetLastError(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE_VALUE;
    }
    return g_orig_cfw_kb(n, a, s, sa, c, f, t);
}

static HANDLE WINAPI Hook_CreateFileA(LPCSTR n, DWORD a, DWORD s, LPSECURITY_ATTRIBUTES sa,
                                      DWORD c, DWORD f, HANDLE t) {
    if (blocked_a(n)) {
        mod_log("movie: blocked (A) '%s' — reported not-found", n);
        SetLastError(ERROR_FILE_NOT_FOUND); return INVALID_HANDLE_VALUE;
    }
    return g_orig_cfa(n, a, s, sa, c, f, t);
}

void hook_vm_install() {
    mod_log("hook_vm_install: begin (grant store @0x%X, override)", (unsigned)kGrantStore);
    MH_Initialize();  // may already be initialized by the D3D9 hook (returns 9)
    MH_STATUS c = MH_CreateHook((void*)kGrantStore, (void*)&Hook_Grant, &g_orig);
    MH_STATUS e = MH_EnableHook((void*)kGrantStore);
    // The `+=` form of the same grant (0x67 Flag_AddInt) — see kGrantAdd.
    MH_STATUS ca = MH_CreateHook((void*)kGrantAdd, (void*)&Hook_GrantAdd, &g_orig_add);
    MH_STATUS ea = MH_EnableHook((void*)kGrantAdd);
    mod_log("hook_vm_install: 0x67 add-store @0x%X create=%d enable=%d",
            (unsigned)kGrantAdd, (int)ca, (int)ea);
    // Vanilla statue blessing shop: display / affordability / deduction.
    MH_STATUS cbc = MH_CreateHook((void*)kBlessCmp,  (void*)&Hook_BlessCmp,  &g_orig_blesscmp);
    MH_STATUS ebc = MH_EnableHook((void*)kBlessCmp);
    MH_STATUS cbs = MH_CreateHook((void*)kBlessSub,  (void*)&Hook_BlessSub,  &g_orig_blesssub);
    MH_STATUS ebs = MH_EnableHook((void*)kBlessSub);
    MH_STATUS cbm = MH_CreateHook((void*)kBlessMenu, (void*)&Hook_BlessMenu, &g_orig_blessmenu);
    MH_STATUS ebm = MH_EnableHook((void*)kBlessMenu);
    mod_log("hook_vm_install: statue shop cmp=%d/%d sub=%d/%d menu=%d/%d",
            (int)cbc, (int)ebc, (int)cbs, (int)ebs, (int)cbm, (int)ebm);
    MH_CreateHook((void*)kRavalCost, (void*)&Hook_RavalCost, &g_orig_ravalcost);
    MH_EnableHook((void*)kRavalCost);
    MH_CreateHook((void*)kBlessPriceStr, (void*)&Hook_BlessPriceStr,
                  &g_orig_blesspricestr);
    MH_EnableHook((void*)kBlessPriceStr);
    MH_STATUS cbg = MH_CreateHook((void*)kBlessGrant, (void*)&Hook_BlessGrant,
                                  &g_orig_blessgrant);
    MH_STATUS ebg = MH_EnableHook((void*)kBlessGrant);
    mod_log("hook_vm_install: 0xAF blessing grant @0x%X create=%d enable=%d",
            (unsigned)kBlessGrant, (int)cbg, (int)ebg);
    MH_STATUS cg = MH_CreateHook((void*)kGiveItemFn, (void*)&Hook_GiveItemFn,
                                 &g_orig_give);
    MH_STATUS eg = MH_EnableHook((void*)kGiveItemFn);
    MH_STATUS cs = MH_CreateHook((void*)kEquip12C, (void*)&Hook_Equip12C, &g_orig_12c);
    MH_STATUS es = MH_EnableHook((void*)kEquip12C);
    MH_CreateHook((void*)kBoxFn, (void*)&Hook_Box, &g_orig_box);
    MH_EnableHook((void*)kBoxFn);
    MH_STATUS cx = MH_CreateHook((void*)kExpAdd, (void*)&Hook_ExpAdd, &g_orig_expadd);
    MH_STATUS ex = MH_EnableHook((void*)kExpAdd);
    MH_CreateHook((void*)kExpAdd2, (void*)&Hook_ExpAdd2, &g_orig_expadd2);
    MH_EnableHook((void*)kExpAdd2);
    MH_CreateHook((void*)kExpAdd3, (void*)&Hook_ExpAdd3, &g_orig_expadd3);
    MH_EnableHook((void*)kExpAdd3);
    MH_STATUS cw = MH_CreateHook((void*)kWaitOp, (void*)&Hook_Wait, &g_orig_wait);
    MH_STATUS ew = MH_EnableHook((void*)kWaitOp);
    MH_STATUS ct = MH_CreateHook((void*)kWaitTail, (void*)&Hook_WaitTail, &g_orig_waittail);
    MH_STATUS et = MH_EnableHook((void*)kWaitTail);
    // Intro-movie skip: report the opening AVIs as not-found.
    //
    // Hook BOTH kernel32 and KERNELBASE. The movie is not opened by the game's
    // own code path: FUN_005773e0 can read the file itself, but for the intro it
    // takes the DirectShow branch, and the filter graph (quartz / xvid.ax) opens
    // the file from ITS module — which imports CreateFileW from KERNELBASE, not
    // from the kernel32 export. Hooking only kernel32 therefore installs fine and
    // is simply never called, which is exactly what the log showed: the resolved
    // address was recorded at install and no "movie: blocked" line ever appeared
    // while the intro played.
    //
    // On modern Windows kernel32's CreateFileW is a thin forwarder to
    // KERNELBASE's, so hooking both can double-invoke the detour for callers that
    // go through kernel32. That is harmless here: the detour is a pure filename
    // test with no state, and the inner call simply sees a name it already
    // rejected (or passes it through twice).
    {
        void* cfw32 = nullptr; void* cfa32 = nullptr;
        if (HMODULE k = GetModuleHandleA("kernel32.dll")) {
            cfw32 = (void*)GetProcAddress(k, "CreateFileW");
            cfa32 = (void*)GetProcAddress(k, "CreateFileA");
            if (cfw32) { MH_CreateHook(cfw32, (void*)&Hook_CreateFileW, (void**)&g_orig_cfw); MH_EnableHook(cfw32); }
            if (cfa32) { MH_CreateHook(cfa32, (void*)&Hook_CreateFileA, (void**)&g_orig_cfa); MH_EnableHook(cfa32); }
        }
        // KERNELBASE — the one the DirectShow filter actually calls.
        void* cfwB = nullptr;
        if (HMODULE kb = GetModuleHandleA("kernelbase.dll")) {
            cfwB = (void*)GetProcAddress(kb, "CreateFileW");
            if (cfwB && cfwB != cfw32) {
                MH_STATUS c = MH_CreateHook(cfwB, (void*)&Hook_CreateFileW_KB,
                                            (void**)&g_orig_cfw_kb);
                MH_STATUS e = MH_EnableHook(cfwB);
                mod_log("hook_movies: KERNELBASE CreateFileW=%p create=%d enable=%d",
                        cfwB, (int)c, (int)e);
            }
        }
        mod_log("hook_movies: kernel32 CFW=%p CFA=%p, kernelbase CFW=%p",
                cfw32, cfa32, cfwB);
    }
    mod_log("hook_vm_install: grant=%d/%d popup=%d/%d skill-abort=%d/%d exp=%d/%d wait-ff=%d/%d tail-ff=%d/%d (+box)",
            (int)c, (int)e, (int)cg, (int)eg, (int)cs, (int)es, (int)cx, (int)ex, (int)cw, (int)ew, (int)ct, (int)et);
}
