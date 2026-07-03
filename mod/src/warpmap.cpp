// Overlay: the found/total check count drawn on each floor bar of the in-game
// statue warp map ("The Tower"). Gated on the warp-map-open flag so it only
// shows while that screen is up.
//
// The map lists the 22 warp-statue floors as bars in two columns (live-mapped
// from the labelled map): the RIGHT column runs bottom->top 1F,4F,5F,6F,7F,8F,
// 9F,10F,11F,12F,13F,14F,Rado's Annex; then it continues at the LEFT column
// bottom->top 17F,18F,20F,21F,22F,23F,24F,25F,Tower Summit. Each bar's count is
// placed at its right-end thumbnail. Bar positions can't be read cleanly from
// the scaled UI widget tree, so each column is defined by its bottom/top bar
// anchor (display fractions) and the middle bars are interpolated; all anchors
// + the color are cfg-tunable (yso_ap.cfg) so the layout can be dialed in
// without a rebuild.
#include "imgui.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cfloat>

// floor -> (found, total) checks + list of all check-floors (hook_ap.cpp).
extern "C" bool ap_floor_count(int floor, int* found, int* total);
extern "C" int ap_floors_with_checks(int* out, int cap);

namespace apwarpmap {

// The warp map is open when this cell == 3 (0 = closed). Found via open/close
// memory diff (yso_win.exe v1.1.1.0).
static const uintptr_t kMapOpenAbs = 0x00738B40;

// Bars bottom->top per column, as FLOOR numbers for the count lookup.
// 0 = a bar with no numbered floor / no checks (Rado's Annex, Tower Summit) — skipped.
static const int kRightFloors[] = {1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16};
static const int kLeftFloors[]  = {17, 18, 20, 21, 22, 23, 24, 25, 0};
static const int kRightN = 13, kLeftN = 9;

// Tunable right-end anchors (display fractions): each column lerps bottom->top.
static float g_rbx = 0.690f, g_rby = 0.913f, g_rtx = 0.830f, g_rty = 0.150f; // right col
static float g_lbx = 0.318f, g_lby = 0.588f, g_ltx = 0.573f, g_lty = 0.082f; // left col
static float g_size = 34.0f;
static unsigned g_rgb = 0xAAFF33;      // lime-green: floors with checks still left
static unsigned g_rgb_done = 0xFFC83C; // gold: floors cleared of all checks
// Barless floors (no statue -> no bar) are listed here, in the empty bottom-left.
static float g_lx = 0.220f, g_ly = 0.820f;
static bool g_cfg_loaded = false;

static void load_cfg() {
    g_cfg_loaded = true;
    FILE* f = fopen("yso_ap.cfg", "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == ';') continue;
        char* eq = strchr(line, '='); if (!eq) continue;
        *eq = 0; const char* k = line; const char* v = eq + 1;
        if      (!strcmp(k, "warpmap_color")) g_rgb  = (unsigned)strtoul(v, nullptr, 16);
        else if (!strcmp(k, "warpmap_color_done")) g_rgb_done = (unsigned)strtoul(v, nullptr, 16);
        else if (!strcmp(k, "warpmap_size"))  g_size = (float)atof(v);
        else if (!strcmp(k, "warpmap_rbx"))   g_rbx  = (float)atof(v);
        else if (!strcmp(k, "warpmap_rby"))   g_rby  = (float)atof(v);
        else if (!strcmp(k, "warpmap_rtx"))   g_rtx  = (float)atof(v);
        else if (!strcmp(k, "warpmap_rty"))   g_rty  = (float)atof(v);
        else if (!strcmp(k, "warpmap_lbx"))   g_lbx  = (float)atof(v);
        else if (!strcmp(k, "warpmap_lby"))   g_lby  = (float)atof(v);
        else if (!strcmp(k, "warpmap_ltx"))   g_ltx  = (float)atof(v);
        else if (!strcmp(k, "warpmap_lty"))   g_lty  = (float)atof(v);
        else if (!strcmp(k, "warpmap_list_x")) g_lx  = (float)atof(v);
        else if (!strcmp(k, "warpmap_list_y")) g_ly  = (float)atof(v);
    }
    fclose(f);
}

static void text_outline(ImDrawList* dl, ImFont* font, float px, float py,
                         ImU32 col, const char* t) {
    const ImU32 sh = IM_COL32(0, 0, 0, 235);
    const float o = 2.0f;
    dl->AddText(font, g_size, ImVec2(px - o, py), sh, t);
    dl->AddText(font, g_size, ImVec2(px + o, py), sh, t);
    dl->AddText(font, g_size, ImVec2(px, py - o), sh, t);
    dl->AddText(font, g_size, ImVec2(px, py + o), sh, t);
    dl->AddText(font, g_size, ImVec2(px, py), col, t);
}

static void text_centered(ImDrawList* dl, ImFont* font, float x, float y,
                          ImU32 col, const char* t) {
    ImVec2 ts = font->CalcTextSizeA(g_size, FLT_MAX, 0.0f, t);
    text_outline(dl, font, x - ts.x * 0.5f, y - ts.y * 0.5f, col, t);  // centered
}

static void draw_col(ImDrawList* dl, ImFont* font, ImVec2 disp, ImU32 col,
                     ImU32 col_done, const int* floors, int n,
                     float bx, float by, float tx, float ty) {
    for (int i = 0; i < n; i++) {
        int fl = floors[i];
        if (fl <= 0) continue;
        int found = 0, total = 0;
        if (!ap_floor_count(fl, &found, &total)) continue;
        float t = (n > 1) ? (float)i / (n - 1) : 0.0f;
        float x = (bx + (tx - bx) * t) * disp.x;
        float y = (by + (ty - by) * t) * disp.y;
        char buf[16];
        snprintf(buf, sizeof(buf), "%d/%d", found, total);
        text_centered(dl, font, x, y, found >= total ? col_done : col, buf);
    }
}

void draw() {
    if (!g_cfg_loaded) load_cfg();
    if (*(volatile int*)kMapOpenAbs != 3) return;   // warp map not open
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    ImFont* font = ImGui::GetFont();
    ImVec2 disp = ImGui::GetIO().DisplaySize;
    ImU32 col = IM_COL32((g_rgb >> 16) & 0xFF, (g_rgb >> 8) & 0xFF, g_rgb & 0xFF, 255);
    ImU32 col_done = IM_COL32((g_rgb_done >> 16) & 0xFF, (g_rgb_done >> 8) & 0xFF,
                              g_rgb_done & 0xFF, 255);
    draw_col(dl, font, disp, col, col_done, kRightFloors, kRightN, g_rbx, g_rby, g_rtx, g_rty);
    draw_col(dl, font, disp, col, col_done, kLeftFloors,  kLeftN,  g_lbx, g_lby, g_ltx, g_lty);

    // Barless floors (no statue -> no bar on the map): list them in the empty
    // bottom-left. Any check-floor not covered by a bar above goes here.
    bool has_bar[64] = {false};
    for (int i = 0; i < kRightN; i++) if (kRightFloors[i] > 0 && kRightFloors[i] < 64) has_bar[kRightFloors[i]] = true;
    for (int i = 0; i < kLeftN;  i++) if (kLeftFloors[i]  > 0 && kLeftFloors[i]  < 64) has_bar[kLeftFloors[i]]  = true;
    int floors[64];
    int nf = ap_floors_with_checks(floors, 64);
    float lh = g_size * 1.15f;
    float y = g_ly * disp.y;
    for (int i = 0; i < nf; i++) {
        int fl = floors[i];
        if (fl <= 0 || fl >= 64 || has_bar[fl]) continue;
        int found = 0, total = 0;
        if (!ap_floor_count(fl, &found, &total)) continue;
        char buf[24];
        snprintf(buf, sizeof(buf), "%dF  %d/%d", fl, found, total);
        text_outline(dl, font, g_lx * disp.x, y, found >= total ? col_done : col, buf);
        y += lh;
    }
}

}  // namespace apwarpmap
