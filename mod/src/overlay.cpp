// The in-game overlay UI. Shows AP connection status and a live "recent items"
// feed pushed from the AP client (hook_ap.cpp), plus a terminal stub (later:
// AP commands). Drawn from the D3D9 EndScene hook (render thread); the shared
// buffers are mutex-guarded since the AP client thread writes them.
#include "imgui.h"

#include <deque>
#include <mutex>
#include <string>

namespace overlay {

static std::mutex g_mtx;
static std::deque<std::string> g_items;   // most-recent appended at the back
static std::string g_status = "connecting...";

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

// -- drawn from the render thread ------------------------------------------ #

void draw() {
    // Top-RIGHT corner: the top-left overlaps the game's "New Area" titles.
    const ImGuiIO& io = ImGui::GetIO();
    const float w = 380.0f, h = 320.0f, margin = 20.0f;
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - w - margin, margin),
                            ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(w, h), ImGuiCond_FirstUseEver);
    ImGui::Begin("Archipelago");

    ImGui::TextColored(ImVec4(0.88f, 0.64f, 0.34f, 1.0f),
                       "Ys Origin  -  Archipelago");
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        ImGui::TextDisabled("%s", g_status.c_str());
    }
    ImGui::TextDisabled("INSERT toggles this overlay.");
    ImGui::Spacing();

    ImGui::SeparatorText("Recent items");
    {
        std::lock_guard<std::mutex> lk(g_mtx);
        if (g_items.empty()) {
            ImGui::TextDisabled("(none yet)");
        } else {
            // newest first
            for (auto it = g_items.rbegin(); it != g_items.rend(); ++it)
                ImGui::BulletText("%s", it->c_str());
        }
    }

    ImGui::SeparatorText("Terminal");
    static char cmd[256] = "";
    ImGui::SetNextItemWidth(-1);
    if (ImGui::InputText("##cmd", cmd, sizeof(cmd),
                         ImGuiInputTextFlags_EnterReturnsTrue)) {
        // milestone 3: forward `cmd` to the AP client (Say / !commands).
        cmd[0] = '\0';
    }
    ImGui::End();
}

}  // namespace overlay
