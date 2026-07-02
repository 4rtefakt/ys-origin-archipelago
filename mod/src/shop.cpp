// The overlay blessing shop (blessing_costs: random).
//
// Our own shop UI, independent of the game's statue menu: lists the blessings
// at the seed's randomized SP prices together with the multiworld item each
// one holds (scouted; colored by classification), and buys entirely through
// the mod — deduct SP, set the purchase bit (the bit poll fires the check),
// grant the effect. Backing data + purchase logic live in hook_ap.cpp
// (ap_shop_*); this file is only the F5 panel: keys + drawing.
//
//   F5        toggle (only when the seed has randomized costs and we're online)
//   Up/Down   select        Enter   buy        Esc/F5   close
//
// While open the game's input is frozen (hook_input checks
// apshop::is_capturing(), same as the connect menu and the chat input).
#include "imgui.h"

#include <cfloat>
#include <string>

void mod_log(const char* fmt, ...);
bool ap_shop_available();
int ap_shop_count();
std::string ap_shop_status();
std::string ap_shop_line(int i);
bool ap_shop_buy(int i);

namespace apshop {

static bool g_open = false;
static int g_cursor = 0;

bool is_capturing() { return g_open; }

bool on_wm_key(unsigned msg, unsigned long long wp) {
    (void)msg;
    if (wp == 0x74) {                       // VK_F5: toggle
        if (!g_open && !ap_shop_available()) return false;
        g_open = !g_open;
        return true;
    }
    if (!g_open) return false;
    int n = ap_shop_count();
    switch (wp) {
        case 0x1B: g_open = false; return true;              // Esc
        case 0x26: if (n) g_cursor = (g_cursor + n - 1) % n; return true;  // Up
        case 0x28: if (n) g_cursor = (g_cursor + 1) % n; return true;      // Down
        case 0x0D: ap_shop_buy(g_cursor); return true;       // Enter
        default:   return true;             // swallow the rest while open
    }
}

// Drawn from the D3D9 EndScene hook, inside the ImGui frame. A CENTERED panel
// styled like the game's own blessing dialog (translucent blue frame + border)
// so it reads as the in-game shop rather than a debug overlay.
void draw() {
    if (!g_open) return;
    if (!ap_shop_available()) { g_open = false; return; }
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    ImFont* font = ImGui::GetFont();
    const float sz = ImGui::GetFontSize();
    const float lh = ImGui::GetTextLineHeightWithSpacing();
    const ImU32 shadow = IM_COL32(0, 0, 0, 235);
    const ImU32 white = IM_COL32(235, 235, 235, 255);
    const ImU32 gold = IM_COL32(228, 196, 112, 255);
    const ImU32 red = IM_COL32(235, 110, 110, 255);
    const ImU32 dim = IM_COL32(150, 156, 175, 220);

    auto text_at = [&](float x, float y, ImU32 col, const char* t) {
        dl->AddText(font, sz, ImVec2(x - 1, y), shadow, t);
        dl->AddText(font, sz, ImVec2(x + 1, y), shadow, t);
        dl->AddText(font, sz, ImVec2(x, y - 1), shadow, t);
        dl->AddText(font, sz, ImVec2(x, y + 1), shadow, t);
        dl->AddText(font, sz, ImVec2(x, y), col, t);
    };

    // Rows to show + panel geometry (content-sized, clamped, centered).
    int n = ap_shop_count();
    const int kWindow = 16;
    int visible = n < kWindow ? n : kWindow;
    int first = g_cursor - kWindow / 2;
    if (first > n - kWindow) first = n - kWindow;
    if (first < 0) first = 0;

    const float pad = sz * 0.9f;
    float panelW = io.DisplaySize.x * 0.52f;
    if (panelW < 560.0f) panelW = 560.0f;
    const float headRows = 2.4f;            // title + status/legend
    float panelH = pad * 2 + (headRows + visible) * lh;
    const float px = (io.DisplaySize.x - panelW) * 0.5f;
    const float py = (io.DisplaySize.y - panelH) * 0.5f;

    // Native-style blue frame: translucent fill, soft outer shadow, light border.
    dl->AddRectFilled(ImVec2(px + 6, py + 8), ImVec2(px + panelW + 6, py + panelH + 8),
                      IM_COL32(0, 0, 0, 120), 12.0f);        // drop shadow
    dl->AddRectFilled(ImVec2(px, py), ImVec2(px + panelW, py + panelH),
                      IM_COL32(18, 30, 66, 214), 12.0f);      // blue body
    dl->AddRect(ImVec2(px, py), ImVec2(px + panelW, py + panelH),
                IM_COL32(120, 160, 230, 210), 12.0f, 0, 1.6f); // border

    const float x = px + pad;
    float y = py + pad;
    // Centered title.
    const char* title = "Divine Blessings";
    ImVec2 tsz = font->CalcTextSizeA(sz, FLT_MAX, 0.0f, title);
    text_at(px + (panelW - tsz.x) * 0.5f, y, gold, title);
    y += lh;
    text_at(x, y, dim, ap_shop_status().c_str());
    y += lh * 1.4f;

    for (int i = first; i < n && i < first + kWindow; i++) {
        std::string line = ap_shop_line(i);
        ImU32 col = white;
        if (!line.empty()) {
            if (line[0] == '*') { col = gold; line.erase(0, 1); }
            else if (line[0] == '!') { col = red; line.erase(0, 1); }
            else if (line[0] == ' ') line.erase(0, 1);
        }
        if (line.find("(bought)") != std::string::npos ||
            line.find("(locked") != std::string::npos)
            col = dim;
        if (i == g_cursor) {                 // highlight the selected row
            dl->AddRectFilled(ImVec2(px + pad * 0.4f, y - 2),
                              ImVec2(px + panelW - pad * 0.4f, y + lh - 4),
                              IM_COL32(70, 100, 175, 150), 4.0f);
            if (col == white) col = gold;
        }
        text_at(x + sz * 0.6f, y, col, line.c_str());
        y += lh;
    }
    // Key legend, centered at the bottom.
    const char* legend = "[Up/Down] select    [Enter] buy    [F5/Esc] close";
    ImVec2 lsz = font->CalcTextSizeA(sz, FLT_MAX, 0.0f, legend);
    text_at(px + (panelW - lsz.x) * 0.5f, py + panelH - pad - lh * 0.2f, dim, legend);
}

}  // namespace apshop
