// The in-game AP overlay: connection status/room header (top-right) plus an
// animated stack of item TOASTS -- a slide-in banner per item you find or
// receive, styled by tier, that pulses (progression) and auto-fades. Drawn from
// the D3D9 EndScene hook (render thread); the shared buffers are mutex-guarded
// since the AP client thread pushes toasts.
#include "imgui.h"

#include <cmath>
#include <deque>
#include <mutex>
#include <string>

extern ImFont* g_overlay_big_font;  // large item-name font (hook_d3d9.cpp)
extern "C" __declspec(dllimport) unsigned long __stdcall GetTickCount(void);
extern "C" unsigned long ap_fog_until();   // Blinding Fog trap deadline (hook_ap.cpp)

namespace overlay {

// One item banner. `flags` = AP item classification (1 progression, 2 useful,
// 4 trap, 0 filler). `sent` = we found it FOR someone (arrow out) vs it came
// to us (arrow in). t0 = GetTickCount at creation, drives the animation.
struct Toast {
    std::string title;    // item name
    std::string sub;      // "from Hugo" / "-> Yunica" / "yours"
    int flags = 0;
    bool sent = false;
    unsigned long t0 = 0;
};

static std::mutex g_mtx;
static std::deque<Toast> g_toasts;
static std::string g_status = "connecting...";
static std::string g_room;                // current room (scene), from hook_ap
static std::string g_tracker;             // "Checks here: k/n" (under the room)

// Timings (ms).
static const unsigned kInMs = 200;    // slide + fade in
static const unsigned kHoldMs = 3600; // full visibility
static const unsigned kOutMs = 750;   // fade + slide out
static const unsigned kLifeMs = kInMs + kHoldMs + kOutMs;

// -- called from the AP client thread -------------------------------------- #

// Rich item toast. who = the other player (empty / self => "yours").
void push_toast(const std::string& item, const std::string& who, int flags,
                bool sent) {
    Toast t;
    t.title = item;
    if (sent)              t.sub = "\xE2\x86\x92 " + who;      // → who
    else if (who.empty())  t.sub = "yours";
    else                   t.sub = "from " + who;
    t.flags = flags;
    t.sent = sent;
    t.t0 = GetTickCount();
    std::lock_guard<std::mutex> lk(g_mtx);
    g_toasts.push_back(t);
    while (g_toasts.size() > 8) g_toasts.pop_front();
}

// Plain system message (goal / DeathLink / batch summary) -> a neutral toast.
void push_item(const std::string& text) {
    Toast t;
    t.title = text;
    t.flags = -1;            // neutral (no gem, no tier color)
    t.t0 = GetTickCount();
    std::lock_guard<std::mutex> lk(g_mtx);
    g_toasts.push_back(t);
    while (g_toasts.size() > 8) g_toasts.pop_front();
}

void set_status(const std::string& text) { std::lock_guard<std::mutex> lk(g_mtx); g_status = text; }
void set_room(const std::string& text)   { std::lock_guard<std::mutex> lk(g_mtx); g_room = text; }
void set_tracker(const std::string& text){ std::lock_guard<std::mutex> lk(g_mtx); g_tracker = text; }
void set_panel(const std::string&)       {}   // F7 panel removed; kept for ABI
std::string get_status() { std::lock_guard<std::mutex> lk(g_mtx); return g_status; }

// -- drawn from the render thread ------------------------------------------ #

static ImU32 tier_rgb(int flags) {
    if (flags < 0)      return IM_COL32(150, 205, 255, 255);  // system (soft blue)
    if (flags & 4)      return IM_COL32(235, 110, 110, 255);  // trap (red)
    if (flags & 1)      return IM_COL32(240, 200, 96, 255);   // progression (gold)
    if (flags & 2)      return IM_COL32(150, 205, 255, 255);  // useful (blue)
    return IM_COL32(225, 225, 225, 255);                      // filler (white)
}

static ImU32 with_alpha(ImU32 c, float a) {
    if (a < 0) a = 0; if (a > 1) a = 1;
    return (c & 0x00FFFFFF) | ((ImU32)(a * 255.0f) << 24);
}

// Right-aligned outlined text at a given font/size.
static void rtext(ImDrawList* dl, ImFont* f, float size, float right_x, float y,
                  ImU32 col, float a, const char* t) {
    ImVec2 sz = f->CalcTextSizeA(size, FLT_MAX, 0.0f, t);
    float x = right_x - sz.x;
    ImU32 sh = with_alpha(IM_COL32(0, 0, 0, 230), a);
    float o = (size > 24.0f) ? 2.0f : 1.0f;
    dl->AddText(f, size, ImVec2(x - o, y), sh, t);
    dl->AddText(f, size, ImVec2(x + o, y), sh, t);
    dl->AddText(f, size, ImVec2(x, y - o), sh, t);
    dl->AddText(f, size, ImVec2(x, y + o), sh, t);
    dl->AddText(f, size, ImVec2(x, y), with_alpha(col, a), t);
}

void draw() {
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const ImGuiIO& io = ImGui::GetIO();
    const float right = io.DisplaySize.x - 18.0f;

    ImFont* small = ImGui::GetFont();
    const float ssz = ImGui::GetFontSize();
    const float slh = ImGui::GetTextLineHeightWithSpacing();
    ImFont* big = g_overlay_big_font ? g_overlay_big_font : small;
    const float bsz = 38.0f;

    const ImU32 gold = IM_COL32(228, 196, 112, 255);
    const ImU32 dim = IM_COL32(170, 170, 170, 255);

    // Blinding Fog trap: a hazy white overlay over the GAME (drawn first, so the
    // AP HUD/toasts stay readable on top). ~30s window with a soft in/out fade.
    {
        unsigned long fu = ap_fog_until(), now = GetTickCount();
        if (fu > now) {
            unsigned rem = (unsigned)(fu - now);
            unsigned el = (rem < 30000) ? (30000 - rem) : 0;
            float a = 0.85f;
            if (el < 400) a *= (float)el / 400.0f;      // fade in
            if (rem < 900) a *= (float)rem / 900.0f;    // fade out
            dl->AddRectFilled(ImVec2(0, 0), io.DisplaySize,
                              IM_COL32(222, 227, 236, (int)(a * 255)));
        }
    }

    std::lock_guard<std::mutex> lk(g_mtx);

    // Header (top-right).
    float y = 16.0f;
    rtext(dl, small, ssz, right, y, gold, 1.0f, "Archipelago");   y += slh;
    rtext(dl, small, ssz, right, y, dim, 1.0f, g_status.c_str()); y += slh;
    if (!g_room.empty())    { rtext(dl, small, ssz, right, y, dim, 1.0f, g_room.c_str());     y += slh; }
    if (!g_tracker.empty()) { rtext(dl, small, ssz, right, y, gold, 1.0f, g_tracker.c_str()); y += slh; }
    y += slh * 0.5f;

    // Toasts: newest at the top of the stack, sliding down as older ones fade.
    // Drop expired ones first (render thread owns the deque under the lock).
    unsigned long now = GetTickCount();
    while (!g_toasts.empty() && now - g_toasts.front().t0 >= kLifeMs)
        g_toasts.pop_front();

    const float rowH = bsz * 1.25f;      // name row
    const float subH = slh * 0.95f;      // subtitle row
    const float pad = 8.0f;
    const float toastH = rowH + subH + pad * 1.5f;

    // Iterate newest -> oldest so the freshest is on top.
    for (auto it = g_toasts.rbegin(); it != g_toasts.rend(); ++it) {
        const Toast& t = *it;
        unsigned el = (unsigned)(now - t.t0);
        // Animation: slide-in from the right + alpha envelope.
        float a, slide;
        if (el < kInMs) {
            float k = (float)el / kInMs;      // 0..1
            a = k;
            slide = (1.0f - k) * 44.0f;        // start 44px right, ease to 0
        } else if (el < kInMs + kHoldMs) {
            a = 1.0f; slide = 0.0f;
        } else {
            float k = (float)(el - kInMs - kHoldMs) / kOutMs;  // 0..1
            a = 1.0f - k;
            slide = k * 44.0f;
        }
        bool prog = (t.flags & 1) && t.flags >= 0;
        // Progression pulse: gentle brightness breathing during hold.
        float pulse = 1.0f;
        if (prog && el >= kInMs && el < kInMs + kHoldMs)
            pulse = 0.78f + 0.22f * (0.5f + 0.5f * sinf(el * 0.006f));

        ImU32 accent = tier_rgb(t.flags);
        float rx = right - slide;

        // Measure width from the wider of title / subtitle.
        ImVec2 tsz = big->CalcTextSizeA(bsz, FLT_MAX, 0.0f, t.title.c_str());
        ImVec2 ssz2 = small->CalcTextSizeA(ssz, FLT_MAX, 0.0f, t.sub.c_str());
        float w = (tsz.x > ssz2.x ? tsz.x : ssz2.x) + pad * 3.0f + 6.0f;
        if (w < 300.0f) w = 300.0f;
        float x0 = rx - w, x1 = rx;
        float y0 = y, y1 = y + toastH;

        // Backdrop: translucent dark panel + accent left edge.
        dl->AddRectFilled(ImVec2(x0, y0), ImVec2(x1, y1),
                          with_alpha(IM_COL32(14, 18, 32, 205), a), 7.0f);
        dl->AddRect(ImVec2(x0, y0), ImVec2(x1, y1),
                    with_alpha(accent, a * 0.55f), 7.0f, 0, 1.5f);
        dl->AddRectFilled(ImVec2(x0, y0 + 3), ImVec2(x0 + 4.0f, y1 - 3),
                          with_alpha(accent, a * pulse), 2.0f);  // accent bar

        // Title (accent-colored big) + subtitle (dim small), right-aligned.
        rtext(dl, big, bsz, x1 - pad, y0 + pad * 0.6f,
              (t.flags < 0) ? IM_COL32(235, 235, 235, 255) : accent,
              a * (prog ? pulse : 1.0f), t.title.c_str());
        if (!t.sub.empty())
            rtext(dl, small, ssz, x1 - pad, y0 + pad * 0.6f + rowH, dim, a,
                  t.sub.c_str());

        y += toastH + 6.0f;
    }
}

}  // namespace overlay
