# Ys Origin × Archipelago

A randomizer for **Ys Origin** (Steam, `yso_win.exe` **v1.1.1.0**) that plugs
into [Archipelago](https://archipelago.gg) multiworld. Items across the whole
Darm Tower are shuffled; play as **Yunica, Hugo, or Toal**; everything runs
**inside the game** via a small drop-in mod — no external app to keep open.

---

## 🎮 I just want to play a rando (and I'm not techy)

You need three things:

1. **Ys Origin on Steam**, installed (must be version **1.1.1.0** — the current
   Steam build).
2. **Archipelago** — download and run the installer from
   [archipelago.gg](https://archipelago.gg) (the "Setup" download). You only need
   this to make/host a game; to join a friend's game you still install it.
3. The **two files from this project's [latest Release](../../releases/latest)**:
   - `ys_origin.apworld`
   - `dinput8.dll`

Then follow these steps once:

### Step 1 — Add Ys Origin to Archipelago
Copy **`ys_origin.apworld`** into Archipelago's **`custom_worlds`** folder
(inside your Archipelago install). That's it — Archipelago now knows the game.

### Step 2 — Make your settings file
Take **`Ys-Origin.yaml`** from the Release (a ready-made template), open it in
Notepad, and set your **name** and **character** (`yunica`, `hugo`, or `toal`).
Save it into Archipelago's **`Players`** folder.

### Step 3 — Generate and host the game
Open the **Archipelago Launcher** → **Generate**. It makes a seed from your
yaml. Then **Host** that seed (the Launcher walks you through it), or upload it
at [archipelago.gg/uploads](https://archipelago.gg/uploads). You'll get a
**room address** like `archipelago.gg:12345` (or `localhost` if hosting at home).

### Step 4 — Install the mod in your game
Copy **`dinput8.dll`** into your **Ys Origin game folder** — the one that
contains `yso_win.exe`, usually:

```
C:\Program Files (x86)\Steam\steamapps\common\Ys Origin\
```

(To uninstall later, just delete that file.)

### Step 5 — Connect from the in-game menu
Launch the game. On the **title screen** there's a new **Archipelago** entry just
above *New Game* — **press `F8`** to open the connect menu. Fill in:

- **Server** — e.g. `archipelago.gg` (the secure `wss://` is added automatically)
- **Port** — your room's port
- **Slot / Name** — the **name** you put in your yaml
- **Password** — leave blank if there isn't one

Use **Tab** or **↑/↓** to move between fields, type to edit, then press **Enter**
to connect. The status line shows the result. That's it — no file editing needed.

> **Prefer a config file?** The mod reads **`yso_ap.cfg`** next to `yso_win.exe`
> (created on first launch) for the menu's default values. Set `autoconnect=1`
> there to connect automatically at startup and skip the menu.

### Step 6 — Play!
Load your save or start a New Game. Top-right you'll see an **"Archipelago"**
overlay showing your connection, your current room, and items. Open chests /
trigger checks as normal — you'll get the **randomized** item, and items other
players find for you arrive automatically. **Press `INSERT`** to hide/show the
overlay.

> Stuck connecting? See [Troubleshooting](#-troubleshooting) below.

---

## ✨ Features

- **3 playable characters** — Yunica, Hugo, Toal. Each gets *their own* gear; you
  pick the character in your yaml and the seed is built for them.
- **Whole tower randomized** — chests, key-item events, goddess statues, and
  divine blessings are shuffled (~155 checks by default), plus optional
  boss / floor / room "sanity" checks.
- **Real progression logic** — the generator understands the tower: it knows you
  need the wind skill, dash, water-breathing, thunder, fire, climb, the various
  keys, and the boss medallions to reach each area, so **every seed is
  completable** (no item locked behind itself).
- **Self-contained, in-game** — the mod *is* the client. It connects to the
  Archipelago server, detects your checks, grants your items, and draws a native
  overlay in the game's own font. No second program to babysit.
- **Honest item boxes** — the in-game "Acquired ___" popup is relabeled to show
  the **real** item that was placed (name + icon).
- **Optional statue warp locks** — turn the goddess statues into a warp network:
  each one starts locked (dark — no warp, heal, or save) until you receive its
  unlock item, with one statue unlocked from the start. See `statue_warp_locks`.
- **Start anywhere (`random_start`)** — New Game spawns you at a *random* goddess
  statue anywhere in the tower. The mod skips the entire intro (movies +
  cutscenes, for every character) and warps you straight there with a
  floor-appropriate level + weapon so you're playable wherever you land;
  reachability uses a bidirectional warp-network logic so the seed stays beatable
  from any spawn. Needs `statue_warp_locks`. Normal seeds are unaffected. Two knobs
  tame it: `max_starting_floor` caps how deep the spawn can be (no more waking up on
  25F with nothing behind you), and `max_warp_floors_skip` limits how far ahead a
  single warp unlock can fling you (progress climbs in steps, not lucky leaps).
- **Catch-up level scaling** — so warping to a far-off floor isn't a grind wall:
  the mod can bump an under-leveled character toward the floor's expected level,
  and boosts EXP — a flat base multiplier everywhere (default 3x), raised to a
  catch-up multiplier (default 5x) while your level is at or under the deepest
  visited floor's expected level + 5, so falling behind your progress levels you
  back fast fighting anywhere. On by default; tune or disable with
  `level_scaling` and the `exp_*` options.
- **Weapon gating** — your weapon (the dominant damage stat) is upgraded by Cleria
  Ore, which is shuffled into the pool as progression. The generator guarantees
  the **vanilla weapon level for each floor** is obtainable before that zone is in
  logic, so the warp network can't drop you somewhere your weapon deals 1 damage.
  Receiving Cleria Ore upgrades your weapon directly (no NPC trip). On by default;
  pairs with level scaling so warped-ahead floors stay playable. See
  `weapon_requirements`.
- **Goal:** defeat Darm (the final boss), or optionally all bosses.

## ⚙️ Options (in your yaml)

| Option | Values | Default | Meaning |
|---|---|---|---|
| `character` | `yunica` / `hugo` / `toal` | `hugo` | Who you play as |
| `goal` | `defeat_darm` / `defeat_all_bosses` | `defeat_darm` | What wins the seed |
| `statue_checks` | `true` / `false` | `true` | Activating goddess statues are checks |
| `blessing_checks` | `true` / `false` | `true` | Buying divine blessings are checks |
| `boss_checks` | `true` / `false` | `true` | Reaching each boss arena is a check |
| `floor_checks` | `true` / `false` | `true` | Reaching each floor is a check |
| `room_checks` | `true` / `false` | `false` | Entering each room is a check (adds ~145 filler checks — big) |
| `statue_warp_locks` | `true` / `false` | `false` | Goddess statues start locked (no warp/heal/save) until you receive their unlock item; adds 21 "Statue Warp" items, one statue unlocked from the start |
| `random_start` | `true` / `false` | `false` | **Start anywhere.** With `statue_warp_locks` on, New Game spawns you at a random statue: the mod skips the whole intro (movies + cutscenes, every character) and warps you there geared for the floor; bidirectional warp-network logic keeps the seed beatable from any spawn |
| `max_starting_floor` | `1`–`25` | `10` | Cap the `random_start` spawn to statues on this floor or below, so New Game never drops you on the brutal deep floors with no gear behind you. Lower = gentler openings; raise for more variety. No effect unless `random_start` is on |
| `max_warp_floors_skip` | `0`–`25` | `5` | How many floors ahead of your current reach a warp may jump (`0` = unlimited). With N > 0 a statue's warp only enters logic once you can reach a floor within N of it, so a single unlock can't teleport you across the tower. Everything still stays reachable on foot; this only paces the warp shortcuts. No effect unless `random_start` is on |
| `starting_items` | list of item names | `[Crystal, Dark Crystal]` | Items to begin every New Game owning (applied as a floor — marked owned). Defaults to the warp Crystals the intro grants, so `random_start` (which skips the intro) still has them. Unknown names are ignored |
| `starting_level` | `1`–`60` | `1` | Minimum character level at New Game (only ever raises you). `1` = vanilla. Stacks with `level_scaling` — you get the higher of this and the floor's expected level |
| `starting_weapon_level` | `1`–`6` | `1` | Minimum displayed weapon level at New Game (a floor). `1` = vanilla starter. The mod still upgrades weapon via Cleria Ore / floor-appropriate gear on top of this |
| `level_scaling` | `off` / `level_floor` / `exp_multiplier` / `both` | `both` | Catch-up leveling so warping to a far floor isn't a grind wall: bump you toward the floor's level, and/or grant scaled bonus EXP. No-op when you're already on level |
| `level_margin` | `0`–`10` | `0` | How many levels under a floor's expected level the floor-bump leaves you (0 = right at the expected level); raise for more challenge |
| `exp_multiplier_base` | `1`–`10` | `3` | Flat EXP multiplier applied everywhere while EXP scaling is on (`1` = vanilla rate) |
| `exp_multiplier_catchup` | `1`–`20` | `5` | EXP multiplier while your level ≤ the deepest visited floor's expected level + margin — catch up by fighting anywhere, easy floors included |
| `exp_catchup_margin` | `0`–`20` | `5` | Levels above the deepest floor's expected level that still count as catching up |
| `progressive_armor` | `true` / `false` | `true` | Armor & Boots become progressive: gear chests hold "Progressive Armor"/"Progressive Boots", and receiving one grants your character's next tier (pickups never skip ahead). Off = raw pieces shuffled as-is |
| `weapon_requirements` | `true` / `false` | `true` | Gate each zone behind enough Cleria Ore that the vanilla weapon level for that floor is obtainable first; Cleria Ore becomes progression and upgrades your weapon on pickup. Pairs with `level_scaling` to keep warped-ahead floors playable |
| `death_link` | `true` / `false` | `false` | You die when any other DeathLink player dies (and vice-versa) |

## ❓ Troubleshooting

- **No overlay in-game?** Press `INSERT`. If still nothing, the mod may not have
  loaded — confirm `dinput8.dll` is in the same folder as `yso_win.exe`.
- **Says disconnected / won't connect?** Check `yso_ap.cfg` — `host`, `port`, and
  `slot` must match the hosted room and the **name** in your yaml exactly.
- **Game won't launch / crashes immediately?** Make sure your Ys Origin is the
  current Steam build (**v1.1.1.0**) and that you used the `dinput8.dll` from the
  matching Release (it's built for that exact version).
- **Diagnostics:** the mod writes a log to `%TEMP%\yso_ap_mod.log`.
- **Starting gear missing?** Don't run any *other* external AP client at the same
  time as the mod — they conflict. The mod handles everything itself.

---

## 🛠️ For developers

This repo has two halves:

- **`ys_origin/`** — the **apworld** (its folder name is the AP world id). Items,
  locations, regions, and access logic for the generator. Room logic lives in
  `ys_origin/data/room_logic.json`; the location/item dataset in
  `ys_origin/data/`.
- **`mod/`** — the native **mod** (a `dinput8.dll` proxy). It embeds the
  Archipelago client (apclientpp), hooks the game's Direct3D 9 + event-script VM,
  suppresses vanilla item grants, applies received items, and draws the overlay.
- `client/` + `tools/` — the legacy external memory client and the
  reverse-engineering / dataset-extraction toolchain (`RE_FINDINGS.md`).

### Build the apworld
```powershell
# from the repo root — zip the ys_origin/ folder (top-level folder must be ys_origin)
Compress-Archive -Path ys_origin -DestinationPath ys_origin.zip -Force
Rename-Item ys_origin.zip ys_origin.apworld -Force
```

### Build the mod (Windows, 32-bit)
```powershell
cmake -S mod -B mod/build -A Win32
cmake --build mod/build --config Release
# -> mod/build/Release/dinput8.dll   (copy into the Ys Origin folder)
```

### Generate a seed from source
Put your yaml in `<Archipelago>/Players/`, copy `ys_origin/` into
`<Archipelago>/worlds/ys_origin/` (or install the `.apworld`), then run the
Archipelago generator. See [`ys_origin/docs/setup_en.md`](ys_origin/docs/setup_en.md).

> Reverse-engineering notes, the memory map, and the offline data pipeline are in
> [`RE_FINDINGS.md`](RE_FINDINGS.md).
