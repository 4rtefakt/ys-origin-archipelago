// Self-contained Archipelago client embedded in the mod (apclientpp).
//
// Replaces the external Python client: the mod connects to the AP server
// directly over ws:// (local) or wss:// (e.g. archipelago.gg — set host with a
// wss:// scheme in yso_ap.cfg; TLS via OpenSSL, CA certs from the Windows store).
// It drives the in-game randomizer end to end:
//
//   * on slot_connected: parse slot_data to learn which vanilla item grants to
//     suppress (g_supp_item), which location flags are checks (g_loc_flag) and
//     their AP location ids (g_flag_to_loc), and item name -> g_flags index.
//   * the VM grant hook (hook_vm.cpp) calls ap_on_check() when a watched
//     location flag fires; we queue the AP location id and send LocationChecks.
//   * on items_received: resolve each AP item id to a g_flags index and grant it
//     in-game (give semantics). The server decides what the player gets; the mod
//     just applies it. (g_supp_item / g_loc_flag live in hook_bridge.cpp and are
//     shared with the VM hook.)
//
// Header-only stack (apclientpp/asio/websocketpp/json); build knobs in
// CMakeLists. Include apclient.hpp FIRST (pulls asio/winsock2); this TU avoids
// <windows.h>; the poll loop is a std::thread.
#include <apclient.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <list>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

void mod_log(const char* fmt, ...);
extern "C" void set_pending_box(int art_id, const char* name);  // relabel box (hook_vm.cpp)
namespace overlay {
void push_item(const std::string& text);
void push_toast(const std::string& item, const std::string& who, int flags, bool sent);
void set_status(const std::string& text);
void set_room(const std::string& text);
void set_tracker(const std::string& text);
void set_panel(const std::string& text);
}
namespace apchat {
void push(const std::string& line);
void set_visible(bool v);
}

// Shared with the VM grant hook (defined in hook_bridge.cpp).
extern bool g_supp_item[0x200];   // vanilla item indices to suppress
extern bool g_loc_flag[0x200];    // location flags that are checks
// Seed-scoped save redirection (hook_saveredir.cpp).
extern "C" void saveredir_set_seed(const char* seed);
extern "C" void saveredir_config(int enabled, const char* pattern);
extern bool g_statue_lock[0x200]; // locked statue activation flags (suppress purify)

static const char* AP_GAME = "Ys Origin";

// Connection settings — overridable via `yso_ap.cfg` next to the game exe
// (key=value lines: host, port, slot, password). Defaults suit local play.
static char g_host[128] = "archipelago.gg";  // menu strips scheme; wss:// implied
static int  g_port = 38281;
static char g_slot[128] = "Hugo";
static char g_pass[128] = "";
static bool g_autoconnect = false;  // yso_ap.cfg autoconnect=1 -> connect at boot
static char g_uri[192] = "ws://127.0.0.1:38281";
// Combat HP = *(module + g_hp_ptr) + g_hp_field, a float (reverse-engineered).
// g_hp_ptr is a static global holding the player-entity pointer; +0x98 = HP. The
// HUD/menu HP cells are mirrors that do NOT drive death — this entity HP does.
static int g_hp_ptr = 0x349D44;     // module-relative static entity pointer
static int g_hp_field = 0x98;       // HP offset within the entity
// Filled from yso_ap.cfg in load_config; documented at their feature blocks
// (overlay blessing shop / goal reporting) further down.
static int g_bless_arr_idx[32];     // bless_idx_N: bit -> state-array idx (-1 unmapped)
// goal_scene=: the ending scene. Default captured live on a real Toal clear:
// Darm dies in S_7099 -> current_scene blanks to 0 for the death cutscene -> the
// ending loads as S_7002. Overridable in yso_ap.cfg if another route differs; a
// wrong value only means the goal never fires (it can't false-complete, see the
// gameplay latch on the goal check).
static int g_goal_scene = 7002;

static void strip_eol(char* s) { s[strcspn(s, "\r\n")] = '\0'; }

static void load_config() {
    FILE* f = fopen("yso_ap.cfg", "r");
    if (!f) {
        // Drop a commented sample so the player knows the format.
        FILE* w = fopen("yso_ap.cfg", "w");
        if (w) {
            fputs("# Ys Origin Archipelago mod — connection settings.\n"
                  "# These are just DEFAULTS for the in-game connect menu\n"
                  "# (press F8 at the title screen). No need to edit this file\n"
                  "# unless you want autoconnect. host may include a scheme\n"
                  "# (ws:// for local, wss:// for archipelago.gg — the menu\n"
                  "# adds wss:// automatically if you omit it).\n"
                  "host=archipelago.gg\nport=38281\nslot=Hugo\npassword=\n"
                  "# autoconnect=1  # connect at startup with the above (skip the menu)\n"
                  "# save_redirect=1   # AP runs save into archipelago_<seed>/ next to\n"
                  "#                   # the normal saves (vanilla saves stay untouched).\n"
                  "#                   # 0 = write the regular save files as usual.\n"
                  "# save_pattern=sav  # filename substring that marks a save file\n"
                  "# chat=1            # show the AP chat overlay at boot (F6 toggles;\n"
                  "#                   # Enter types, e.g. !hint <item>)\n"
                  "# goal_scene=7002   # ending-scene number; entering it reports your\n"
                  "#                   # goal to the server. 7002 (verified on a real\n"
                  "#                   # clear) is the default — only set this if your\n"
                  "#                   # route ends on a different scene.\n",
                  w);
            fclose(w);
        }
        mod_log("ap: no yso_ap.cfg — wrote a sample; using defaults");
        return;
    }
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == ';') continue;
        char* eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char* key = line;
        char* val = eq + 1;
        strip_eol(val);
        if (!strcmp(key, "host")) { strncpy(g_host, val, sizeof(g_host) - 1); }
        else if (!strcmp(key, "port")) { g_port = atoi(val); }
        else if (!strcmp(key, "slot")) { strncpy(g_slot, val, sizeof(g_slot) - 1); }
        else if (!strcmp(key, "password")) { strncpy(g_pass, val, sizeof(g_pass) - 1); }
        else if (!strcmp(key, "hp_ptr")) { g_hp_ptr = (int)strtol(val, nullptr, 0); }
        else if (!strcmp(key, "hp_field")) { g_hp_field = (int)strtol(val, nullptr, 0); }
        else if (!strcmp(key, "autoconnect")) { g_autoconnect = atoi(val) != 0; }
        else if (!strcmp(key, "save_redirect")) { saveredir_config(atoi(val), nullptr); }
        else if (!strcmp(key, "save_pattern")) { saveredir_config(-1, val); }
        else if (!strcmp(key, "goal_scene")) { g_goal_scene = atoi(val); }
        else if (!strcmp(key, "chat")) { apchat::set_visible(atoi(val) != 0); }
        else if (!strncmp(key, "bless_idx_", 10)) {
            int bit = atoi(key + 10);
            if (bit >= 0 && bit < 32) g_bless_arr_idx[bit] = atoi(val);
        }
    }
    fclose(f);
    mod_log("ap: config host=%s port=%d slot=%s", g_host, g_port, g_slot);
}

static const uintptr_t kGFlagsAbs = 0x0076B91C;  // runtime g_flags base
static const int kGFlagsRel = 0x0036B91C;        // module-relative (slot_data offsets)
// Sanity window for a module-relative poll offset before we dereference it every
// tick. The real out-of-g_flags cells (armor blessing 0x36A684, floor/bit cells
// ~0x36BCxx) all live in the exe's .data band ~0x36xxxx; a corrupt or version-
// skewed slot_data offset outside this range would fault the poll thread.
static inline bool poll_offset_ok(int off) {
    return off >= 0x300000 && off < 0x400000;
}
// Current loaded scene = g_flags[0x1F9] (abs 0x0076C100), as a decimal leaf
// number (S_1004 -> 1004). Stable per room, changes on transition. Drives
// scene-method check detection (boss / room locations) + the overlay room line.
static const int kSceneIdx = 0x1F9;
static inline int read_current_scene() {
    return *(volatile int*)(kGFlagsAbs + kSceneIdx * 4);
}

static APClient* g_ap = nullptr;
static std::thread g_thread;
static std::atomic<bool> g_run{false};

// -- runtime (re)connect, driven by the in-game Archipelago menu -------------- #
// The menu (menu.cpp, main thread) fills these and raises g_conn_req; the poll
// thread owns the APClient lifecycle, so it does the actual create/teardown
// (no cross-thread client construction). Startup no longer auto-connects unless
// `autoconnect=1` is set in yso_ap.cfg — the player connects from the menu.
static std::mutex g_conn_mtx;
static std::atomic<bool> g_conn_req{false};
static char g_req_host[128] = "";
static int  g_req_port = 0;
static char g_req_slot[128] = "";
static char g_req_pass[128] = "";
static void create_client();  // defined below; builds g_ap on the poll thread
static void reset_floor_prev();  // clears the Reach-NF crossing baseline (poll section)

// flag index -> AP location ids (set in slot_connected, read in ap_on_check).
// A VECTOR per flag: one event flag can be several AP locations (the elemental
// altars grant two items — weapon + bracelet — on a single script flag).
// g_reg_mtx guards it: the poll thread rebuilds it on (re)connect while the VM
// hook reads it on the game's main thread.
static std::mutex g_reg_mtx;
static std::vector<int64_t> g_flag_to_loc[0x200];
// scene-method detection: scene number -> AP location ids fired on entering it
// (boss arenas, room-sanity checks); scene number -> room name for the overlay.
static std::map<int, std::vector<int64_t>> g_scene_locs;
static std::map<int, std::string> g_scene_name;
static std::set<int64_t> g_scene_fired;   // dedupe (each scene loc fires once)
static int g_last_scene = -1;

// Overlay trackers (slot_data): every ACTIVE location by scene / by floor, the
// blessing shop-hint names, and the statue scenes that trigger the panel. The
// checked-set mirrors the server (location_checked handler + our own sends).
static std::map<int, std::vector<int64_t>> g_scene_locations;  // scene -> locs
static std::map<int, std::vector<int64_t>> g_floor_locations;  // floor -> locs
static std::map<int64_t, std::string> g_bless_names;   // blessing loc -> name
static std::set<int> g_statue_scene_set;
// g_checked + g_floors_seen: written on the poll thread, read by the shop UI on
// the game's main thread (WndProc/EndScene) — guarded by g_checked_mtx.
static std::mutex g_checked_mtx;
static std::set<int64_t> g_checked;
static bool g_shop_hints = false;
static bool g_panel_toggle = false;       // F7: force the panel anywhere
// Item classification per scouted location (AP NetworkItem.flags: 1 progression,
// 2 useful, 4 trap) — colors the shop-hint lines.
static std::map<int64_t, int> g_loc_flags;

// -- overlay blessing shop (blessing_costs: random) --------------------------- #
// Our OWN shop UI (shop.cpp, F5): sells the bit-method blessings at the seed's
// randomized prices, deducting SP and granting the blessing directly — no
// dependency on the game's cost table. Buying sets the purchase bit (the bit
// poll then fires the check), writes the blessing state-array level, and
// requests the effect recompute (BLESSING_DIRTY). The state-array INDEX per
// blessing is the one un-RE'd piece: map them via `bless_idx_<bit>=<idx>` lines
// in yso_ap.cfg (captured once with tools/flaglog.py --bless); until a bit is
// mapped, its purchase still registers (bit + check + SP) and the effect
// applies after the next save+reload (g_flags persists the bit).
struct BlessShopItem { int64_t loc; std::string name; int bit; int cost; };
static std::vector<BlessShopItem> g_shop_items;      // sorted by cost, cheap first
static std::map<int64_t, int> g_loc_bitmap;          // blessing loc -> bit
static int g_shop_unlock_mode = 0;                   // 0 all, 1 one-per-floor
static std::set<int> g_floors_seen;                  // distinct floors visited
// Real spendable SP currency (the value shown in the HUD): a standalone static
// int, NOT g_flags[0xD8]. Live-confirmed 2026-07-02: 0x76A75C tracked the HUD SP
// (328) and accepted writes, while g_flags[0xD8] only ever held the AP-granted
// "SP: N" fillers (50) that the game never spends. It is the LOW dword of the
// {SP, level} pair (level = 0x76A760), so writing it doesn't touch the level.
static const uintptr_t kSpAbs = 0x0076A75C;                    // SP currency
// Guards read-modify-write of the SP cell: the F5 shop deducts on the game main
// thread while SP-filler grants add on the poll thread — without this a lost
// update either drops a grant or hands out a free blessing.
static std::mutex g_sp_mtx;
static const uintptr_t kBlessBitsAbs = kGFlagsAbs + 0xD9 * 4;  // purchase bitfield
static const uintptr_t kBlessBaseAbs = 0x0076A634;   // state array (+idx*8 = level)
static const uintptr_t kBlessDirtyAbs = 0x0076B914;  // |= 0x10 -> effect recompute
// Goal reporting: entering the ending scene (g_goal_scene, S_7002) sends
// StatusUpdate(GOAL) once. The ending shares the 7xxx range with the New-Game
// intro cutscenes (Toal's especially), so the scene alone can't tell "the game
// just ended" from "the game just started". g_saw_gameplay latches once a real
// gameplay scene (1000..6999) is entered: the intro plays BEFORE any of those, so
// a fresh game can't false-complete the slot, while a clear always has it set.
static bool g_goal_sent = false;
static bool g_saw_gameplay = false;
// Chat send queue (chat.cpp input -> poll thread -> g_ap->Say).
static std::mutex g_say_mtx;
static std::vector<std::string> g_say_queue;

extern "C" void ap_request_say(const char* text) {
    std::lock_guard<std::mutex> lk(g_say_mtx);
    g_say_queue.push_back(text);
}

// Poll-method detection, for signals the VM store hook can't see (they're
// written natively, not by script opcode 0x64): blessing purchases (a bitfield
// cell + the armor cell outside g_flags) and the current-floor "Reach NF"
// checks. Polled every client tick; each location fires once (g_poll_fired) and
// already-satisfied signals fire on connect (the AP server dedupes re-sends, so
// this doubles as catch-up for purchases made while disconnected).
static const uintptr_t kImageBase = 0x00400000;  // kGFlagsAbs - kGFlagsRel
struct PollBit   { uintptr_t abs; int bit;     int64_t loc; };  // (cell>>bit)&1
struct PollVal   { uintptr_t abs;              int64_t loc; };  // cell >= 1
struct PollFloor { uintptr_t abs; int floor_n; int64_t loc; };  // cell >= N
static std::vector<PollBit>   g_poll_bits;
static std::vector<PollVal>   g_poll_vals;
static std::vector<PollFloor> g_poll_floors;
static std::set<int64_t> g_poll_fired;    // dedupe (each poll loc fires once)

// -- DeathLink -------------------------------------------------------------- #
// Enabled from slot_data (death_link). We add the "DeathLink" tag after connect,
// send a bounce when the player dies, and kill the player when we receive one.
// The current-HP cell is reverse-engineered live; its module-relative offset is
// read from yso_ap.cfg (hp_offset=0x...) so it can be tuned without a rebuild.
static bool g_death_link = false;
static std::atomic<bool> g_pending_death{false};
static std::string g_pending_death_cause;
static double g_last_death_ts = 0.0;      // debounce sends / suppress self-echo
static int g_prev_hp = -1;

// Resolve the entity HP cell through the static pointer; 0 if not (yet) valid
// (during loads / menus the pointer may be null).
static inline uintptr_t hp_addr() {
    if (!g_hp_ptr) return 0;
    uint32_t ent = *(volatile uint32_t*)(0x00400000u + (uintptr_t)g_hp_ptr);
    if (ent < 0x10000u) return 0;
    return (uintptr_t)ent + (uintptr_t)g_hp_field;
}
static inline int read_hp() {
    uintptr_t a = hp_addr();
    return a ? (int)(*(volatile float*)a) : -1;
}
static inline void write_hp_zero() {
    uintptr_t a = hp_addr();
    if (a) *(volatile float*)a = 0.0f;
}
// AP item name -> g_flags index (from slot_data item_index).
static std::map<std::string, int> g_name_to_idx;
// Sacred artifact -> g_flags index of the POWER it unlocks (the bracelet cells
// 0x74/0x75/0x76). Casting keys off the power, not the artifact, and the vanilla
// chest sets both — but the bracelets aren't separate pickups, so an AP artifact
// grant has to light up its skill too or you own an uncastable item. From
// slot_data skill_grants.
static std::map<std::string, int> g_skill_grants;
// Statue warp locks: unlock-item name -> the statue's activation-flag index, so
// receiving that item clears g_statue_lock (purification allowed). Populated
// from slot_data statue_unlocks when statue_warp_locks is on.
static std::map<std::string, int> g_statue_item_idx;
// Statue WARP REGISTRY: a byte-per-statue array at g_flags[0x200] (abs 0x76C11C),
// indexed by statue SCENE order (live-confirmed: byte0=S_1000/1F, byte1=S_1009/4F,
// ...); byte=1 => that statue is a Crystal warp destination. The game writes it
// natively (not through the VM grant store), so the mod can't intercept the
// write — instead the poll loop clears locked statues' bytes each tick (revert
// pattern). g_statue_reg holds (registry index, activation-flag index, scene) for
// every statue, ordered by scene = registry index order.
struct StatueReg { int reg_index; int flag_idx; int scene; };
static std::vector<StatueReg> g_statue_reg;
static bool g_statue_locks_on = false;
// Statues unlocked via a received "Statue Warp" item (by activation-flag index).
// The poll loop FORCES these warpable+purified every tick, so the state survives
// an in-game save load (which reloads g_flags + the registry from the save file,
// clobbering a one-time write made at item-receipt / reconnect-replay time).
static std::set<int> g_statue_forced;

// -- catch-up level scaling ------------------------------------------------- #
// Static globals (no ASLR): current EXP, current LEVEL. FUN_004200f0(level) is
// the game's own "set to level N" (sets EXP=threshold, level, recomputes stats).
// g_exp_factor (in hook_vm.cpp) scales earned EXP at the award hook.
extern float g_exp_factor;
// Cutscene fast-forward: hook_vm reads the hold-to-skip hotkey each tick (it owns
// <windows.h>); while held, the event-VM wait op (0xF2) elapses instantly.
extern "C" void cutscene_ff_poll();
static const uintptr_t kLevelAbs = 0x0076A760;   // current character level
typedef void(__fastcall* SetLevelFn)(unsigned);
static const SetLevelFn kSetLevel = (SetLevelFn)0x004200F0;
static int g_level_scaling = 0;        // 0 off, 1 level_floor, 2 exp_mult, 3 both
static int g_level_margin = 3;
static int g_exp_base_mult = 3;        // flat EXP multiplier (exp modes)
static int g_exp_catchup_mult = 5;     // while level <= deepest expected + margin
static int g_exp_catchup_margin = 5;
static int g_expected_hi = 0;          // deepest visited floor's expected level
                                       // (session high-water; reset on New Game)
static std::map<int, int> g_scene_levels;  // scene number -> expected level
static std::map<int, int> g_scene_floors;  // scene number -> tower floor (shop pacing)
static std::atomic<int> g_pending_level{0};  // bump requested; applied on the main thread
static inline int read_level() { return *(volatile int*)kLevelAbs; }

// -- weapon level (Cleria Ore upgrade) -------------------------------------- #
// In vanilla you trade Cleria Ore to an NPC, whose cutscene runs WEAPON_LEVEL_UP
// (VM sub-op 0x7F) to raise the weapon. The weapon is the dominant damage factor,
// so the rando applies the upgrade the moment Cleria Ore is RECEIVED (no NPC trip
// needed for warped-ahead floors). We replicate sub-op 0x7F: set the persistent
// record g_flags[0x94], call the stat setter for the 4 weapon slots, then push
// the recomputed stat block into the live player entity. See RE_FINDINGS.md.
static const int kCleriaOreId = 0x58;
static const uintptr_t kWeaponLevelAbs = kGFlagsAbs + 0x94 * 4;  // 0x0076BB6C
// FUN_004201D0(ecx=stat idx, edx=value): stat[idx]=value, set recompute dirty bit,
// recompute (FUN_00420C40). The 4 weapon slots are idx 0..3.
typedef void(__fastcall* StatSetFn)(unsigned idx, int value);
static const StatSetFn kStatSet = (StatSetFn)0x004201D0;
static char** const kPlayerEntPtr = (char**)0x0074C09C;       // *() = player entity
static const uintptr_t kStatBlock = 0x0076A72C;               // -> entity+0x94..
// g_flags[0x94] weapon value per Cleria Ore count: the Nth ore is the Nth vanilla
// upgrade (the game's WEAPON_LEVEL_UP ladder steps 1->2->4->6->8), displaying as
// Lv = value/2 + 2, i.e. ore N -> vanilla weapon Lv N+1. Indexed by (count-1).
static const int kWeaponTier[5] = {1, 2, 4, 6, 8};  // 1..5 ore -> Lv2..Lv6
static std::atomic<int> g_cleria_count{0};   // ore received this run
static std::atomic<int> g_pending_weapon{0}; // tier requested; applied on main thread
static int g_weapon_applied = 0;             // last tier pushed to the entity
static char* g_weapon_entity = nullptr;      // entity we last pushed the weapon to
                                             // (re-push on respawn = new entity)
static const uintptr_t kWarpRegAbs = kGFlagsAbs + 0x200 * 4;  // 0x0076C11C (byte array)

// -- traps (received by NAME; effect applied on the main thread or as a timed
// window). Names must match data_tables.TRAP_POOL. -------------------------- #
static std::atomic<bool> g_trap_chaos_warp{false};   // main thread: warp to a random statue
static std::atomic<bool> g_trap_exp_leech{false};    // main thread: drop a chunk of EXP
static std::atomic<unsigned long> g_fog_until{0};    // Blinding Fog: overlay haze deadline
static std::atomic<unsigned long> g_butter_until{0}; // Butterfingers: weapon-Lv1 window end
static const uintptr_t kExpAbs = 0x0076A748;         // current EXP (float)

// Set the effect for a received trap. Returns true if `name` was a trap.
static bool apply_trap(const std::string& name) {
    unsigned long now = GetTickCount();
    if (name == "EXP Leech")          g_trap_exp_leech.store(true);
    else if (name == "Chaos Warp")    g_trap_chaos_warp.store(true);
    else if (name == "Butterfingers") g_butter_until.store(now + 8000);
    else if (name == "Blinding Fog")  g_fog_until.store(now + 30000);
    else return false;
    mod_log("ap: TRAP received -> %s", name.c_str());
    return true;
}
// Read by overlay.cpp for the fog haze.
extern "C" unsigned long ap_fog_until() { return g_fog_until.load(); }
extern "C" int ap_current_scene() { return read_current_scene(); }

// -- force-spawn (random start) --------------------------------------------- #
// On a random-start New Game, after the intro drops the player at 1F, warp them
// to the chosen spawn statue and grant a floor-appropriate loadout (weapon now;
// level falls out of the existing level-floor via the fixed scene_levels). The
// warp replicates the Crystal menu's confirm path (live-RE'd): set the target
// statue index at 0x76BB40, then run the menu's warp-confirm sequence.
static int g_start_statue_scene = 0;     // slot_data start_statue_scene (0/1000 = no warp)
static int g_start_weapon = 0;           // slot_data start_weapon (g_flags[0x94] value)
static int g_start_level = 0;            // slot_data start_level (New-Game level floor)
static std::vector<int> g_start_items;   // slot_data start_items (g_flags indices to own)
// SP fillers: item name -> SP amount, added to the currency cell g_flags[0xD8]
// (slot_data sp_items / sp_flag_idx; SP is a stat, not an inventory item).
static std::map<std::string, int> g_sp_items;
static int g_sp_flag_idx = 0xD8;
// Progressive gear: item name -> the character's tier ladder (g_flags indices in
// tier order); receiving one grants the first unowned tier (never skips ahead).
static std::map<std::string, std::vector<int>> g_prog_gear;
static int g_character = 1;              // 0=Yunica 1=Hugo 2=Toal (slot_data character)
static int g_spawn_reg_idx = -1;         // warp-registry index of the spawn statue
static int g_spawn_flag_idx = -1;        // purify flag index of the spawn statue
extern "C" __declspec(dllimport) unsigned long __stdcall GetTickCount(void);
static std::atomic<bool> g_saw_intro{false};     // New-Game intro (scene 2) seen
static std::atomic<unsigned long> g_intro_arm_tick{0};  // GetTickCount when armed
// Calibrated (F9 timing run): a warp ~1.5s after the intro arms takes cleanly,
// even with no player entity and during a scene-0 gap. Fire from here and retry
// until the spawn scene loads (the intro flickers, so one fire can be swallowed).
static const unsigned long kIntroDelayMs = 1600;
static const unsigned long kWarpRetryMs = 400;
static std::atomic<bool> g_force_spawn_done{false};
static std::atomic<bool> g_warp_request{false};  // manual test hotkey -> main thread
static volatile int g_warp_idx = -1;     // target for do_warp_native()
static const uintptr_t kWarpTarget = 0x0076BB40;  // warp target statue index

// Replicates the warp-menu confirm at 0x5ACCAF..0x5ACD16 (set target -> optional
// fade 0x5CE2A0 -> warp 0x434970 -> set flag 0x739160). MUST run on the main
// thread. Reads g_warp_idx for the destination; preserves nonvolatile regs.
// (Byte ops on absolute addresses are loaded through ebx: MSVC inline asm rejects
//  the `byte ptr [literal]` form that dword reads accept.)
__declspec(naked) static void do_warp_native() {
    __asm {
        push esi
        push edi
        push ebx
        xor  edi, edi
        mov  esi, dword ptr [g_warp_idx]
        mov  ebx, 0x0076BB40
        mov  dword ptr [ebx], esi              // 0x76BB40 = target index
        mov  esi, dword ptr [0x0074E238]
        test byte ptr [esi+8], 0x80
        jne  s1
        mov  ebx, 0x006D6067
        cmp  byte ptr [ebx], 0
        je   s2
        xor  ecx, ecx
        mov  eax, 0x00439EE0
        call eax
        test eax, eax
        js   s2
    s1:
        mov  edx, dword ptr [esi+8]
        xor  ecx, ecx
        push 0x400
        and  edx, 1
        mov  eax, 0x005CE2A0
        call eax
        add  esp, 4
    s2:
        push dword ptr [0x0074C09C]            // player entity
        xor  edx, edx
        mov  ecx, 0x006A7620
        push 0
        mov  eax, 0x00434970                   // the warp
        call eax
        add  esp, 8
        mov  eax, dword ptr [0x0074C09C]
        mov  ebx, 0x00739160
        mov  byte ptr [ebx], 1
        test eax, eax
        je   s3
        mov  word ptr [eax+0x4C8], di
    s3:
        pop  ebx
        pop  edi
        pop  esi
        ret
    }
}
// queued AP location ids to check (VM hook thread -> poll thread).
static std::mutex g_check_mtx;
static std::vector<int64_t> g_checks;
// highest received-item index already granted (dedupe replays this session).
static int g_applied_through = -1;
// AP location id -> "what's here" display string (from LocationScouts), so when
// a chest is opened we can show the real item + owning world (incl. other games).
static std::mutex g_scout_mtx;
static std::map<int64_t, std::string> g_loc_found;
// AP location id -> local Ys Origin item id (g_flags index) of what's placed
// there, for the native box's ART; -1 if foreign (use a generic art). And the
// item's display NAME for the box text. Populated from LocationScouts.
static std::map<int64_t, int> g_loc_local_id;
static std::map<int64_t, std::string> g_loc_item_name;
// item name -> classification (1/2/4/0), from slot_data; a fallback for the toast
// color when a received item carries no flags (cheat /send, or a stingy server).
static std::map<std::string, int> g_item_tiers;
static int tier_or(const std::string& name, int flags) {
    if (flags) return flags;
    auto it = g_item_tiers.find(name);
    return it != g_item_tiers.end() ? it->second : 0;
}

// Tier-colored toast for a fired location, from the scout map ("item  -> who").
// Caller must NOT hold g_scout_mtx.
static void toast_loc(int64_t loc) {
    std::string found, name;
    int flags = 0;
    {
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        auto f = g_loc_found.find(loc);
        if (f != g_loc_found.end()) found = f->second;
        auto in = g_loc_item_name.find(loc);
        if (in != g_loc_item_name.end()) name = in->second;
        auto fl = g_loc_flags.find(loc);
        if (fl != g_loc_flags.end()) flags = fl->second;
    }
    if (found.empty()) return;
    std::string item = name, who;
    size_t p = found.find("  -> ");
    if (p != std::string::npos) {
        if (item.empty()) item = found.substr(0, p);
        who = found.substr(p + 5);
    } else if (item.empty()) {
        item = found;
    }
    bool sent = !who.empty() && who != g_slot;
    overlay::push_toast(item, sent ? who : std::string(), tier_or(item, flags), sent);
}
// Generic art id for foreign items (a real Ys item id used as a placeholder
// icon until per-game icons exist). Roda Fruit (0x57) reads as a neutral pickup.
static const int kForeignArtId = 0x57;

// Grant an item in g_flags (give semantics: -1 -> 1, else +count). Atomic int32.
static void ap_give(int idx, int count) {
    if (idx < 0 || idx >= 0x200) return;
    volatile int* cell = (volatile int*)(kGFlagsAbs + idx * 4);
    int cur = *cell;
    int base = (cur >= 1) ? cur : 0;
    *cell = base + (count > 0 ? count : 1);
    mod_log("ap: granted g_flags[0x%X] %d -> %d", idx, cur, *cell);
}

// -- owned-gear reconcile ---------------------------------------------------- #
// Granted items live in g_flags, but the game rebuilds that block on every save
// load — and a DeathLink death reloads constantly. Any item granted since the
// last save is silently reset to unowned, and g_applied_through (session-only,
// monotonic) means on_items_received never re-grants it: the item is just gone
// until a full reconnect (which would also re-fire traps and re-add SP).
// So remember each granted cell's owned value and re-assert it every tick.
// ONLY progression/useful items are tracked: filler (Celcetan Panacea, herbs) is
// meant to be SPENT, and re-asserting it would make consumables infinite. Traps
// carry no flag, SP lives outside g_flags, Cleria Ore rides the weapon watermark
// and statue unlocks ride g_statue_forced — all already survive a reload.
static std::mutex g_gear_mtx;
static std::map<int, int> g_owned_gear;   // g_flags idx -> owned value

// Record a granted cell if the item is progression(1)/useful(2). Call AFTER the
// grant, so we latch the post-grant (cumulative) value.
static void remember_gear(int idx, int tier) {
    if (idx < 0 || idx >= 0x200) return;
    if (!(tier & 3)) return;                       // filler/trap -> spendable
    int v = *(volatile int*)(kGFlagsAbs + idx * 4);
    if (v < 1) return;
    std::lock_guard<std::mutex> lk(g_gear_mtx);
    int& cur = g_owned_gear[idx];
    if (v > cur) cur = v;                          // watermark: only ever rises
}

// Re-assert every tracked cell the game reset below its owned value.
static void reconcile_gear() {
    if (read_current_scene() <= 0) return;         // not in-game: block is stale
    std::lock_guard<std::mutex> lk(g_gear_mtx);
    for (const auto& kv : g_owned_gear) {
        volatile int* cell = (volatile int*)(kGFlagsAbs + kv.first * 4);
        if (*cell < kv.second) {
            mod_log("gear: restored g_flags[0x%X] %d -> %d (save/load wipe)",
                    kv.first, *cell, kv.second);
            *cell = kv.second;
        }
    }
}

// Called by the VM grant hook (game main thread) when a watched location flag
// fires. Queue its AP location id; the poll loop sends the LocationCheck. Also
// show what was here (item + owning world), from the scout map.
void ap_on_check(int flag_idx) {
    if (flag_idx < 0 || flag_idx >= 0x200) return;
    std::vector<int64_t> locs;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);   // vs reconnect re-registration
        locs = g_flag_to_loc[flag_idx];
    }
    for (int64_t loc : locs) {
        {
            std::lock_guard<std::mutex> lk(g_check_mtx);
            g_checks.push_back(loc);
        }
        std::string found, name;
        int local_id = -1, flags = 0;
        {
            std::lock_guard<std::mutex> lk(g_scout_mtx);
            auto it = g_loc_found.find(loc);
            if (it != g_loc_found.end()) found = it->second;
            auto il = g_loc_local_id.find(loc);
            if (il != g_loc_local_id.end()) local_id = il->second;
            auto in = g_loc_item_name.find(loc);
            if (in != g_loc_item_name.end()) name = in->second;
            auto fl = g_loc_flags.find(loc);
            if (fl != g_loc_flags.end()) flags = fl->second;
        }
        // Toast: "yours" for self, "-> Player" when you found it for someone else.
        if (!found.empty()) {
            std::string item = name, who;
            size_t p = found.find("  -> ");
            if (p != std::string::npos) {
                if (item.empty()) item = found.substr(0, p);
                who = found.substr(p + 5);
            } else if (item.empty()) {
                item = found;
            }
            bool sent = !who.empty() && who != g_slot;
            overlay::push_toast(item, sent ? who : std::string(), tier_or(item, flags), sent);
        }
        // Stash the actually-placed item so the native "Acquired <item>" box
        // (the 0xD5 op, which runs just after this check flag) shows the REAL
        // item: its art (local id, or a generic icon for foreign) and its name.
        // On a multi-location flag (altar double-grants) the last one wins the
        // single native box; the overlay above still lists every item.
        int art = (local_id >= 0) ? local_id : kForeignArtId;
        set_pending_box(art, name.c_str());
    }
}

// Reply to LocationScouts: learn the item + recipient at each of our locations.
static void on_location_info(const std::list<APClient::NetworkItem>& items) {
    std::lock_guard<std::mutex> lk(g_scout_mtx);
    for (const auto& it : items) {
        std::string game = g_ap->get_player_game(it.player);
        std::string item = g_ap->get_item_name(it.item, game);
        std::string who = g_ap->get_player_alias(it.player);
        g_loc_found[it.location] = item + "  -> " + who;   // owner = slot name
        // Local Ys Origin item -> remember its g_flags index for the native
        // box art; foreign (other game) -> -1 (use the generic art). Always keep
        // the display name for the box text.
        int lid = -1;
        if (game == AP_GAME) {
            auto f = g_name_to_idx.find(item);
            if (f != g_name_to_idx.end()) lid = f->second;
        }
        g_loc_local_id[it.location] = lid;
        g_loc_item_name[it.location] = item;
        g_loc_flags[it.location] = it.flags;   // 1 prog / 2 useful / 4 trap
    }
    mod_log("ap: scouted %d location(s)", (int)items.size());
}

static void on_slot_connected(const nlohmann::json& sd) {
    int supp = 0, locs = 0, names = 0;
    if (sd.contains("item_index")) {
        for (auto& kv : sd["item_index"].items()) {
            g_name_to_idx[kv.key()] = kv.value().get<int>();
            names++;
        }
    }
    g_skill_grants.clear();
    if (sd.contains("skill_grants"))
        for (auto& kv : sd["skill_grants"].items())
            g_skill_grants[kv.key()] = kv.value().get<int>();
    if (sd.contains("suppress_items")) {
        for (auto& v : sd["suppress_items"]) {
            int i = v.get<int>();
            if (i >= 0 && i < 0x200) { g_supp_item[i] = true; supp++; }
        }
    }
    std::list<int64_t> scout;
    int scenes = 0;
    // Reset detect registrations (a reconnect re-registers everything; without
    // this a flag would accumulate duplicate location entries). g_reg_mtx: the
    // VM hook reads g_flag_to_loc from the game's main thread.
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        for (int i = 0; i < 0x200; i++) g_flag_to_loc[i].clear();
    }
    g_poll_bits.clear();
    g_poll_vals.clear();
    g_poll_floors.clear();
    g_loc_bitmap.clear();
    // Per-session dedupe/progress state must also reset on a (re)connect, or a
    // second connection in the same process (the F8 menu allows switching rooms
    // without restarting) inherits the previous room's fired/checked/floor state.
    // Location ids are identical across seeds, so stale entries would silently
    // swallow this room's checks and mislabel the shop. g_floor_prev is defined
    // below (poll section) — reset via the extern helper declared there.
    g_poll_fired.clear();
    g_scene_fired.clear();
    g_goal_sent = false;
    g_saw_gameplay = false;
    g_applied_through = -1;
    {   // the ReceivedItems replay below re-grants everything -> rebuild from scratch
        std::lock_guard<std::mutex> lk(g_gear_mtx);
        g_owned_gear.clear();
    }
    reset_floor_prev();
    // Re-arm force-spawn from the connect point: discard any intro scene the
    // title/attract screen showed BEFORE the player connected, so a fresh F8
    // connect at the title can't burn the one-shot warp on nothing (which left
    // the real New Game to play the full intro). It now fires only when the intro
    // scene is entered AFTER connecting — i.e. the player actually starts a game.
    g_saw_intro.store(false);
    g_force_spawn_done.store(false);
    // Weapon watermark: the ReceivedItems replay on reconnect re-counts Cleria Ore
    // from index 0, so clear it here — otherwise a different-character reconnect
    // inherits the previous character's weapon tier (Yunica's floor weapon leaking
    // onto Toal), and a same-character reconnect double-counts the replayed ore.
    g_cleria_count.store(0);
    g_pending_weapon.store(0);
    g_weapon_applied = 0;
    g_weapon_entity = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_checked_mtx);
        g_checked.clear();
        g_floors_seen.clear();
    }
    if (sd.contains("location_detect")) {
        const auto& sig = sd.contains("location_signals") ? sd["location_signals"]
                                                          : nlohmann::json::object();
        for (auto& kv : sd["location_detect"].items()) {
            const auto& d = kv.value();
            if (!sig.contains(kv.key())) continue;
            int64_t loc = sig[kv.key()].get<int64_t>();
            const std::string method = d.value("method", std::string());
            if (method == "flag" && d.contains("offset")) {
                int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
                int idx = (off - kGFlagsRel) / 4;
                if (idx >= 0 && idx < 0x200) {
                    g_loc_flag[idx] = true;
                    std::lock_guard<std::mutex> lk(g_reg_mtx);
                    g_flag_to_loc[idx].push_back(loc);
                } else if (poll_offset_ok(off)) {
                    // Outside g_flags (e.g. the armor blessing cell): the VM
                    // store hook can't see it — poll for value >= 1 instead.
                    g_poll_vals.push_back({kImageBase + (uintptr_t)off, loc});
                } else {
                    mod_log("ap: WARN dropped flag detect, offset 0x%X out of range", off);
                    continue;
                }
                scout.push_back(loc);
                locs++;
            } else if (method == "bit" && d.contains("offset") && d.contains("bit")) {
                // Blessing purchases: one bitfield cell, one bit per blessing.
                // Written natively by the shop menu -> poll, don't hook.
                int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
                if (!poll_offset_ok(off)) {
                    mod_log("ap: WARN dropped bit detect, offset 0x%X out of range", off);
                    continue;
                }
                g_poll_bits.push_back({kImageBase + (uintptr_t)off,
                                       d["bit"].get<int>(), loc});
                g_loc_bitmap[loc] = d["bit"].get<int>();
                scout.push_back(loc);
                locs++;
            } else if (method == "floor" && d.contains("offset") && d.contains("floor")) {
                // "Reach NF": fires once current_floor reaches N (native write).
                int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
                if (!poll_offset_ok(off)) {
                    mod_log("ap: WARN dropped floor detect, offset 0x%X out of range", off);
                    continue;
                }
                g_poll_floors.push_back({kImageBase + (uintptr_t)off,
                                         d["floor"].get<int>(), loc});
                scout.push_back(loc);
                locs++;
            } else if (method == "scene" && d.contains("scene")) {
                // "S_1004" / "S_1014/S_BOX01" -> leading integer 1004 / 1014.
                const std::string s = d["scene"].get<std::string>();
                int num = atoi(s.c_str() + (s.size() > 2 && s[0] == 'S' ? 2 : 0));
                if (num <= 0) continue;
                g_scene_locs[num].push_back(loc);
                scout.push_back(loc);
                scenes++;
            }
        }
    }
    if (sd.contains("scene_names")) {
        for (auto& kv : sd["scene_names"].items())
            g_scene_name[atoi(kv.key().c_str())] = kv.value().get<std::string>();
    }
    // Overlay trackers: per-scene / per-floor location lists, shop hints, and
    // the statue scenes that pop the panel.
    g_scene_locations.clear();
    if (sd.contains("scene_locations"))
        for (auto& kv : sd["scene_locations"].items())
            for (auto& v : kv.value())
                g_scene_locations[atoi(kv.key().c_str())].push_back(v.get<int64_t>());
    g_floor_locations.clear();
    if (sd.contains("floor_locations"))
        for (auto& kv : sd["floor_locations"].items())
            for (auto& v : kv.value())
                g_floor_locations[atoi(kv.key().c_str())].push_back(v.get<int64_t>());
    g_bless_names.clear();
    if (sd.contains("blessing_names"))
        for (auto& kv : sd["blessing_names"].items())
            g_bless_names[atoll(kv.key().c_str())] = kv.value().get<std::string>();
    g_shop_hints = sd.value("shop_hints", false);
    g_statue_scene_set.clear();
    if (sd.contains("statue_scenes"))
        for (auto& v : sd["statue_scenes"])
            g_statue_scene_set.insert(v.get<int>());
    // Overlay blessing shop: randomized prices (empty = vanilla mode, no shop).
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);   // shop UI reads on main thread
        g_shop_items.clear();
    }
    g_shop_unlock_mode = sd.value("blessing_shop_unlock", 0);
    if (sd.contains("blessing_costs")) {
        std::vector<BlessShopItem> items;
        for (auto& kv : sd["blessing_costs"].items()) {
            int64_t loc = atoll(kv.key().c_str());
            auto nm = g_bless_names.find(loc);
            auto bt = g_loc_bitmap.find(loc);
            if (nm == g_bless_names.end() || bt == g_loc_bitmap.end()) continue;
            items.push_back({loc, nm->second, bt->second,
                             kv.value().get<int>()});
        }
        std::sort(items.begin(), items.end(),
                  [](const BlessShopItem& a, const BlessShopItem& b) {
                      return a.cost < b.cost;
                  });
        {
            std::lock_guard<std::mutex> lk(g_reg_mtx);
            g_shop_items.swap(items);
        }
        mod_log("ap: blessing shop — %d items, unlock mode %d",
                (int)g_shop_items.size(), g_shop_unlock_mode);
    }
    // Seed-scoped save redirection: hand the room seed to the file hook.
    saveredir_set_seed(g_ap->get_seed().c_str());
    int statues = 0;
    if (sd.value("statue_warp_locks", false) && sd.contains("statue_unlocks")) {
        g_statue_locks_on = true;
        int start_scene = sd.value("start_statue_scene", 0);
        std::vector<StatueReg> tmp;
        for (auto& kv : sd["statue_unlocks"].items()) {
            const auto& v = kv.value();
            int scene = v.value("scene", 0);
            const std::string off = v.value("flag", std::string());
            if (off.empty()) continue;
            int o = (int)strtol(off.c_str(), nullptr, 16);
            int idx = (o - kGFlagsRel) / 4;
            if (idx < 0 || idx >= 0x200) continue;
            g_statue_item_idx[kv.key()] = idx;     // unlock item name -> flag idx
            // The start statue stays usable from the beginning so the player can
            // always save; every other statue is locked until its item arrives.
            g_statue_lock[idx] = (scene != start_scene);
            tmp.push_back({0, idx, scene});
            statues++;
        }
        // Registry byte index = the game's AUTHORED warp-destination order (static
        // table at exe 0x68C190 -> scene-name structs; global/character-independent,
        // yso_win.exe v1.1.1.0). This is NOT a numeric scene sort: e.g. idx4=S_2013,
        // idx5=S_2100, idx6=S_2012; idx9=S_3015,idx10=S_3014; idx19=S_6082,idx20=S_6053.
        // Sorting by scene number mis-warps every spawn past idx3 (S_2013 -> lands on
        // S_2100) and corrupts locked-statue byte management. Assign each statue its
        // real index by looking its scene up in this order.
        static const int kWarpRegOrder[] = {
            1000, 1009, 1011, 2000, 2013, 2100, 2012, 3000, 3006, 3015, 3014,
            4000, 4104, 4020, 5000, 5010, 5014, 6000, 6010, 6082, 6053, 7000,
        };
        g_statue_reg.clear();
        for (const auto& s : tmp) {
            int reg = -1;
            for (int i = 0; i < (int)(sizeof(kWarpRegOrder) / sizeof(int)); i++)
                if (kWarpRegOrder[i] == s.scene) { reg = i; break; }
            if (reg < 0) {
                mod_log("ap: WARN statue S_%d not in warp registry order — skipped",
                        s.scene);
                continue;
            }
            g_statue_reg.push_back({reg, s.flag_idx, s.scene});
        }
        // Resolve the spawn statue's registry + purify-flag index for force-spawn.
        g_start_statue_scene = start_scene;
        for (const auto& s : g_statue_reg)
            if (s.scene == start_scene) {
                g_spawn_reg_idx = s.reg_index;
                g_spawn_flag_idx = s.flag_idx;
            }
        mod_log("ap: statue warp locks ON — %d statues, start scene S_%d (reg idx %d)",
                statues, start_scene, g_spawn_reg_idx);
    }
    g_start_weapon = sd.value("start_weapon", 0);   // spawn loadout weapon value
    // New-Game starting loadout floor: minimum level + items to mark owned.
    g_start_level = sd.value("start_level", 0);
    {
        // g_reg_mtx: exp_scaling_on_frame iterates this on the game main thread.
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        g_start_items.clear();
        if (sd.contains("start_items"))
            for (auto& v : sd["start_items"])
                g_start_items.push_back(v.get<int>());
    }
    g_sp_items.clear();
    if (sd.contains("sp_items"))
        for (auto& kv : sd["sp_items"].items())
            g_sp_items[kv.key()] = kv.value().get<int>();
    g_sp_flag_idx = sd.value("sp_flag_idx", 0xD8);
    g_item_tiers.clear();
    if (sd.contains("item_tiers"))
        for (auto& kv : sd["item_tiers"].items())
            g_item_tiers[kv.key()] = kv.value().get<int>();
    g_prog_gear.clear();
    if (sd.contains("progressive_gear"))
        for (auto& kv : sd["progressive_gear"].items()) {
            std::vector<int> tiers;
            for (auto& v : kv.value()) tiers.push_back(v.get<int>());
            g_prog_gear[kv.key()] = tiers;
        }
    g_character = sd.value("character", 1);         // 0 Yunica / 1 Hugo / 2 Toal
    g_level_scaling = sd.value("level_scaling", 0);
    g_level_margin = sd.value("level_margin", 3);
    g_exp_base_mult = sd.value("exp_base_mult", 3);
    g_exp_catchup_mult = sd.value("exp_catchup_mult", 5);
    g_exp_catchup_margin = sd.value("exp_catchup_margin", 5);
    if (sd.contains("scene_levels")) {
        for (auto& kv : sd["scene_levels"].items())
            g_scene_levels[atoi(kv.key().c_str())] = kv.value().get<int>();
    }
    g_scene_floors.clear();
    if (sd.contains("scene_floors")) {
        for (auto& kv : sd["scene_floors"].items())
            g_scene_floors[atoi(kv.key().c_str())] = kv.value().get<int>();
    }
    if (g_level_scaling)
        mod_log("ap: level scaling mode=%d (margin %d, exp x%d base / x%d catch-up +%d)",
                g_level_scaling, g_level_margin, g_exp_base_mult,
                g_exp_catchup_mult, g_exp_catchup_margin);
    if (sd.contains("death_link")) g_death_link = sd["death_link"].get<bool>();
    if (g_death_link) {
        g_ap->ConnectUpdate(false, 0, true, {std::string("DeathLink")});
        mod_log("ap: DeathLink ON (hp_ptr=0x%X+0x%X)", g_hp_ptr, g_hp_field);
    }
    mod_log("ap: slot_connected — %d items, %d suppress, %d location flags, "
            "%d scene checks", names, supp, locs, scenes);
    overlay::set_status(std::string("connected as ") + g_slot);
    if (!scout.empty()) g_ap->LocationScouts(scout, 0);  // learn what's at each
}

static void on_items_received(const std::list<APClient::NetworkItem>& items) {
    // A release/collect flood arrives as one big batch: apply everything, but
    // collapse the overlay feed to a single summary line (the chat overlay
    // still carries the full detail via PrintJSON).
    int fresh = 0;
    for (const auto& it : items)
        if (it.index > g_applied_through) fresh++;
    bool batch = fresh > 6;
    for (const auto& it : items) {
        if (it.index <= g_applied_through) continue;  // already applied this run
        std::string name = g_ap->get_item_name(it.item, AP_GAME);
        std::string from = g_ap->get_player_alias(it.player);
        int tier = tier_or(name, it.flags);   // classification: 1 prog, 2 useful, 4 trap
        auto su = g_statue_item_idx.find(name);
        auto f = g_name_to_idx.find(name);
        auto sp = g_sp_items.find(name);
        auto pg = g_prog_gear.find(name);
        if (sp != g_sp_items.end()) {
            // SP filler: add to the REAL SP currency cell (kSpAbs = 0x76A75C, the
            // HUD value), not g_flags[0xD8] which the game never spends.
            std::lock_guard<std::mutex> lk(g_sp_mtx);   // vs main-thread shop buy
            volatile int* spc = (volatile int*)kSpAbs;
            *spc = *spc + sp->second;
            mod_log("ap: granted SP +%d -> %d", sp->second, *spc);
        } else if (pg != g_prog_gear.end()) {
            // Progressive gear: grant the first unowned tier in the ladder.
            bool granted = false;
            for (int idx : pg->second)
                if (idx >= 0 && idx < 0x200 &&
                    *(volatile int*)(kGFlagsAbs + idx * 4) < 1) {
                    ap_give(idx, 1);
                    remember_gear(idx, tier);   // survive save/load wipes
                    granted = true;
                    break;
                }
            if (!granted)
                mod_log("ap: '%s' — all tiers owned, no-op", name.c_str());
        } else if (su != g_statue_item_idx.end()) {
            // Statue warp unlock: fully activate that statue immediately so it's
            // warpable right away (no need to revisit it). Stop clearing its warp
            // byte, set the warp-registry byte, and set the purify/activation flag
            // (darkness clears on next room entry; save/heal/warp all work).
            int fidx = su->second;
            g_statue_lock[fidx] = false;     // stop reverting its warp byte
            g_statue_forced.insert(fidx);    // poll forces it warpable+purified
            mod_log("ap: statue unlock '%s' -> g_flags[0x%X] forced warpable",
                    name.c_str(), fidx);
        } else if (f != g_name_to_idx.end() && f->second == kCleriaOreId) {
            // Cleria Ore: upgrade the weapon instead of granting the (dead) ore
            // item. The acquire box still shows "Cleria Ore" (box-relabel path).
            int n = g_cleria_count.fetch_add(1) + 1;  // 1..N ore now received
            int wtier = kWeaponTier[(n < 5 ? n : 5) - 1];
            g_pending_weapon.store(wtier);
            mod_log("ap: Cleria Ore #%d -> weapon tier value %d (pending)", n, wtier);
        } else if (apply_trap(name)) {
            // Trap effect armed above; the red trap toast fires below like any item.
        } else if (f != g_name_to_idx.end()) {
            ap_give(f->second, 1);
            remember_gear(f->second, tier);   // survive save/load wipes
            // A sacred artifact also unlocks its power (the bracelet cell) —
            // that's what the game checks to let you cast. Granting the artifact
            // alone leaves a dead skill slot.
            auto sk = g_skill_grants.find(name);
            if (sk != g_skill_grants.end()) {
                ap_give(sk->second, 1);
                remember_gear(sk->second, tier);
                mod_log("ap: '%s' -> also unlocked skill g_flags[0x%X]",
                        name.c_str(), sk->second);
            }
        } else
            mod_log("ap: received '%s' (id %lld) — no g_flags index, skipped",
                    name.c_str(), (long long)it.item);
        // Your own items already print as "Found: X (yours)" when their location
        // fires (ap_on_check), so only surface items coming FROM another player
        // (or the server) here — avoids showing every self-item twice.
        if (!batch && from != g_slot)
            overlay::push_toast(name, from, tier, /*sent=*/false);
        g_applied_through = it.index;
    }
    if (batch) {
        char buf[64];
        snprintf(buf, sizeof(buf), "Received %d items", fresh);
        overlay::push_item(buf);
    }
}

// Send a DeathLink bounce (we died). Debounced so a forced death / rapid HP
// flicker doesn't spam the room.
static void send_deathlink(const std::string& cause) {
    if (!g_ap || !g_death_link) return;
    double now = g_ap->get_server_time();
    if (now - g_last_death_ts < 6.0) return;
    g_last_death_ts = now;
    nlohmann::json data;
    data["time"] = now;
    data["source"] = g_slot;
    data["cause"] = cause;
    g_ap->Bounce(data, {}, {}, {std::string("DeathLink")});
    mod_log("ap: -> DeathLink sent (%s)", cause.c_str());
}

// Each tick: if a DeathLink arrived, kill the player (write HP=0); also detect
// our own death (HP crossed >0 -> <=0) and broadcast it. No-op until the HP
// offset is configured (hp_offset in yso_ap.cfg) and we're in a loaded scene.
static void poll_deathlink() {
    if (!g_death_link || !hp_addr()) return;
    bool in_game = read_current_scene() > 0;
    if (g_pending_death.exchange(false) && in_game) {
        write_hp_zero();
        g_last_death_ts = g_ap->get_server_time();   // suppress echoing this death
        g_prev_hp = 0;
        overlay::push_item("DeathLink: " + g_pending_death_cause);
        return;
    }
    if (!in_game) { g_prev_hp = -1; return; }
    int hp = read_hp();
    if (g_prev_hp > 0 && hp <= 0)
        send_deathlink(std::string(g_slot) + " ran out of HP");
    g_prev_hp = hp;
}

// Read current_scene each tick; on a room change, update the overlay room line
// and fire any scene-method checks (boss arenas / room-sanity) for the new
// scene, deduped so each fires once per session. Runs on the poll thread; the
// scene cell is a plain process-memory int, safe to read from here.
static void poll_scene() {
    int scene = read_current_scene();
    if (scene == g_last_scene) return;
    unsigned long since = g_saw_intro.load()
        ? (GetTickCount() - g_intro_arm_tick.load()) : 0;
    mod_log("scene: %d -> %d (entity %s, +%lums since arm)", g_last_scene, scene,
            *kPlayerEntPtr ? "yes" : "no", since);   // intro/New-Game trace
    g_last_scene = scene;

    // New-Game intro: Hugo/Yunica play scene 2; Toal plays his own cutscenes in
    // the 7xxx range (e.g. S_7001, the goddesses) — gameplay scenes are 1000-6151
    // so 7xxx is a safe Toal-intro marker. Arm force-spawn; the warp fires on the
    // main thread as soon as a player entity exists (skipping the rest). Fires once
    // per session (g_force_spawn_done not re-armed here, so a 7xxx cutscene playing
    // again mid-game can't re-warp the player).
    if (scene == 2 || (scene >= 7000 && scene < 8000)) {
        if (!g_saw_intro.exchange(true)) {
            g_intro_arm_tick.store(GetTickCount());
            mod_log("force-spawn: armed (New Game intro scene %d seen)", scene);
        }
        g_expected_hi = 0;   // New Game: forget the last run's deepest floor
    }

    // Real gameplay reached (the intro's cutscene scenes are 2 / 7xxx, and play
    // BEFORE any of these) -> the ending scene can now be trusted as an ending
    // rather than a New-Game intro. Gates the goal check below.
    if (scene >= 1000 && scene <= 6999) g_saw_gameplay = true;

    // Goal: entering the ending scene (S_7002 by default, goal_scene= to override)
    // marks the slot as GOALed so the multiworld releases/completes properly.
    // g_saw_gameplay gates it so the shared 7xxx intro range can't complete a
    // freshly-started slot (verified live: Darm -> scene 0 -> 7002 -> GOAL).
    if (g_goal_scene > 0 && scene == g_goal_scene && g_saw_gameplay &&
        !g_goal_sent && g_ap) {
        g_goal_sent = true;
        try {
            g_ap->StatusUpdate(APClient::ClientStatus::GOAL);
            overlay::push_item("GOAL complete!");
            mod_log("ap: goal scene %d reached — StatusUpdate(GOAL) sent", scene);
        } catch (...) {}
    }

    // Blessing-shop one-per-floor pacing: count distinct floors from the RELIABLE
    // current scene (the floor cell is wrong for warp destinations). Fed here on
    // every room change so warping to a statue still advances the shop.
    auto fl = g_scene_floors.find(scene);
    if (fl != g_scene_floors.end() && fl->second >= 1 && fl->second <= 26) {
        std::lock_guard<std::mutex> lk(g_checked_mtx);
        g_floors_seen.insert(fl->second);
    }

    auto it = g_scene_name.find(scene);
    char room[160];
    if (it != g_scene_name.end())
        snprintf(room, sizeof(room), "Room: %s", it->second.c_str());
    else if (scene > 0)
        snprintf(room, sizeof(room), "Room: S_%d", scene);
    else
        room[0] = '\0';
    overlay::set_room(room);

    auto locs = g_scene_locs.find(scene);
    if (locs == g_scene_locs.end()) return;
    std::vector<int64_t> fire;
    for (int64_t loc : locs->second) {
        if (g_scene_fired.insert(loc).second) fire.push_back(loc);
    }
    if (fire.empty()) return;
    {
        std::lock_guard<std::mutex> lk(g_check_mtx);
        g_checks.insert(g_checks.end(), fire.begin(), fire.end());
    }
    for (int64_t loc : fire) toast_loc(loc);
}

// Fire a batch of poll-detected locations: queue the LocationChecks and echo
// what was found (from the scout map) on the overlay. Same pattern as
// poll_scene's firing tail.
static void fire_poll_locs(const std::vector<int64_t>& fire) {
    if (fire.empty()) return;
    {
        std::lock_guard<std::mutex> lk(g_check_mtx);
        g_checks.insert(g_checks.end(), fire.begin(), fire.end());
    }
    for (int64_t loc : fire) toast_loc(loc);
}

// Poll-method checks (each client tick): blessing bits, out-of-g_flags value
// cells (armor blessing), and "Reach NF" floors. These are all written natively
// (shop menu / floor transition), so the VM store hook never sees them. Each
// location fires once per session. Blessings/values are CUMULATIVE state, so
// anything already satisfied fires on the first poll after connect (the server
// dedupes re-sends — clean catch-up for purchases made while disconnected).
// The floor cell is WHERE YOU ARE, not progress: it uses crossing semantics
// (prev primed on the first read, mirroring the Python client), so a random-
// start warp or a mid-run reconnect can't spray Reach-NF checks never earned.
// Only polled once a scene is loaded, so New-Game/menu garbage can't misfire.
static std::map<uintptr_t, int> g_floor_prev;   // floor cell -> last seen value
static std::atomic<bool> g_reprime_floor{false};  // set after a warp: rebaseline, don't fire
static void reset_floor_prev() { g_reprime_floor.store(true); }
static void poll_value_checks() {
    if (read_current_scene() <= 0) return;
    std::vector<int64_t> fire;
    for (const auto& pb : g_poll_bits)
        if (((*(volatile int*)pb.abs >> pb.bit) & 1) &&
            g_poll_fired.insert(pb.loc).second)
            fire.push_back(pb.loc);
    for (const auto& pv : g_poll_vals)
        if (*(volatile int*)pv.abs >= 1 && g_poll_fired.insert(pv.loc).second)
            fire.push_back(pv.loc);
    // A pending reprime (New Game / force-spawn / any warp) means the floor cell
    // just jumped for a reason that is NOT the player climbing — swallow this
    // tick's crossings so a spawn at 10F can't spray Reach-2F..10F.
    bool reprime = g_reprime_floor.exchange(false);
    std::map<uintptr_t, int> cur_floor;
    for (const auto& pf : g_poll_floors)
        cur_floor[pf.abs] = *(volatile int*)pf.abs;
    for (const auto& pf : g_poll_floors) {
        int cur = cur_floor[pf.abs];
        if (cur >= 1 && cur <= 26) {
            std::lock_guard<std::mutex> lk(g_checked_mtx);
            g_floors_seen.insert(cur);    // shop unlock pacing (one-per-floor)
        }
        if (reprime) continue;            // baseline this tick, fire nothing
        auto pit = g_floor_prev.find(pf.abs);
        if (pit == g_floor_prev.end()) continue;  // first sighting: prime only, below
        int prev = pit->second;
        // Fire only on ARRIVING at exactly floor_n from below (cur == floor_n),
        // not on any cur >= floor_n span — so a multi-floor warp doesn't back-fill
        // every intermediate Reach-NF the player never actually walked into.
        if (cur == pf.floor_n && pf.floor_n > prev &&
            g_poll_fired.insert(pf.loc).second)
            fire.push_back(pf.loc);
    }
    for (const auto& kv : cur_floor) g_floor_prev[kv.first] = kv.second;
    fire_poll_locs(fire);
}

// -- overlay trackers -------------------------------------------------------- #
// This TU avoids <windows.h> (see the header comment); declare the one user32
// import the F7 panel toggle needs.
extern "C" __declspec(dllimport) short __stdcall GetAsyncKeyState(int vk);
static const int kVkF7 = 0x76;

static int count_done(const std::vector<int64_t>& v) {
    std::lock_guard<std::mutex> lk(g_checked_mtx);
    int n = 0;
    for (int64_t l : v)
        if (g_checked.count(l)) n++;
    return n;
}

// floor number -> (found, total) checks, for the warp-map overlay (warpmap.cpp,
// render thread). Copies the loc list under g_reg_mtx (rebuilt at connect) then
// counts done under g_checked_mtx — never nests the two locks.
extern "C" bool ap_floor_count(int floor, int* found, int* total) {
    std::vector<int64_t> locs;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        auto it = g_floor_locations.find(floor);
        if (it == g_floor_locations.end() || it->second.empty()) return false;
        locs = it->second;
    }
    *total = (int)locs.size();
    *found = count_done(locs);
    return true;
}

// Sorted list of every floor number that has checks (for the warp-map overlay's
// "no bar" list — floors without a statue bar). Returns how many were written.
extern "C" int ap_floors_with_checks(int* out, int cap) {
    std::lock_guard<std::mutex> lk(g_reg_mtx);
    int n = 0;
    for (const auto& kv : g_floor_locations) {
        if (n >= cap) break;
        if (!kv.second.empty()) out[n++] = kv.first;
    }
    return n;
}

// Rebuild the "Checks here: k/n" line (always, for the current room) and the
// left panel (per-floor remaining checks + blessing shop hints). The panel
// auto-shows at goddess statues — that's where the warp menu (floors) and the
// blessing shop live — and F7 toggles it anywhere. Runs each poll tick; string
// work only, and the overlay setters are mutex-guarded.
static void update_trackers() {
    static bool f7_prev = false;
    bool f7 = (GetAsyncKeyState(kVkF7) & 0x8000) != 0;
    if (f7 && !f7_prev) g_panel_toggle = !g_panel_toggle;
    f7_prev = f7;

    // Rebuild only when the inputs changed — this runs every ~10ms poll tick
    // and the panel is a fair amount of string work.
    size_t checked, scouted;
    {
        std::lock_guard<std::mutex> lk(g_checked_mtx);
        checked = g_checked.size();
    }
    {
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        scouted = g_loc_found.size();
    }
    // F7 is a per-room override of the auto behavior: reset it on a room change
    // so it defaults back (shown at statues, hidden elsewhere) each new room.
    static int last_scene = -2;
    if (g_last_scene != last_scene) g_panel_toggle = false;
    static size_t last_checked = (size_t)-1, last_scouted = (size_t)-1;
    static bool last_toggle = false;
    if (g_last_scene == last_scene && checked == last_checked &&
        scouted == last_scouted && g_panel_toggle == last_toggle)
        return;
    last_scene = g_last_scene;
    last_checked = checked;
    last_scouted = scouted;
    last_toggle = g_panel_toggle;

    int scene = g_last_scene;
    std::string tr;
    auto it = g_scene_locations.find(scene);
    if (it != g_scene_locations.end() && !it->second.empty()) {
        char buf[48];
        snprintf(buf, sizeof(buf), "Checks here: %d/%d",
                 count_done(it->second), (int)it->second.size());
        tr = buf;
    }
    overlay::set_tracker(tr);

    // Auto-show at goddess statues; F7 flips that default for the current room
    // (so it can be hidden AT a statue, or shown anywhere else).
    bool at_statue = g_statue_scene_set.count(scene) != 0;
    if (!(at_statue ^ g_panel_toggle)) {
        overlay::set_panel("");
        return;
    }
    std::string p = "- Remaining checks -\n";
    std::string line;
    int col = 0;
    for (const auto& kv : g_floor_locations) {
        int left = (int)kv.second.size() - count_done(kv.second);
        if (left <= 0) continue;
        char buf[32];
        snprintf(buf, sizeof(buf), "%2dF:%-3d ", kv.first, left);
        line += buf;
        if (++col == 4) { p += line + "\n"; line.clear(); col = 0; }
    }
    if (!line.empty()) p += line + "\n";
    // (The blessing-shop hint list was removed from this panel — the F5 overlay
    // shop is the dedicated storefront now; this panel is remaining-checks only.)
    overlay::set_panel(p);
}

// -- overlay blessing shop accessors (UI in shop.cpp) ------------------------- #

static bool shop_item_owned(const BlessShopItem& it) {
    if ((*(volatile int*)kBlessBitsAbs >> it.bit) & 1) return true;
    std::lock_guard<std::mutex> lk(g_checked_mtx);
    return g_checked.count(it.loc) != 0;
}

static bool shop_item_unlocked(int i) {
    if (g_shop_unlock_mode != 1) return true;
    // Progression-carrying blessings are ALWAYS unlocked: one-per-floor pacing
    // must never trap a run-gating item (e.g. a Statue Warp that's the only
    // escape from a random-start floor). Only filler/useful blessings are paced.
    int64_t loc = -1;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        if (i >= 0 && i < (int)g_shop_items.size()) loc = g_shop_items[i].loc;
    }
    if (loc >= 0) {
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        auto f = g_loc_flags.find(loc);
        if (f != g_loc_flags.end() && (f->second & 1)) return true;  // progression
    }
    std::lock_guard<std::mutex> lk(g_checked_mtx);
    return i < (int)g_floors_seen.size();   // one slot per distinct floor visited
}

bool ap_shop_available() {
    std::lock_guard<std::mutex> lk(g_reg_mtx);
    return !g_shop_items.empty() && g_ap != nullptr;
}
int ap_shop_count() {
    std::lock_guard<std::mutex> lk(g_reg_mtx);
    return (int)g_shop_items.size();
}

std::string ap_shop_status() {
    char buf[96];
    int total;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        total = (int)g_shop_items.size();
    }
    int unlocked = 0;
    for (int i = 0; i < total; i++)
        if (shop_item_unlocked(i)) unlocked++;
    snprintf(buf, sizeof(buf), "SP: %d   (%d/%d slots unlocked)",
             *(volatile int*)kSpAbs, unlocked, total);
    return buf;
}

// One display line; first char is the classification/color marker the overlay
// scheme uses ('*' progression gold, '!' trap red, ' ' plain).
std::string ap_shop_line(int i) {
    BlessShopItem it;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        if (i < 0 || i >= (int)g_shop_items.size()) return "";
        it = g_shop_items[i];
    }
    std::string found = "?";
    int fl = 0;
    {
        // g_scout_mtx guards BOTH g_loc_found and g_loc_flags — on_location_info
        // rebuilds them on the poll thread while this runs on the render thread.
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        auto f = g_loc_found.find(it.loc);
        if (f != g_loc_found.end()) found = f->second;
        auto ff = g_loc_flags.find(it.loc);
        if (ff != g_loc_flags.end()) fl = ff->second;
    }
    const char* mark = (fl & 1) ? "*" : (fl & 4) ? "!" : " ";
    // The blessing effect is no longer shown — the shop is a pure AP storefront.
    // Recompose the scouted "item  -> who" as "who's item" (e.g. "Toal's Cleria
    // Ore"); "?" (unscouted / hints off) stays hidden.
    std::string reward = found;
    if (found == "?") {
        reward = "???";
    } else {
        size_t p = found.find("  -> ");
        if (p != std::string::npos)
            reward = found.substr(p + 5) + "'s " + found.substr(0, p);
    }
    char buf[256];
    if (shop_item_owned(it)) {
        snprintf(buf, sizeof(buf), " %s  (bought)", reward.c_str());
    } else if (!shop_item_unlocked(i)) {
        // one-per-floor pacing: slot i unlocks once you've visited i+1 distinct
        // floors, so it needs (i+1 - seen) more. Tell the player exactly how many.
        int seen;
        {
            std::lock_guard<std::mutex> lk(g_checked_mtx);
            seen = (int)g_floors_seen.size();
        }
        int need = (i + 1) - seen;
        if (need < 1) need = 1;
        snprintf(buf, sizeof(buf), " %d SP  =>  (locked - visit %d more floor%s)",
                 it.cost, need, need == 1 ? "" : "s");
    } else {
        snprintf(buf, sizeof(buf), "%s%d SP  =>  %s", mark, it.cost, reward.c_str());
    }
    return buf;
}

// Buy slot i: deduct SP, set the purchase bit (the bit poll fires the check),
// grant the effect (state array + recompute) when the array index is mapped.
bool ap_shop_buy(int i) {
    BlessShopItem it;
    {
        std::lock_guard<std::mutex> lk(g_reg_mtx);
        if (i < 0 || i >= (int)g_shop_items.size()) return false;
        it = g_shop_items[i];
    }
    if (shop_item_owned(it) || !shop_item_unlocked(i)) return false;
    {
        std::lock_guard<std::mutex> lk(g_sp_mtx);   // vs poll-thread SP grants
        volatile int* sp = (volatile int*)kSpAbs;
        if (*sp < it.cost) return false;
        *sp = *sp - it.cost;
    }
    *(volatile int*)kBlessBitsAbs |= (1 << it.bit);      // purchase bit -> check
    int idx = (it.bit >= 0 && it.bit < 32) ? g_bless_arr_idx[it.bit] : -1;
    if (idx >= 0) {
        *(volatile int*)(kBlessBaseAbs + (uintptr_t)idx * 8) = 1;  // level/owned
        *(volatile int*)kBlessDirtyAbs |= 0x10;                    // recompute
    } else {
        mod_log("ap: shop — bit %d has no bless_idx_%d mapping; effect applies "
                "after save+reload", it.bit, it.bit);
    }
    // No "Bought: X" toast -- the blessing check fires its own item toast, and
    // two banners for one purchase reads as a bug.
    mod_log("ap: shop bought '%s' (bit %d, %d SP, arr idx %d)",
            it.name.c_str(), it.bit, it.cost, idx);
    return true;
}

// Clear the warp-registry byte of every currently-locked statue. The game writes
// it natively when you interact with a statue (we can't catch that at the VM
// grant store), so we revert it here each tick. Unlocked + start statues keep
// their bytes, so they stay valid Crystal warp destinations.
static void poll_statue_warp() {
    if (!g_statue_locks_on) return;
    volatile unsigned char* reg = (volatile unsigned char*)kWarpRegAbs;
    for (const auto& s : g_statue_reg) {
        if (g_statue_lock[s.flag_idx]) {
            // Locked: keep it un-registered (the game writes the byte natively on
            // interaction; revert it).
            if (reg[s.reg_index] != 0) {
                reg[s.reg_index] = 0;
                mod_log("statue: cleared warp registry byte[%d] (scene S_%d, locked)",
                        s.reg_index, s.scene);
            }
        } else if (g_statue_forced.count(s.flag_idx)) {
            // Unlocked via item: force it warpable + purified every tick so it
            // survives a save load (which would otherwise reset it to the saved,
            // still-locked state). The start statue is neither locked nor forced
            // and registers normally via the intro.
            if (reg[s.reg_index] != 1) reg[s.reg_index] = 1;
            volatile int* pf = (volatile int*)(kGFlagsAbs + s.flag_idx * 4);
            if (*pf != 1) *pf = 1;
        }
    }
}

// Compute the catch-up EXP factor and (for the level floor) request a bump.
// Runs on the poll thread: only READS level/floor + sets g_exp_factor / a pending
// target; the actual level write happens on the main thread (exp_scaling_on_frame).
static void exp_scaling_poll() {
    if (!g_level_scaling) { g_exp_factor = 1.0f; return; }
    auto it = g_scene_levels.find(read_current_scene());
    int explv = (it != g_scene_levels.end()) ? it->second : 0;
    int lv = read_level();
    if (lv <= 0 || lv > 99) { g_exp_factor = 1.0f; return; }  // not in-game
    if (explv > g_expected_hi) g_expected_hi = explv;  // deepest floor visited
    // EXP multiplier (modes 2 + 3): the base multiplier everywhere; the catch-up
    // multiplier while at/under the deepest visited floor's expected level +
    // margin. Keyed to your furthest PROGRESS (not the current room), so you can
    // catch up by fighting anywhere — including easy floors below you.
    if (g_level_scaling == 2 || g_level_scaling == 3) {
        bool catching_up = g_expected_hi > 0 &&
                           lv <= g_expected_hi + g_exp_catchup_margin;
        g_exp_factor = (float)(catching_up ? g_exp_catchup_mult
                                           : g_exp_base_mult);
    } else {
        g_exp_factor = 1.0f;
    }
    // Level floor (modes 1 + 3): bump up to (expected - margin) if below.
    if ((g_level_scaling == 1 || g_level_scaling == 3) && explv > 0) {
        int target = explv - g_level_margin;
        if (target > lv && target > 1) g_pending_level.store(target);
    }
}

// Replicate VM sub-op 0x7F (the weapon-upgrade apply). MUST run on the main
// thread (it calls the stat recompute FUN_00420C40 via kStatSet). Sets the
// persistent record, the 4 weapon stat slots, then pushes the recomputed stat
// block into the live player entity so the upgrade takes effect immediately.
// Push a weapon tier into the game (record + 4 stat slots + live entity) WITHOUT
// touching the tracked g_weapon_applied -- the Butterfingers trap uses this to
// jam the weapon to Lv1 for a window, after which the enforcement restores the
// real tier (since the record then reads below g_weapon_applied).
static void set_weapon_game(int tier) {
    *(volatile int*)kWeaponLevelAbs = tier;     // persistent record / trade gate / menu
    kStatSet(0, tier);
    kStatSet(1, tier);
    kStatSet(2, tier);
    kStatSet(3, tier);
    char* ent = *kPlayerEntPtr;
    if (ent) {
        memcpy(ent + 0x94, (const void*)(kStatBlock + 0x00), 16);
        memcpy(ent + 0xA4, (const void*)(kStatBlock + 0x10), 16);
        memcpy(ent + 0xB4, (const void*)(kStatBlock + 0x20), 16);
        memcpy(ent + 0xC4, (const void*)(kStatBlock + 0x30), 8);
        *(int*)(ent + 0xCC) = *(const int*)(kStatBlock + 0x38);
    }
}
static void apply_weapon_level(int tier) {
    set_weapon_game(tier);
    g_weapon_applied = tier;
    mod_log("weapon: applied tier value %d", tier);
}

// Force-spawn the player at the random-start statue. MUST run on the main thread
// (the warp touches the player entity / scene). Registers + purifies the spawn
// statue, runs the native warp, and queues the floor-appropriate weapon (the
// level is handled by the level-floor once the new scene loads).
static void force_spawn() {
    if (g_spawn_reg_idx < 0) return;
    // Grant the warp crystal the (now-skipped) intro would have given: Toal uses
    // the Dark Crystal (g_flags[0x56]), Hugo/Yunica the Crystal (g_flags[0x54]).
    int crystal_idx = (g_character == 2) ? 0x56 : 0x54;
    *(volatile int*)(kGFlagsAbs + crystal_idx * 4) = 1;
    ((volatile unsigned char*)kWarpRegAbs)[g_spawn_reg_idx] = 1;   // register spawn
    if (g_spawn_flag_idx >= 0)
        *(volatile int*)(kGFlagsAbs + g_spawn_flag_idx * 4) = 1;   // purify spawn
    g_warp_idx = g_spawn_reg_idx;
    do_warp_native();
    if (g_start_weapon > 0) g_pending_weapon.store(g_start_weapon);
    // The warp jumps the floor cell for a non-climb reason — rebaseline the
    // Reach-NF crossing detector so it doesn't spray floor checks on arrival.
    reset_floor_prev();
    mod_log("force-spawn: warped to spawn S_%d (reg idx %d), weapon=%d, crystal idx 0x%X",
            g_start_statue_scene, g_spawn_reg_idx, g_start_weapon, crystal_idx);
}

// Manual force-spawn trigger (test hotkey, polled in hook_vm). Re-warps to the
// spawn statue on demand so the warp can be validated without replaying the intro.
extern "C" void request_force_spawn() { g_warp_request.store(true); }

// Apply pending stat changes on the GAME's main thread (called from the D3D9
// EndScene hook). Uses the game's own fns so EXP/threshold/stats stay consistent.
extern "C" void exp_scaling_on_frame() {
    // Random-start force-spawn: skip the WHOLE intro. The moment New Game starts
    // its opening (scene 2 seen) and a player entity exists, warp straight to the
    // spawn statue and grant the crystal ourselves — no waiting for the intro's
    // cutscenes/dialogue/tutorial (which also skips Toal's extra cutscenes). F9
    // re-triggers manually.
    // Fire for ANY valid spawn incl. S_1000 (1F): random_start's intro-skip is
    // wanted wherever you land, and with a low max_starting_floor 1F is a common
    // roll — the old `!= 1000` guard made a 1F spawn play the full intro.
    bool spawn_seed = g_start_statue_scene > 0;
    bool manual = g_warp_request.exchange(false);
    // Auto-fire once a New Game has begun and the intro has handed us a player
    // entity in a loaded scene. g_saw_intro (set when the intro scene 2/7xxx is
    // entered) proves a New Game started; it's re-armed to false at connect so a
    // pre-connect title screen can't trip it. We DON'T require the current scene
    // to still be the intro cutscene — during that cutscene there is no player
    // entity yet, so we'd never fire; instead we wait for the entity to appear
    // (control handed over) and warp a short beat later.
    int cur_scene = read_current_scene();
    // The warp has landed once the spawn scene is loaded — then stop retrying.
    bool landed = g_saw_intro.load() && cur_scene == g_start_statue_scene;
    if (landed) g_force_spawn_done.store(true);
    // Wall-clock delay from intro arm (flicker-proof; a frame counter can't be
    // used because the cutscene bounces scene 2 <-> 0). Past the delay, fire and
    // retry every kWarpRetryMs until the spawn scene loads.
    unsigned long now = GetTickCount();
    bool delay_passed = g_saw_intro.load() &&
        (now - g_intro_arm_tick.load()) >= kIntroDelayMs;
    bool want_auto = delay_passed && !g_force_spawn_done.load() && !landed;
    static unsigned long s_last_fire = 0;
    if (spawn_seed && (manual || want_auto)) {
        if (manual || now - s_last_fire >= kWarpRetryMs) {
            s_last_fire = now;
            force_spawn();
        }
    }

    // New-Game starting loadout (start_level / start_weapon / start_items): a floor
    // applied continuously once a player entity + loaded scene exist, so it's
    // idempotent (only ever raises), survives new-game init / save reload, and
    // composes with Cleria-Ore weapon upgrades and catch-up level scaling. Only
    // writes when below the floor, so it self-heals without spamming.
    if (*kPlayerEntPtr != nullptr && read_current_scene() > 0) {
        if (g_start_weapon > g_weapon_applied)
            g_pending_weapon.store(g_start_weapon);   // -> weapon section below
        if (g_start_level > 1 && read_level() < g_start_level)
            g_pending_level.store(g_start_level);      // -> level section below
        std::vector<int> start_items;
        {
            std::lock_guard<std::mutex> lk(g_reg_mtx);  // vs reconnect rebuild
            start_items = g_start_items;
        }
        for (int idx : start_items)
            if (idx >= 0 && idx < 0x200 &&
                *(volatile int*)(kGFlagsAbs + idx * 4) < 1)
                *(volatile int*)(kGFlagsAbs + idx * 4) = 1;
    }

    // Traps that touch the player/scene run here (main thread), only in-game.
    if (cur_scene > 0) {
        if (g_trap_exp_leech.exchange(false)) {
            // Drop a whole level (sets EXP to the lower level's threshold + rescales
            // stats) rather than a flat EXP cut -- reads as "you lost a level".
            int lv = read_level();
            if (lv > 1) {
                kSetLevel((unsigned)(lv - 1));
                mod_log("trap: EXP Leech -> Lv %d", lv - 1);
            }
        }
        if (g_trap_chaos_warp.exchange(false)) {
            volatile unsigned char* reg = (volatile unsigned char*)kWarpRegAbs;
            int cand[24], nc = 0;
            for (int i = 0; i < 22; i++) if (reg[i]) cand[nc++] = i;
            if (nc > 0) {
                g_warp_idx = cand[GetTickCount() % (unsigned)nc];
                do_warp_native();
                reset_floor_prev();
                mod_log("trap: Chaos Warp -> reg idx %d", g_warp_idx);
            }
        }
    }

    // Weapon upgrade (Cleria Ore) + the Butterfingers trap. Re-enforce if a save
    // load reset g_flags[0x94] below what we applied.
    int wv = g_pending_weapon.exchange(0);
    if (wv > g_weapon_applied) g_weapon_applied = wv;
    char* cur_ent = *kPlayerEntPtr;
    bool ent_changed = (cur_ent != nullptr && cur_ent != g_weapon_entity);
    if (GetTickCount() < g_butter_until.load()) {
        // Butterfingers: jam the weapon to Lv1 for the window WITHOUT touching
        // g_weapon_applied, so the real tier restores when the window ends.
        if (*(volatile int*)kWeaponLevelAbs != 0 || ent_changed) {
            set_weapon_game(0);
            g_weapon_entity = cur_ent;
        }
    } else if (g_weapon_applied > 0 &&
        (wv > 0 || ent_changed || *(volatile int*)kWeaponLevelAbs < g_weapon_applied)) {
        // Re-apply on: new tier, save-load reset, or entity change (respawn spawns
        // a fresh entity whose combat weapon defaults to Lv1 -> deals 1 dmg).
        apply_weapon_level(g_weapon_applied);
        g_weapon_entity = *kPlayerEntPtr;
    }
    // Level floor.
    int t = g_pending_level.exchange(0);
    if (t <= 0 || t > 99) return;
    if (read_level() < t) {
        kSetLevel((unsigned)t);
        mod_log("level scaling: bumped to Lv %d (floor catch-up)", t);
    }
}

static void poll_loop() {
    while (g_run.load()) {
        cutscene_ff_poll();   // hold-to-fast-forward hotkey (works pre/post connect)
        // Menu-driven (re)connect: copy the requested settings into the live
        // config and (re)build the client on THIS thread (it owns g_ap).
        if (g_conn_req.exchange(false)) {
            {
                std::lock_guard<std::mutex> lk(g_conn_mtx);
                strncpy(g_host, g_req_host, sizeof(g_host) - 1); g_host[sizeof(g_host)-1] = 0;
                strncpy(g_slot, g_req_slot, sizeof(g_slot) - 1); g_slot[sizeof(g_slot)-1] = 0;
                strncpy(g_pass, g_req_pass, sizeof(g_pass) - 1); g_pass[sizeof(g_pass)-1] = 0;
                g_port = g_req_port;
            }
            try { create_client(); }
            catch (const std::exception& e) {
                mod_log("ap: create_client failed: %s", e.what());
                overlay::set_status(std::string("connect error: ") + e.what());
            } catch (...) {
                mod_log("ap: create_client failed (unknown)");
                overlay::set_status("connect error");
            }
        }
        if (g_ap) {
            try { g_ap->poll(); } catch (...) {}
            poll_scene();
            poll_value_checks();
            poll_deathlink();
            poll_statue_warp();
            reconcile_gear();   // re-assert granted gear a save/load reset
            exp_scaling_poll();
            // drain queued checks
            std::vector<int64_t> pending;
            {
                std::lock_guard<std::mutex> lk(g_check_mtx);
                pending.swap(g_checks);
            }
            if (!pending.empty()) {
                std::list<int64_t> locs(pending.begin(), pending.end());
                g_ap->LocationChecks(locs);
                {
                    std::lock_guard<std::mutex> lk(g_checked_mtx);
                    g_checked.insert(pending.begin(), pending.end());
                }
                mod_log("ap: sent %d LocationCheck(s)", (int)locs.size());
            }
            // Outgoing chat (typed in the F6 overlay; also server commands
            // like !hint).
            std::vector<std::string> says;
            {
                std::lock_guard<std::mutex> lk(g_say_mtx);
                says.swap(g_say_queue);
            }
            for (const auto& s : says) {
                try { g_ap->Say(s); } catch (...) {}
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

// Build the APClient for the current g_host/g_port/g_slot/g_pass and wire its
// handlers. MUST run on the poll thread (it owns g_ap's lifecycle). Any existing
// client is torn down first so this doubles as "reconnect with new settings".
static void create_client() {
    if (g_ap) {
        mod_log("ap: tearing down existing client before reconnect");
        delete g_ap;
        g_ap = nullptr;
    }
    if (strstr(g_host, "://"))
        snprintf(g_uri, sizeof(g_uri), "%s:%d", g_host, g_port);
    else
        snprintf(g_uri, sizeof(g_uri), "ws://%s:%d", g_host, g_port);
    mod_log("ap: creating client (game=%s uri=%s slot=%s)", AP_GAME, g_uri, g_slot);
    overlay::set_status(std::string("connecting to ") + g_uri + " ...");
    g_ap = new APClient("YsOrigin-Mod", AP_GAME, g_uri);

    g_ap->set_socket_connected_handler([]() {
        mod_log("ap: socket connected");
        overlay::set_status("connected; authenticating...");
    });
    g_ap->set_socket_disconnected_handler([]() {
        mod_log("ap: socket disconnected");
        overlay::set_status("disconnected - retrying...");
    });
    g_ap->set_room_info_handler([]() {
        mod_log("ap: room_info -> ConnectSlot(%s)", g_slot);
        g_ap->ConnectSlot(g_slot, g_pass, 0b111);
    });
    g_ap->set_slot_connected_handler(on_slot_connected);
    g_ap->set_items_received_handler(on_items_received);
    g_ap->set_location_info_handler(on_location_info);
    // Server-known checked locations (sent on connect + after each check):
    // feeds the overlay trackers so counts survive reconnects/co-op checks.
    g_ap->set_location_checked_handler([](const std::list<int64_t>& locs) {
        std::lock_guard<std::mutex> lk(g_checked_mtx);
        for (int64_t l : locs) g_checked.insert(l);
    });
    // Room chat / item log -> the F6 chat overlay (rendered to plain text).
    g_ap->set_print_json_handler([](const APClient::PrintJSONArgs& args) {
        if (!g_ap) return;
        try {
            apchat::push(g_ap->render_json(args.data, APClient::RenderFormat::TEXT));
        } catch (...) {}
    });
    g_ap->set_bounced_handler([](const nlohmann::json& packet) {
        if (!g_death_link || !packet.contains("tags")) return;
        bool dl = false;
        for (auto& t : packet["tags"])
            if (t.is_string() && t.get<std::string>() == "DeathLink") { dl = true; break; }
        if (!dl) return;
        std::string src, cause = "DeathLink";
        if (packet.contains("data")) {
            const auto& d = packet["data"];
            if (d.contains("source") && d["source"].is_string()) src = d["source"].get<std::string>();
            if (d.contains("cause") && d["cause"].is_string()) cause = d["cause"].get<std::string>();
        }
        if (src == g_slot) return;          // ignore our own death echo
        g_pending_death_cause = cause;
        g_pending_death.store(true);
        mod_log("ap: <- DeathLink (%s)", cause.c_str());
    });
    // apclientpp drives the socket connection from poll() — no explicit connect.
}

// -- public API (called from menu.cpp, main thread) ------------------------- #

// Request a (re)connect with the given settings. The poll thread performs it.
void ap_request_connect(const char* host, int port, const char* slot,
                        const char* pass) {
    std::lock_guard<std::mutex> lk(g_conn_mtx);
    strncpy(g_req_host, host, sizeof(g_req_host) - 1); g_req_host[sizeof(g_req_host)-1] = 0;
    strncpy(g_req_slot, slot, sizeof(g_req_slot) - 1); g_req_slot[sizeof(g_req_slot)-1] = 0;
    strncpy(g_req_pass, pass, sizeof(g_req_pass) - 1); g_req_pass[sizeof(g_req_pass)-1] = 0;
    g_req_port = port;
    g_conn_req.store(true);
    mod_log("ap: connect requested from menu (host=%s port=%d slot=%s)", host, port, slot);
}

// Prefill accessors for the menu (defaults from yso_ap.cfg).
const char* ap_cfg_host() { return g_host; }
int         ap_cfg_port() { return g_port; }
const char* ap_cfg_slot() { return g_slot; }
const char* ap_cfg_pass() { return g_pass; }

void ap_install() {
    for (int i = 0; i < 0x200; i++) g_flag_to_loc[i].clear();
    for (int i = 0; i < 32; i++) g_bless_arr_idx[i] = -1;   // cfg bless_idx_N maps
    load_config();               // prefill defaults for the in-game menu
    overlay::set_status("not connected - open the Archipelago menu");

    g_run.store(true);
    g_thread = std::thread(poll_loop);
    g_thread.detach();  // let the process exit cleanly (no join at teardown)
    mod_log("ap: poll thread started (autoconnect=%d)", (int)g_autoconnect);

    if (g_autoconnect)
        ap_request_connect(g_host, g_port, g_slot, g_pass);
}
