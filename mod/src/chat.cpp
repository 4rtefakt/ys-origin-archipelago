// In-game Archipelago chat overlay.
//
// A toggleable feed of the AP room's chat / item log (everything the server
// PrintJSONs: items found by and for you, hints, joins, chat), drawn bottom-left
// in the same outlined-HUD style as the rest of the overlay, plus a one-line
// input to talk back — which is also how you use server commands (!hint, !help,
// !release, ...).
//
//   F6      toggle the feed
//   Enter   (while the feed is open) focus the input line
//   Esc     unfocus / close
//
// While the input line is focused the game's own input is frozen (hook_input
// checks apchat::is_capturing(), same mechanism as the connect menu) so typing
// doesn't move the player. Incoming lines arrive from the AP poll thread
// (hook_ap.cpp -> push()); everything is mutex-guarded.
#include "imgui.h"

#include <cfloat>
#include <deque>
#include <mutex>
#include <string>

void mod_log(const char* fmt, ...);
extern "C" void ap_request_say(const char* text);  // hook_ap.cpp: queued to poll thread

namespace apchat {

static std::mutex g_mtx;
static std::deque<std::string> g_lines;   // newest at the back
static bool g_visible = false;            // F6; start hidden (cfg chat=1 shows)
static bool g_typing = false;             // input line focused (freezes the game)
static std::string g_input;

static const size_t kKeep = 60;           // scrollback kept
static const int kShow = 8;               // lines drawn

void set_visible(bool v) { g_visible = v; }

void push(const std::string& line) {
    std::lock_guard<std::mutex> lk(g_mtx);
    g_lines.push_back(line);
    while (g_lines.size() > kKeep) g_lines.pop_front();
}

bool is_capturing() { return g_typing; }

// WM_KEYDOWN hook (from the WndProc subclass). Returns true when consumed.
bool on_wm_key(unsigned msg, unsigned long long wp) {
    (void)msg;
    if (wp == 0x75) {                       // VK_F6: toggle the feed
        g_visible = !g_visible;
        if (!g_visible) g_typing = false;
        return true;
    }
    if (!g_visible) return false;
    if (!g_typing) {
        if (wp == 0x0D) { g_typing = true; return true; }   // Enter: focus input
        return false;
    }
    // typing: swallow everything the game might react to
    if (wp == 0x1B) { g_typing = false; g_input.clear(); return true; }  // Esc
    if (wp == 0x0D) {                                        // Enter: send
        std::string out;
        {
            std::lock_guard<std::mutex> lk(g_mtx);
            out.swap(g_input);
        }
        // trim
        while (!out.empty() && out.back() == ' ') out.pop_back();
        if (!out.empty()) ap_request_say(out.c_str());
        g_typing = false;
        return true;
    }
    return true;
}

// WM_CHAR hook: build the input line while typing.
bool on_wm_char(unsigned long long wp) {
    if (!g_typing) return false;
    std::lock_guard<std::mutex> lk(g_mtx);
    if (wp == 0x08) {                       // backspace
        if (!g_input.empty()) g_input.pop_back();
    } else if (wp >= 0x20 && wp < 0x7F && g_input.size() < 200) {
        g_input.push_back((char)wp);
    }
    return true;
}

// Drawn from the D3D9 EndScene hook, inside the ImGui frame. Anchored bottom-
// RIGHT over a translucent backdrop so the feed stays readable over bright
// scenes; each line is right-aligned to the panel edge.
void draw() {
    if (!g_visible) return;
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    ImFont* font = ImGui::GetFont();
    const float sz = ImGui::GetFontSize();
    const float lh = ImGui::GetTextLineHeightWithSpacing();
    const ImU32 shadow = IM_COL32(0, 0, 0, 220);
    const ImU32 white = IM_COL32(235, 235, 235, 255);
    const ImU32 gold = IM_COL32(228, 196, 112, 255);

    // Right-aligned outlined text: x is the RIGHT edge, text is placed to its left.
    auto right_at = [&](float xr, float y, ImU32 col, const char* text) {
        ImVec2 ts = font->CalcTextSizeA(sz, FLT_MAX, 0.0f, text);
        float x = xr - ts.x;
        dl->AddText(font, sz, ImVec2(x - 1, y), shadow, text);
        dl->AddText(font, sz, ImVec2(x + 1, y), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y - 1), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y + 1), shadow, text);
        dl->AddText(font, sz, ImVec2(x, y), col, text);
        return ts.x;
    };

    std::lock_guard<std::mutex> lk(g_mtx);
    const float margin = 18.0f;
    const float pad = 8.0f;
    const float xr = io.DisplaySize.x - margin;          // right edge of text
    const float y_input = io.DisplaySize.y - 30.0f - lh; // input line baseline
    std::string input_line = g_typing
        ? ("> " + g_input + "_")
        : std::string("[F6] chat  [Enter] type  (!hint <item>, !help)");
    // Measure the block (widest line + how many feed lines) to size the backdrop.
    float widest = font->CalcTextSizeA(sz, FLT_MAX, 0.0f, input_line.c_str()).x;
    int feed = 0;
    for (auto it = g_lines.rbegin(); it != g_lines.rend() && feed < kShow;
         ++it, ++feed) {
        float w = font->CalcTextSizeA(sz, FLT_MAX, 0.0f, it->c_str()).x;
        if (w > widest) widest = w;
    }
    // Translucent backdrop behind the whole block (feed lines climb above input).
    dl->AddRectFilled(ImVec2(xr - widest - pad, y_input - feed * lh - pad),
                      ImVec2(xr + pad, y_input + lh + pad),
                      IM_COL32(10, 14, 30, 170), 6.0f);

    right_at(xr, y_input, g_typing ? gold : IM_COL32(150, 150, 150, 210),
             input_line.c_str());
    float y = y_input;
    int shown = 0;
    for (auto it = g_lines.rbegin(); it != g_lines.rend() && shown < kShow;
         ++it, ++shown) {
        y -= lh;
        right_at(xr, y, white, it->c_str());
    }
}

}  // namespace apchat
