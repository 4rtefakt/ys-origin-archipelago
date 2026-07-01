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
void set_status(const std::string& text);
void set_room(const std::string& text);
}

// Shared with the VM grant hook (defined in hook_bridge.cpp).
extern bool g_supp_item[0x200];   // vanilla item indices to suppress
extern bool g_loc_flag[0x200];    // location flags that are checks
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
                  "# autoconnect=1  # connect at startup with the above (skip the menu)\n",
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
    }
    fclose(f);
    mod_log("ap: config host=%s port=%d slot=%s", g_host, g_port, g_slot);
}

static const uintptr_t kGFlagsAbs = 0x0076B91C;  // runtime g_flags base
static const int kGFlagsRel = 0x0036B91C;        // module-relative (slot_data offsets)
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

// flag index -> AP location ids (set in slot_connected, read in ap_on_check).
// A VECTOR per flag: one event flag can be several AP locations (the elemental
// altars grant two items — weapon + bracelet — on a single script flag).
static std::vector<int64_t> g_flag_to_loc[0x200];
// scene-method detection: scene number -> AP location ids fired on entering it
// (boss arenas, room-sanity checks); scene number -> room name for the overlay.
static std::map<int, std::vector<int64_t>> g_scene_locs;
static std::map<int, std::string> g_scene_name;
static std::set<int64_t> g_scene_fired;   // dedupe (each scene loc fires once)
static int g_last_scene = -1;

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
static std::atomic<bool> g_saw_intro{false};     // New-Game intro (scene 2) seen
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

// Called by the VM grant hook (game main thread) when a watched location flag
// fires. Queue its AP location id; the poll loop sends the LocationCheck. Also
// show what was here (item + owning world), from the scout map.
void ap_on_check(int flag_idx) {
    if (flag_idx < 0 || flag_idx >= 0x200) return;
    for (int64_t loc : g_flag_to_loc[flag_idx]) {
        {
            std::lock_guard<std::mutex> lk(g_check_mtx);
            g_checks.push_back(loc);
        }
        std::string found, name;
        int local_id = -1;
        {
            std::lock_guard<std::mutex> lk(g_scout_mtx);
            auto it = g_loc_found.find(loc);
            if (it != g_loc_found.end()) found = it->second;
            auto il = g_loc_local_id.find(loc);
            if (il != g_loc_local_id.end()) local_id = il->second;
            auto in = g_loc_item_name.find(loc);
            if (in != g_loc_item_name.end()) name = in->second;
        }
        if (!found.empty()) overlay::push_item(found);
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
    if (sd.contains("suppress_items")) {
        for (auto& v : sd["suppress_items"]) {
            int i = v.get<int>();
            if (i >= 0 && i < 0x200) { g_supp_item[i] = true; supp++; }
        }
    }
    std::list<int64_t> scout;
    int scenes = 0;
    // Reset detect registrations (a reconnect re-registers everything; without
    // this a flag would accumulate duplicate location entries).
    for (int i = 0; i < 0x200; i++) g_flag_to_loc[i].clear();
    g_poll_bits.clear();
    g_poll_vals.clear();
    g_poll_floors.clear();
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
                    g_flag_to_loc[idx].push_back(loc);
                } else {
                    // Outside g_flags (e.g. the armor blessing cell): the VM
                    // store hook can't see it — poll for value >= 1 instead.
                    g_poll_vals.push_back({kImageBase + (uintptr_t)off, loc});
                }
                scout.push_back(loc);
                locs++;
            } else if (method == "bit" && d.contains("offset") && d.contains("bit")) {
                // Blessing purchases: one bitfield cell, one bit per blessing.
                // Written natively by the shop menu -> poll, don't hook.
                int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
                g_poll_bits.push_back({kImageBase + (uintptr_t)off,
                                       d["bit"].get<int>(), loc});
                scout.push_back(loc);
                locs++;
            } else if (method == "floor" && d.contains("offset") && d.contains("floor")) {
                // "Reach NF": fires once current_floor reaches N (native write).
                int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
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
        // Registry byte index = position in scene order (live-confirmed pairing).
        std::sort(tmp.begin(), tmp.end(),
                  [](const StatueReg& a, const StatueReg& b) { return a.scene < b.scene; });
        g_statue_reg.clear();
        for (int i = 0; i < (int)tmp.size(); i++)
            g_statue_reg.push_back({i, tmp[i].flag_idx, tmp[i].scene});
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
    g_start_items.clear();
    if (sd.contains("start_items"))
        for (auto& v : sd["start_items"])
            g_start_items.push_back(v.get<int>());
    g_sp_items.clear();
    if (sd.contains("sp_items"))
        for (auto& kv : sd["sp_items"].items())
            g_sp_items[kv.key()] = kv.value().get<int>();
    g_sp_flag_idx = sd.value("sp_flag_idx", 0xD8);
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
    for (const auto& it : items) {
        if (it.index <= g_applied_through) continue;  // already applied this run
        std::string name = g_ap->get_item_name(it.item, AP_GAME);
        std::string from = g_ap->get_player_alias(it.player);
        auto su = g_statue_item_idx.find(name);
        auto f = g_name_to_idx.find(name);
        auto sp = g_sp_items.find(name);
        auto pg = g_prog_gear.find(name);
        if (sp != g_sp_items.end()) {
            // SP filler: add straight to the SP currency cell (give semantics).
            ap_give(g_sp_flag_idx, sp->second);
        } else if (pg != g_prog_gear.end()) {
            // Progressive gear: grant the first unowned tier in the ladder.
            bool granted = false;
            for (int idx : pg->second)
                if (idx >= 0 && idx < 0x200 &&
                    *(volatile int*)(kGFlagsAbs + idx * 4) < 1) {
                    ap_give(idx, 1);
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
            int tier = kWeaponTier[(n < 5 ? n : 5) - 1];
            g_pending_weapon.store(tier);
            mod_log("ap: Cleria Ore #%d -> weapon tier value %d (pending)", n, tier);
        } else if (f != g_name_to_idx.end())
            ap_give(f->second, 1);
        else
            mod_log("ap: received '%s' (id %lld) — no g_flags index, skipped",
                    name.c_str(), (long long)it.item);
        // Your own items already print as "Found: X (yours)" when their location
        // fires (ap_on_check), so only surface items coming FROM another player
        // (or the server) here — avoids showing every self-item twice.
        if (from != g_slot)
            overlay::push_item(from.empty() ? name : (name + "  <- " + from));
        g_applied_through = it.index;
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
    g_last_scene = scene;

    // New-Game intro: Hugo/Yunica play scene 2; Toal plays his own cutscenes in
    // the 7xxx range (e.g. S_7001, the goddesses) — gameplay scenes are 1000-6151
    // so 7xxx is a safe Toal-intro marker. Arm force-spawn; the warp fires on the
    // main thread as soon as a player entity exists (skipping the rest). Fires once
    // per session (g_force_spawn_done not re-armed here, so a 7xxx cutscene playing
    // again mid-game can't re-warp the player).
    if (scene == 2 || (scene >= 7000 && scene < 8000)) {
        g_saw_intro.store(true);
        g_expected_hi = 0;   // New Game: forget the last run's deepest floor
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
    for (int64_t loc : fire) {
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        auto f = g_loc_found.find(loc);
        if (f != g_loc_found.end()) overlay::push_item(f->second);
    }
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
    for (int64_t loc : fire) {
        std::lock_guard<std::mutex> lk(g_scout_mtx);
        auto f = g_loc_found.find(loc);
        if (f != g_loc_found.end()) overlay::push_item(f->second);
    }
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
    std::map<uintptr_t, int> cur_floor;
    for (const auto& pf : g_poll_floors)
        cur_floor[pf.abs] = *(volatile int*)pf.abs;
    for (const auto& pf : g_poll_floors) {
        int cur = cur_floor[pf.abs];
        auto pit = g_floor_prev.find(pf.abs);
        int prev = (pit != g_floor_prev.end()) ? pit->second : cur;  // prime
        if (cur >= pf.floor_n && pf.floor_n > prev &&
            g_poll_fired.insert(pf.loc).second)
            fire.push_back(pf.loc);
    }
    for (const auto& kv : cur_floor) g_floor_prev[kv.first] = kv.second;
    fire_poll_locs(fire);
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
static void apply_weapon_level(int tier) {
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
    g_weapon_applied = tier;
    mod_log("weapon: applied tier value %d (entity %s)", tier, ent ? "pushed" : "null");
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
    bool spawn_seed = g_start_statue_scene > 0 && g_start_statue_scene != 1000;
    bool manual = g_warp_request.exchange(false);
    bool auto_intro = !g_force_spawn_done.load() && g_saw_intro.load() &&
                      read_current_scene() >= 2 && *kPlayerEntPtr != nullptr;
    if (spawn_seed && (manual || auto_intro)) {
        g_force_spawn_done.store(true);
        force_spawn();
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
        for (int idx : g_start_items)
            if (idx >= 0 && idx < 0x200 &&
                *(volatile int*)(kGFlagsAbs + idx * 4) < 1)
                *(volatile int*)(kGFlagsAbs + idx * 4) = 1;
    }

    // Weapon upgrade (Cleria Ore). Re-enforce if a save load reset g_flags[0x94]
    // below what we applied (the save reloads g_flags from the on-disk state).
    int wv = g_pending_weapon.exchange(0);
    if (wv > g_weapon_applied) g_weapon_applied = wv;
    // Re-apply when: a new tier arrived (wv), the persistent record dropped (save
    // load), OR the player ENTITY changed (death/respawn or a scene reload spawns
    // a fresh entity whose combat weapon stats default to Lv1 even though the
    // record still reads Lv5). The last case is what makes post-respawn deal 1 dmg.
    char* cur_ent = *kPlayerEntPtr;
    bool ent_changed = (cur_ent != nullptr && cur_ent != g_weapon_entity);
    if (g_weapon_applied > 0 &&
        (wv > 0 || ent_changed || *(volatile int*)kWeaponLevelAbs < g_weapon_applied)) {
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
                mod_log("ap: sent %d LocationCheck(s)", (int)locs.size());
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
    load_config();               // prefill defaults for the in-game menu
    overlay::set_status("not connected - open the Archipelago menu");

    g_run.store(true);
    g_thread = std::thread(poll_loop);
    g_thread.detach();  // let the process exit cleanly (no join at teardown)
    mod_log("ap: poll thread started (autoconnect=%d)", (int)g_autoconnect);

    if (g_autoconnect)
        ap_request_connect(g_host, g_port, g_slot, g_pass);
}
