// The in-game overlay UI (milestone 1: static demo). Later milestones wire this
// to the AP client over a localhost socket: live recent-items list + a terminal
// that sends Archipelago commands.
#include "imgui.h"

namespace overlay {

void draw() {
    // Top-RIGHT corner: the top-left overlaps the game's "New Area" titles.
    const ImGuiIO& io = ImGui::GetIO();
    const float w = 380.0f, h = 260.0f, margin = 20.0f;
    ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - w - margin, margin),
                            ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(w, h), ImGuiCond_FirstUseEver);
    ImGui::Begin("Archipelago");

    ImGui::TextColored(ImVec4(0.88f, 0.64f, 0.34f, 1.0f),
                       "Hello Archipelago  -  Ys Origin mod");
    ImGui::Separator();
    ImGui::TextDisabled("INSERT toggles this overlay.");
    ImGui::Spacing();

    ImGui::SeparatorText("Recent items");
    ImGui::BulletText("(milestone 2: live items from the AP client)");

    ImGui::SeparatorText("Terminal");
    static char cmd[256] = "";
    ImGui::SetNextItemWidth(-1);
    if (ImGui::InputText("##cmd", cmd, sizeof(cmd),
                         ImGuiInputTextFlags_EnterReturnsTrue)) {
        // milestone 3: forward `cmd` to the AP client command processor.
        cmd[0] = '\0';
    }
    ImGui::End();
}

}  // namespace overlay
