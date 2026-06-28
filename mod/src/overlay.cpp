// The in-game overlay UI (milestone 1: static demo). Later milestones wire this
// to the AP client over a localhost socket: live recent-items list + a terminal
// that sends Archipelago commands.
#include "imgui.h"

namespace overlay {

void draw() {
    ImGui::SetNextWindowPos(ImVec2(20, 20), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowSize(ImVec2(380, 260), ImGuiCond_FirstUseEver);
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
