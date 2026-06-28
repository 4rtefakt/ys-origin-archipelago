// Self-contained Archipelago client embedded in the mod (apclientpp).
//
// Replaces the external Python client: the mod connects to the AP server
// directly over ws:// (local play; WSWRAP_NO_SSL so no OpenSSL). Step 1 here is
// just connect + log to validate the build and networking; the slot_data ->
// suppress/detect wiring and items -> grant come next.
//
// apclientpp + asio + websocketpp + nlohmann/json are header-only; build knobs
// are set in CMakeLists (ASIO_STANDALONE, _WEBSOCKETPP_CPP11_STL_, WSWRAP_NO_SSL,
// WIN32_LEAN_AND_MEAN, _WIN32_WINNT). Include apclient.hpp FIRST (it pulls in
// asio/winsock2) — this TU avoids <windows.h>; the poll loop uses std::thread.
#include <apclient.hpp>

#include <atomic>
#include <chrono>
#include <list>
#include <thread>

void mod_log(const char* fmt, ...);

static const char* AP_GAME = "Ys Origin";
static const char* AP_SLOT = "Hugo";
static const char* AP_URI = "ws://127.0.0.1:38281";

static APClient* g_ap = nullptr;
static std::thread g_thread;
static std::atomic<bool> g_run{false};

static void poll_loop() {
    while (g_run.load()) {
        if (g_ap) {
            try { g_ap->poll(); } catch (...) {}
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

void ap_install() {
    mod_log("ap: creating client (game=%s uri=%s)", AP_GAME, AP_URI);
    g_ap = new APClient("YsOrigin-Mod", AP_GAME, AP_URI);

    g_ap->set_socket_connected_handler([]() {
        mod_log("ap: socket connected");
    });
    g_ap->set_socket_disconnected_handler([]() {
        mod_log("ap: socket disconnected");
    });
    g_ap->set_room_info_handler([]() {
        mod_log("ap: room_info -> ConnectSlot(%s)", AP_SLOT);
        g_ap->ConnectSlot(AP_SLOT, "", 0b111);  // full remote items
    });
    g_ap->set_slot_connected_handler([](const nlohmann::json& slot_data) {
        mod_log("ap: SLOT CONNECTED (slot_data %d keys)", (int)slot_data.size());
    });
    g_ap->set_items_received_handler(
        [](const std::list<APClient::NetworkItem>& items) {
            mod_log("ap: received %d item(s)", (int)items.size());
            for (const auto& it : items)
                mod_log("ap:   item=%lld loc=%lld player=%d",
                        (long long)it.item, (long long)it.location, it.player);
        });

    g_run.store(true);
    g_thread = std::thread(poll_loop);
    mod_log("ap: poll thread started");
}
