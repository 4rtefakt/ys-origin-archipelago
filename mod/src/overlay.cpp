// The in-game overlay UI. Shows AP connection status and a live "recent items"
// feed pushed from the AP client (hook_ap.cpp), plus a terminal stub (later:
// AP commands). Drawn from the D3D9 EndScene hook (render thread); the shared
// buffers are mutex-guarded since the AP client thread writes them.
#include "imgui.h"

#include <deque>
#include <mutex>
#include <string>

extern ImFont* g_overlay_big_font;  // large item-name font (hook_d3d9.cpp)

namespace overlay {

static std::mutex g_mtx;
static std::deque<std::string> g_items;   // most-recent appended at the back
static std::string g_status = "connecting...";
static std::string g_room;                // current room (scene), from hook_ap

// -- called from the AP client thread -------------------------------------- #

void push_item(const std::string& text) {
    std::lock_guard<std::mutex> lk(g_mtx);
    g_items.push_back(text);
    while (g_items.size() > 20) g_items.pop_front();
}

void set_status(const std::string& text) {
    std::lock_guard<std::mutex> lk(g_mtx);
    g_status = text;
}

void set_room(const std::string& text) {
    std::lock_guard<std::mutex> lk(g_mtx);
    g_room = text;
}

std::string get_status() {
    std::lock_guard<std::mutex> lk(g_mtx);
    return g_status;
}

// -- drawn from the render thread ------------------------------------------ #

// Draw one right-aligned line as floating HUD text at a given font/size: a black
// outline (4-way) for readability over any background, then the colored text.
static void hud_line(ImDrawList* dl, ImFont* font, float size, float right_x,
                     float y, ImU32 col, const char* text) {
    ImVec2 sz = font->CalcTextSizeA(size, FLT_MAX, 0.0f, text);
    ImVec2 p(right_x - sz.x, y);
    const ImU32 shadow = IM_COL32(0, 0, 0, 220);
    const float o = (size > 24.0f) ? 2.0f : 1.0f;  // thicker outline for big text
    dl->AddText(font, size, ImVec2(p.x - o, p.y), shadow, text);
    dl->AddText(font, size, ImVec2(p.x + o, p.y), shadow, text);
    dl->AddText(font, size, ImVec2(p.x, p.y - o), shadow, text);
    dl->AddText(font, size, ImVec2(p.x, p.y + o), shadow, text);
    dl->AddText(font, size, p, col, text);
}

// Render the AP status + recent-items feed as part of the game's HUD: no window,
// no chrome — just outlined text floating top-right, drawn straight onto the
// frame via the foreground draw list. Item names use the large (~5x) font.
// (INSERT still toggles it; the toggle gates this call in the D3D9 hook.)
void draw() {
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    const float right = io.DisplaySize.x - 18.0f;

    ImFont* small = ImGui::GetFont();         // default UI font
    const float ssz = ImGui::GetFontSize();
    const float slh = ImGui::GetTextLineHeightWithSpacing();
    ImFont* big = g_overlay_big_font ? g_overlay_big_font : small;
    const float bsz = 38.0f;                  // ~3x the default ~13px
    const float blh = bsz * 1.1f;

    const ImU32 gold = IM_COL32(228, 196, 112, 255);  // Ys UI gold
    const ImU32 white = IM_COL32(245, 245, 245, 255);
    const ImU32 dim = IM_COL32(170, 170, 170, 255);

    std::lock_guard<std::mutex> lk(g_mtx);
    float y = 16.0f;
    hud_line(dl, small, ssz, right, y, gold, "Archipelago");
    y += slh;
    hud_line(dl, small, ssz, right, y, dim, g_status.c_str());
    y += slh;
    if (!g_room.empty()) {
        hud_line(dl, small, ssz, right, y, dim, g_room.c_str());
        y += slh;
    }
    y += slh * 0.4f;

    int n = 0;
    for (auto it = g_items.rbegin(); it != g_items.rend() && n < 5; ++it, ++n) {
        hud_line(dl, big, bsz, right, y, white, it->c_str());
        y += blh;
    }
}

}  // namespace overlay
