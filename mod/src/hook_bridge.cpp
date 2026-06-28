// Localhost socket bridge between the mod (in-game) and the Python AP client.
//
// The Python client owns the Archipelago network connection (connect, receive
// items, send location checks). The mod owns in-game grant interception. This
// bridge connects them over 127.0.0.1 with a tiny line protocol:
//
//   Mod  -> Py :  "G <idx> <val>"   a g_flags grant happened (idx hex, val dec)
//                 "C <flag>"        a registered randomized-location flag fired
//   Py   -> Mod:  "L <flag>"        register <flag> (hex) as a randomized loc
//                 "I <idx>"         register <idx> (hex) as a vanilla item to
//                                   suppress (a randomized location's content)
//                 "U"               unregister all (e.g. on AP reconnect)
//                 "V <idx> <count>" grant a received AP item (give semantics)
//
// The mod is the server (it outlives client reconnects); Python connects with
// retry. One client at a time. Winsock; runs on its own thread.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <deque>
#include <string>
#include <mutex>

#pragma comment(lib, "ws2_32.lib")

void mod_log(const char* fmt, ...);

static const u_short kPort = 43673;
static const uintptr_t kGFlagsBase = 0x0076B91C;

// --- shared state read by the VM hook (hook_vm.cpp) ------------------------- #
// Registered randomized-location flags (g_flags index < 0x200): the VM hook
// emits a check when one fires. And vanilla item indices to suppress (a
// randomized location's content — the player gets the AP item via 'V' instead).
bool g_loc_flag[0x200] = {false};
bool g_supp_item[0x200] = {false};

// Outgoing line queue (VM hook -> socket thread).
static std::mutex g_out_mtx;
static std::deque<std::string> g_out;
static volatile bool g_connected = false;

// Called from the VM hook (game main thread). Lock-light enqueue.
void bridge_emit(const char* line) {
    if (!g_connected) return;  // nobody listening; drop
    std::lock_guard<std::mutex> lk(g_out_mtx);
    if (g_out.size() < 4096) g_out.emplace_back(line);
}

// Grant a received AP item directly in g_flags (give semantics: -1 -> 1, else
// +count). Single int32 writes are atomic on x86; safe to do from this thread.
static void give_item(int idx, int count) {
    if (idx < 0 || idx >= 0x200) return;
    volatile int* cell = (volatile int*)(kGFlagsBase + idx * 4);
    int cur = *cell;
    int base = (cur >= 1) ? cur : 0;   // treat -1 ("never") as 0
    *cell = base + (count > 0 ? count : 1);
    mod_log("bridge: gave g_flags[0x%X] %d -> %d", idx, cur, *cell);
}

static void handle_line(const std::string& s) {
    if (s.empty()) return;
    char cmd = s[0];
    if (cmd == 'L') {
        int f = (int)strtol(s.c_str() + 1, nullptr, 16);
        if (f >= 0 && f < 0x200) g_loc_flag[f] = true;
    } else if (cmd == 'I') {
        int f = (int)strtol(s.c_str() + 1, nullptr, 16);
        if (f >= 0 && f < 0x200) g_supp_item[f] = true;
    } else if (cmd == 'U') {
        for (int i = 0; i < 0x200; i++) { g_loc_flag[i] = false; g_supp_item[i] = false; }
        mod_log("bridge: unregistered all flags");
    } else if (cmd == 'V') {
        int idx = 0, cnt = 1;
        sscanf(s.c_str() + 1, "%x %d", &idx, &cnt);
        give_item(idx, cnt);
    }
}

static void serve_client(SOCKET cs) {
    g_connected = true;
    mod_log("bridge: client connected");
    // Non-blocking so we can both send queued lines and read commands.
    u_long nb = 1; ioctlsocket(cs, FIONBIO, &nb);
    std::string inbuf;
    char rx[512];
    for (;;) {
        // drain outgoing
        for (;;) {
            std::string line;
            {
                std::lock_guard<std::mutex> lk(g_out_mtx);
                if (g_out.empty()) break;
                line = std::move(g_out.front());
                g_out.pop_front();
            }
            line.push_back('\n');
            int sent = send(cs, line.data(), (int)line.size(), 0);
            if (sent == SOCKET_ERROR) {
                if (WSAGetLastError() == WSAEWOULDBLOCK) { /* requeue */ }
                else goto done;
            }
        }
        // read incoming
        int n = recv(cs, rx, sizeof(rx), 0);
        if (n == 0) goto done;                 // peer closed
        if (n == SOCKET_ERROR) {
            int e = WSAGetLastError();
            if (e != WSAEWOULDBLOCK) goto done;
        } else {
            inbuf.append(rx, n);
            size_t pos;
            while ((pos = inbuf.find('\n')) != std::string::npos) {
                std::string line = inbuf.substr(0, pos);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                handle_line(line);
                inbuf.erase(0, pos + 1);
            }
        }
        Sleep(2);  // ~500 Hz; cheap, keeps grant latency low
    }
done:
    g_connected = false;
    closesocket(cs);
    mod_log("bridge: client disconnected");
}

static DWORD WINAPI bridge_thread(LPVOID) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        mod_log("bridge: WSAStartup failed");
        return 0;
    }
    SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (ls == INVALID_SOCKET) { mod_log("bridge: socket failed"); return 0; }
    BOOL reuse = TRUE;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (char*)&reuse, sizeof(reuse));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(kPort);
    InetPtonA(AF_INET, "127.0.0.1", &addr.sin_addr);
    if (bind(ls, (sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR) {
        mod_log("bridge: bind failed (port %d in use?)", kPort);
        closesocket(ls); return 0;
    }
    listen(ls, 1);
    mod_log("bridge: listening on 127.0.0.1:%d", kPort);
    for (;;) {
        SOCKET cs = accept(ls, nullptr, nullptr);
        if (cs == INVALID_SOCKET) { Sleep(100); continue; }
        serve_client(cs);   // blocks until that client disconnects
    }
}

void bridge_install() {
    CreateThread(nullptr, 0, bridge_thread, nullptr, 0, nullptr);
}
