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

// Drawn from the D3D9 EndScene hook, inside the ImGui frame.
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

    auto line_at = [&](float x, float y, ImU32 col, const char* text) {
        ImVec2 p(x, y);
        dl->AddText(font, sz, ImVec2(p.x - 1, p.y), shadow, text);
        dl->AddText(font, sz, ImVec2(p.x + 1, p.y), shadow, text);
        dl->AddText(font, sz, ImVec2(p.x, p.y - 1), shadow, text);
        dl->AddText(font, sz, ImVec2(p.x, p.y + 1), shadow, text);
        dl->AddText(font, sz, p, col, text);
    };

    std::lock_guard<std::mutex> lk(g_mtx);
    const float x = 18.0f;
    float y = io.DisplaySize.y - 30.0f - lh;        // input line anchor
    // input line (or the hint how to open it)
    if (g_typing) {
        std::string cur = "> " + g_input + "_";
        line_at(x, y, gold, cur.c_str());
    } else {
        line_at(x, y, IM_COL32(150, 150, 150, 200),
                "[F6] chat  [Enter] type  (!hint <item>, !help)");
    }
    // feed above it, newest at the bottom
    int shown = 0;
    for (auto it = g_lines.rbegin(); it != g_lines.rend() && shown < kShow;
         ++it, ++shown) {
        y -= lh;
        line_at(x, y, white, it->c_str());
    }
}

}  // namespace apchat
