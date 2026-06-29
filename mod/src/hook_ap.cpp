// Self-contained Archipelago client embedded in the mod (apclientpp).
//
// Replaces the external Python client: the mod connects to the AP server
// directly over ws:// (local play; WSWRAP_NO_SSL so no OpenSSL). It drives the
// in-game randomizer end to end:
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

static const char* AP_GAME = "Ys Origin";

// Connection settings — overridable via `yso_ap.cfg` next to the game exe
// (key=value lines: host, port, slot, password). Defaults suit local play.
static char g_host[128] = "127.0.0.1";
static int  g_port = 38281;
static char g_slot[128] = "Hugo";
static char g_pass[128] = "";
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
                  "# Edit and relaunch the game. host may include a scheme\n"
                  "# (ws:// or wss://); default is ws:// for local play.\n"
                  "host=127.0.0.1\nport=38281\nslot=Hugo\npassword=\n", w);
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

// flag index -> AP location id (set in slot_connected, read in ap_on_check).
static int64_t g_flag_to_loc[0x200];
// scene-method detection: scene number -> AP location ids fired on entering it
// (boss arenas, room-sanity checks); scene number -> room name for the overlay.
static std::map<int, std::vector<int64_t>> g_scene_locs;
static std::map<int, std::string> g_scene_name;
static std::set<int64_t> g_scene_fired;   // dedupe (each scene loc fires once)
static int g_last_scene = -1;

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
    int64_t loc = g_flag_to_loc[flag_idx];
    if (loc < 0) return;
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
    if (!found.empty()) overlay::push_item("Found: " + found);
    // Stash the actually-placed item so the native "Acquired <item>" box (the
    // 0xD5 op, which runs just after this check flag) shows the REAL item: its
    // art (local id, or a generic icon for foreign) and its name text.
    int art = (local_id >= 0) ? local_id : kForeignArtId;
    set_pending_box(art, name.c_str());
}

// Reply to LocationScouts: learn the item + recipient at each of our locations.
static void on_location_info(const std::list<APClient::NetworkItem>& items) {
    std::lock_guard<std::mutex> lk(g_scout_mtx);
    for (const auto& it : items) {
        std::string game = g_ap->get_player_game(it.player);
        std::string item = g_ap->get_item_name(it.item, game);
        std::string who = g_ap->get_player_alias(it.player);
        g_loc_found[it.location] =
            (who == g_slot) ? (item + "  (yours)") : (item + "  -> " + who);
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
                if (idx < 0 || idx >= 0x200) continue;
                g_loc_flag[idx] = true;
                g_flag_to_loc[idx] = loc;
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
        auto f = g_name_to_idx.find(name);
        if (f != g_name_to_idx.end())
            ap_give(f->second, 1);
        else
            mod_log("ap: received '%s' (id %lld) — no g_flags index, skipped",
                    name.c_str(), (long long)it.item);
        overlay::push_item(from.empty() || from == g_slot
                               ? name : (name + "  <- " + from));
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
        if (f != g_loc_found.end()) overlay::push_item("Found: " + f->second);
    }
}

static void poll_loop() {
    while (g_run.load()) {
        if (g_ap) {
            try { g_ap->poll(); } catch (...) {}
            poll_scene();
            poll_deathlink();
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

void ap_install() {
    for (int i = 0; i < 0x200; i++) g_flag_to_loc[i] = -1;
    load_config();
    if (strstr(g_host, "://"))
        snprintf(g_uri, sizeof(g_uri), "%s:%d", g_host, g_port);
    else
        snprintf(g_uri, sizeof(g_uri), "ws://%s:%d", g_host, g_port);
    mod_log("ap: creating client (game=%s uri=%s)", AP_GAME, g_uri);
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

    g_run.store(true);
    g_thread = std::thread(poll_loop);
    g_thread.detach();  // let the process exit cleanly (no join at teardown)
    mod_log("ap: poll thread started");
}
