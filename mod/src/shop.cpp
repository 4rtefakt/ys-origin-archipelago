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

// Drawn from the D3D9 EndScene hook, inside the ImGui frame.
void draw() {
    if (!g_open) return;
    if (!ap_shop_available()) { g_open = false; return; }
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    ImFont* font = ImGui::GetFont();
    const float sz = ImGui::GetFontSize();
    const float lh = ImGui::GetTextLineHeightWithSpacing();
    const ImU32 shadow = IM_COL32(0, 0, 0, 230);
    const ImU32 white = IM_COL32(235, 235, 235, 255);
    const ImU32 gold = IM_COL32(228, 196, 112, 255);
    const ImU32 red = IM_COL32(235, 110, 110, 255);
    const ImU32 dim = IM_COL32(150, 150, 150, 220);

    auto line_at = [&](float x, float y, ImU32 col, const char* text) {
        dl->AddText(font, sz, ImVec2(x - 1, y), shadow, text);
        dl->AddText(font, sz, ImVec2(x + 1, y), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y - 1), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y + 1), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y), col, text);
    };

    const float x = io.DisplaySize.x * 0.22f;
    float y = io.DisplaySize.y * 0.18f;
    // dark backdrop so the list reads over gameplay
    int n = ap_shop_count();
    const int kWindow = 16;
    int rows = (n < kWindow ? n : kWindow) + 3;
    dl->AddRectFilled(ImVec2(x - 14, y - 8),
                      ImVec2(io.DisplaySize.x * 0.80f, y + rows * lh + 8),
                      IM_COL32(10, 10, 14, 200), 6.0f);

    line_at(x, y, gold, "Blessing Shop (Archipelago)");
    y += lh;
    line_at(x, y, dim, (ap_shop_status() +
                        "   [Up/Down] select  [Enter] buy  [Esc] close").c_str());
    y += lh * 1.2f;

    int first = g_cursor - kWindow / 2;
    if (first > n - kWindow) first = n - kWindow;
    if (first < 0) first = 0;
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
        std::string row = (i == g_cursor ? "> " : "  ") + line;
        line_at(x, y, (i == g_cursor && col == white) ? gold : col, row.c_str());
        y += lh;
    }
}

}  // namespace apshop
