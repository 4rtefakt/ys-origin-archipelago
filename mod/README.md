# Ys Origin — native in-game mod (Phase B)

A drop-in `dinput8.dll` proxy that loads into `yso_win.exe`, hooks Direct3D 9,
and draws a **Dear ImGui** overlay **inside the game** — the home for the recent-
items overlay, an in-game Archipelago terminal, in-game text relabeling, and
clean VM-level hooks. It complements the external Python client (`client/`);
early milestones bridge to it over a localhost socket.

> This is separate from the no-injection external tool. It IS an injected mod
> (a proxy DLL + runtime hooks) — for **single-player Ys Origin only**, which has
> no anti-cheat. Uninstall anytime by deleting the DLL.

## Status — milestone 1
Proxy DLL + D3D9 `EndScene`/`Reset` hook + ImGui overlay showing "Hello
Archipelago". Toggle in-game with **INSERT**. (Recent-items/terminal are stubs;
milestones 2–5 wire the AP bridge, VM-grant hook, text relabel, and a New Game
menu entry.)

## 1. Install a C++ toolchain (one-time)
You need a **32-bit** MSVC toolchain (the game is 32-bit). Easiest:

1. Install **Visual Studio Community 2022** (free):
   `winget install --id Microsoft.VisualStudio.2022.Community`
   …or download from https://visualstudio.microsoft.com/.
2. In the installer, check **“Desktop development with C++.”** That includes the
   MSVC x86 compiler, the Windows SDK, and CMake.

(Lighter alternative: “Build Tools for Visual Studio 2022” + standalone CMake —
same workload, no IDE.)

## 2. Build
The first configure downloads MinHook + Dear ImGui via CMake FetchContent
(needs internet once).

```powershell
cd mod
cmake -A Win32 -B build          # -A Win32 = 32-bit (required)
cmake --build build --config Release
```
Output: `mod/build/Release/dinput8.dll`.

(Or in the VS IDE: **File ▸ Open ▸ Folder…** this `mod/` folder, pick the **x86**
configuration, Build.)

## 3. Install into the game
Copy the DLL next to the executable:
```
mod/build/Release/dinput8.dll  ->  …/steamapps/common/Ys Origin/dinput8.dll
```
Launch the game and press **INSERT** — the "Archipelago" window should appear.
**Uninstall:** delete that `dinput8.dll`.

> If the game fails to start, the proxy may be mis-forwarding — check that
> `dinput8.dll` is the 32-bit build, and see the troubleshooting notes below.

## Architecture
```
dinput8.dll (our proxy, in the game folder)
  ├─ proxy_dinput8.cpp  forwards real dinput8 exports (transparent)
  ├─ dllmain.cpp        loads real dinput8, starts the hook thread
  ├─ hook_d3d9.cpp      MinHook on IDirect3DDevice9::EndScene/Reset -> ImGui
  └─ overlay.cpp        the in-game UI (overlay + terminal)
deps (auto-fetched): MinHook (detours), Dear ImGui (+dx9/win32 backends)
```

## Roadmap
1. ✅ inject + ImGui overlay (this).
2. Socket bridge to `client/` → live recent-items (reuse the AP networking).
3. In-game terminal → AP command processor.
4. Hook the script-VM grant opcode (`0x64` in `FUN_004472e0`) for clean
   suppression + instant detection; hook text render to relabel item names.
5. "Archipelago" entry in the New Game menu.

## Troubleshooting
- **Build says “Configure a 32-bit build”** — you didn’t pass `-A Win32` (or
  didn’t pick x86 in the IDE). Delete `build/` and reconfigure.
- **Game crashes / no window** — make sure it’s the Release **x86** DLL; some
  overlays (Steam, RTSS) also hook D3D9 and can conflict — test with those off.
- **Overlay but no input** — INSERT toggles input capture; the game keeps
  running underneath.
