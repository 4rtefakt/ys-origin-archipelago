// In-game "Archipelago" main-menu entry + connect submenu.
//
// The game's title menu (New Game / Load Game / Bonus Modes / Options / Exit) is
// an engine-native sprite state machine we deliberately don't mutate. Instead we
// draw our own entry ABOVE "New Game" and a connect form, both rendered in the
// game's own font via the D3D9 EndScene hook (no ImGui window chrome). Input is
// captured from the window messages the game already delivers; while the form is
// open the game's own input is frozen (hook_input.cpp) so nothing reacts behind
// it. On Connect we hand the settings to hook_ap.cpp, which (re)builds the client
// on its poll thread.
//
// "At the title" is detected by current_scene == 0 (g_flags[0x1F9] @ 0x76c100).
#include <windows.h>
#include <cstdio>
#include <string>
#include "imgui.h"

extern ImFont* g_overlay_big_font;                 // game TTF (hook_d3d9.cpp)
void mod_log(const char* fmt, ...);
void ap_request_connect(const char* host, int port, const char* slot, const char* pass);
const char* ap_cfg_host(); int ap_cfg_port();
const char* ap_cfg_slot(); const char* ap_cfg_pass();
namespace overlay { std::string get_status(); }

namespace apmenu {

// ---- tunable layout (fractions of the display; override in yso_ap.cfg) ------
static float g_cx   = 0.5f;    // horizontal center
static float g_ng_y = 0.728f;  // "New Game" line, as a fraction of height
static float g_line = 0.060f;  // gap above New Game to our entry (frac of height)
static float g_size = 50.0f;   // menu font pixel size
static bool  g_cfg_loaded = false;

static void load_cfg() {
    g_cfg_loaded = true;
    FILE* f = fopen("yso_ap.cfg", "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == ';') continue;
        char* eq = strchr(line, '='); if (!eq) continue;
        *eq = 0; const char* k = line; const char* v = eq + 1;
        if      (!strcmp(k, "menu_cx"))   g_cx   = (float)atof(v);
        else if (!strcmp(k, "menu_y"))    g_ng_y = (float)atof(v);
        else if (!strcmp(k, "menu_line")) g_line = (float)atof(v);
        else if (!strcmp(k, "menu_size")) g_size = (float)atof(v);
    }
    fclose(f);
}

// ---- state ------------------------------------------------------------------
enum Field { F_HOST, F_PORT, F_SLOT, F_PASS, F_COUNT };
static const char* kLabels[F_COUNT] = {"Server", "Port", "Slot / Name", "Password"};
static bool        g_open = false;
static bool        g_prefilled = false;
static int         g_focus = F_HOST;
static std::string g_fields[F_COUNT];

// "At the main title list" = current_scene == 0 (not in a map) AND the task /
// modal-stack counter == 0 (no sub-screen pushed). Character Select, Options,
// etc. are all scene 0 but push a task (counter -> 1), so this excludes them.
static const uintptr_t kSceneAbs     = 0x0076C100;  // current_scene
static const uintptr_t kTaskDepthAbs = 0x00730294;  // title task/modal-stack count
static bool at_title() {
    return *(volatile int*)kSceneAbs == 0 && *(volatile int*)kTaskDepthAbs == 0;
}

static void prefill_once() {
    if (g_prefilled) return;
    g_prefilled = true;
    // Show the bare hostname — the wss:// scheme is implied (re-added on connect).
    std::string h = ap_cfg_host();
    size_t s = h.find("://");
    if (s != std::string::npos) h = h.substr(s + 3);
    if (h.empty()) h = "archipelago.gg";
    g_fields[F_HOST] = h;
    char portbuf[16]; snprintf(portbuf, sizeof(portbuf), "%d", ap_cfg_port());
    g_fields[F_PORT] = portbuf;
    g_fields[F_SLOT] = ap_cfg_slot();
    g_fields[F_PASS] = ap_cfg_pass();
}

static void do_connect() {
    int port = atoi(g_fields[F_PORT].c_str());
    // Default to secure wss:// (public servers require TLS). A user can still
    // force plain ws:// (e.g. localhost) by typing the scheme explicitly.
    std::string host = g_fields[F_HOST];
    if (host.find("://") == std::string::npos) host = "wss://" + host;
    ap_request_connect(host.c_str(), port,
                       g_fields[F_SLOT].c_str(), g_fields[F_PASS].c_str());
}

// ---- input (called from the WndProc subclass in hook_d3d9.cpp) --------------
bool is_capturing() { return g_open; }

// Returns true if the key was consumed (menu handled it). Only active at title.
bool on_wm_key(UINT msg, WPARAM wp) {
    if (msg != WM_KEYDOWN && msg != WM_SYSKEYDOWN) return false;
    if (!at_title()) { g_open = false; return false; }
    if (!g_open) {
        if (wp == VK_F8) { prefill_once(); g_open = true; g_focus = F_HOST; return true; }
        return false;
    }
    switch (wp) {
        case VK_F8: case VK_ESCAPE:  g_open = false; return true;
        case VK_F5: case VK_RETURN:  do_connect(); return true;
        case VK_TAB:
        case VK_DOWN: g_focus = (g_focus + 1) % F_COUNT; return true;
        case VK_UP:   g_focus = (g_focus - 1 + F_COUNT) % F_COUNT; return true;
        case VK_BACK:
            if (!g_fields[g_focus].empty()) g_fields[g_focus].pop_back();
            return true;
    }
    return true;  // swallow everything else while open (text arrives via WM_CHAR)
}

bool on_wm_char(WPARAM ch) {
    if (!g_open || !at_title()) return false;
    if (ch < 0x20 || ch > 0x7e) return true;   // printable ASCII only
    if (g_focus == F_PORT && (ch < '0' || ch > '9')) return true;  // port = digits
    if (g_fields[g_focus].size() < 120) g_fields[g_focus].push_back((char)ch);
    return true;
}

// ---- rendering (called from EndScene, main thread) --------------------------
static void text_centered(ImDrawList* dl, ImFont* f, float size, float cx,
                          float y, ImU32 col, const char* s) {
    ImVec2 sz = f->CalcTextSizeA(size, FLT_MAX, 0.0f, s);
    ImVec2 p(cx - sz.x * 0.5f, y);
    const ImU32 sh = IM_COL32(0, 0, 0, 210);
    dl->AddText(f, size, ImVec2(p.x - 2, p.y), sh, s);
    dl->AddText(f, size, ImVec2(p.x + 2, p.y), sh, s);
    dl->AddText(f, size, ImVec2(p.x, p.y - 2), sh, s);
    dl->AddText(f, size, ImVec2(p.x, p.y + 2), sh, s);
    dl->AddText(f, size, p, col, s);
}

void draw() {
    if (!g_cfg_loaded) load_cfg();
    if (!at_title()) { g_open = false; return; }

    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    const float W = io.DisplaySize.x, H = io.DisplaySize.y;
    const float cx = W * g_cx;
    ImFont* f = g_overlay_big_font ? g_overlay_big_font : ImGui::GetFont();

    const ImU32 gold = IM_COL32(232, 202, 120, 255);
    const ImU32 gray = IM_COL32(200, 205, 210, 235);
    const ImU32 dim  = IM_COL32(150, 155, 160, 220);

    // The "Archipelago" entry, one line above New Game.
    float ap_y = H * (g_ng_y - g_line);
    if (!g_open)  // small hint above the entry (kept clear of the New Game frame)
        text_centered(dl, f, g_size * 0.40f, cx, ap_y - g_size * 0.5f, dim,
                      "press  F8");
    text_centered(dl, f, g_size, cx, ap_y, g_open ? gold : gray, "Archipelago");

    if (!g_open) return;

    // Connect form: dim the screen, then draw the fields as native text lines.
    dl->AddRectFilled(ImVec2(0, 0), ImVec2(W, H), IM_COL32(0, 0, 0, 150));
    float y = H * 0.30f;
    const float fs = g_size;
    const float lh = fs * 1.5f;
    text_centered(dl, f, fs * 1.15f, cx, y, gold, "Archipelago  \xE2\x80\x94  Connect");
    y += lh * 1.4f;
    for (int i = 0; i < F_COUNT; i++) {
        char row[256];
        std::string shown = g_fields[i];
        if (i == F_PASS) shown = std::string(g_fields[i].size(), '*');
        const char* cursor = (i == g_focus) ? "_" : "";
        snprintf(row, sizeof(row), "%s%s:  %s%s", (i == g_focus ? "> " : "  "),
                 kLabels[i], shown.c_str(), cursor);
        text_centered(dl, f, fs, cx, y, (i == g_focus) ? gold : gray, row);
        y += lh;
    }
    y += lh * 0.5f;
    text_centered(dl, f, fs * 0.85f, cx, y, gold,
                  "[Enter/F5] Connect     [Tab/Up/Down] Field     [Esc] Close");
    y += lh;
    std::string st = "Status: " + overlay::get_status();
    text_centered(dl, f, fs * 0.7f, cx, y, dim, st.c_str());
}

}  // namespace apmenu
