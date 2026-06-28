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
#include <string>
#include <thread>
#include <vector>

void mod_log(const char* fmt, ...);

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
    }
    fclose(f);
    mod_log("ap: config host=%s port=%d slot=%s", g_host, g_port, g_slot);
}

static const uintptr_t kGFlagsAbs = 0x0076B91C;  // runtime g_flags base
static const int kGFlagsRel = 0x0036B91C;        // module-relative (slot_data offsets)

static APClient* g_ap = nullptr;
static std::thread g_thread;
static std::atomic<bool> g_run{false};

// flag index -> AP location id (set in slot_connected, read in ap_on_check).
static int64_t g_flag_to_loc[0x200];
// AP item name -> g_flags index (from slot_data item_index).
static std::map<std::string, int> g_name_to_idx;
// queued AP location ids to check (VM hook thread -> poll thread).
static std::mutex g_check_mtx;
static std::vector<int64_t> g_checks;
// highest received-item index already granted (dedupe replays this session).
static int g_applied_through = -1;

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
// fires. Queue its AP location id; the poll loop sends the LocationCheck.
void ap_on_check(int flag_idx) {
    if (flag_idx < 0 || flag_idx >= 0x200) return;
    int64_t loc = g_flag_to_loc[flag_idx];
    if (loc < 0) return;
    std::lock_guard<std::mutex> lk(g_check_mtx);
    g_checks.push_back(loc);
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
    if (sd.contains("location_detect")) {
        const auto& sig = sd.contains("location_signals") ? sd["location_signals"]
                                                          : nlohmann::json::object();
        for (auto& kv : sd["location_detect"].items()) {
            const auto& d = kv.value();
            if (d.value("method", std::string()) != "flag" || !d.contains("offset"))
                continue;
            int off = (int)strtol(d["offset"].get<std::string>().c_str(), nullptr, 16);
            int idx = (off - kGFlagsRel) / 4;
            if (idx < 0 || idx >= 0x200) continue;
            if (!sig.contains(kv.key())) continue;
            g_loc_flag[idx] = true;
            g_flag_to_loc[idx] = sig[kv.key()].get<int64_t>();
            locs++;
        }
    }
    mod_log("ap: slot_connected — %d items, %d suppress, %d location flags",
            names, supp, locs);
}

static void on_items_received(const std::list<APClient::NetworkItem>& items) {
    for (const auto& it : items) {
        if (it.index <= g_applied_through) continue;  // already applied this run
        std::string name = g_ap->get_item_name(it.item, AP_GAME);
        auto f = g_name_to_idx.find(name);
        if (f != g_name_to_idx.end())
            ap_give(f->second, 1);
        else
            mod_log("ap: received '%s' (id %lld) — no g_flags index, skipped",
                    name.c_str(), (long long)it.item);
        g_applied_through = it.index;
    }
}

static void poll_loop() {
    while (g_run.load()) {
        if (g_ap) {
            try { g_ap->poll(); } catch (...) {}
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

    g_ap->set_socket_connected_handler([]() { mod_log("ap: socket connected"); });
    g_ap->set_socket_disconnected_handler([]() { mod_log("ap: socket disconnected"); });
    g_ap->set_room_info_handler([]() {
        mod_log("ap: room_info -> ConnectSlot(%s)", g_slot);
        g_ap->ConnectSlot(g_slot, g_pass, 0b111);
    });
    g_ap->set_slot_connected_handler(on_slot_connected);
    g_ap->set_items_received_handler(on_items_received);

    g_run.store(true);
    g_thread = std::thread(poll_loop);
    g_thread.detach();  // let the process exit cleanly (no join at teardown)
    mod_log("ap: poll thread started");
}
