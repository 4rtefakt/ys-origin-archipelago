# Ys Origin — Archipelago Integration

An [Archipelago](https://archipelago.gg) multiworld integration for **Ys Origin**
(Steam, `yso_win.exe`, v1.1.1.0). It has two halves:

1. An **apworld** (`ys_origin/`) — defines the items, locations, regions, and
   access logic for the AP generator.
2. A **memory client** (`client/`) — attaches to the running game with
   `ReadProcessMemory` / `WriteProcessMemory`, detects location checks by polling
   memory, and writes received items back into the game.

No BepInEx, no DLL injection, no binary patching — purely external process
memory access via the Win32 API.

> **Status — vertical slice working end-to-end (Hugo).** The full Archipelago
> loop has been proven live against the game: opening a chest in-game sends a
> location check to a real Archipelago server, which awards the placed item, and
> the client writes it into the running game. Memory mechanism, offsets,
> apworld, and client all verified together. See
> [RE_FINDINGS.md](RE_FINDINGS.md) for the confirmed memory map.
>
> **Confirmed:** item-grant array (`+0x36BAxx`), event/location-flag array
> (`+0x36BDxx`, bidirectional), SP/level, equipment reads.
>
> **Full tower extracted offline + seeds generate.** The game archives are
> unpacked (`tools/ni_unpack.py`), the event-script bytecode disassembled
> (`tools/xso_dis.py`), and chests/items/scene-names/logic mined into
> `ys_origin/data/chests.json` (`tools/build_dataset.py`): **62 chests across all
> 7 zones, every item named** (`MISC/INVINFO.DAT`), with key→door/medallion gates
> (`tools/xso_logic.py`). The apworld now spans the whole tower and **generates a
> valid, beatable seed** (verified on Archipelago 0.6.8: 62 locations, multi-
> sphere medallion-gated logic). See [RE_FINDINGS.md](RE_FINDINGS.md).
>
> **Content replacement implemented.** The loop is no longer additive: the
> client now neutralizes the vanilla item a chest grants so the AP item
> *replaces* it (see [Content replacement](#content-replacement) below). Set
> `YSO_SUPPRESS=0` to fall back to the old additive behaviour.
>
> **Next:** map the rest of the Hugo route, then generalize to Yunica/Toal. The
> one open suppression gap is skill slots (e.g. Protective Bubble): they are left
> cosmetic because safely reverting one needs the equipped-skill slot, which is
> not yet mapped.

## Architecture

```
                Archipelago server (WebSocket)
                          ▲  │
            LocationChecks│  │ReceivedItems
                          │  ▼
                  client/ap_client.py            (CommonContext subclass)
                          │  ▲
        detect_checks()   │  │  apply_item()
                          ▼  │
                  client/game_state.py           (poll @ 500ms, diff, write)
                          │  ▲
                 read_*() │  │ write_*()
                          ▼  │
                  client/memory.py               (ctypes Win32 RPM/WPM)
                          │
                          ▼
                  yso_win.exe  (offsets: client/offsets.py)
```

The apworld (`ys_origin/`) runs only inside the AP **generator**; the client
(`client/`) runs alongside the **game** on the player's machine. They meet over
the AP network protocol: the world publishes its `location name -> id` map via
`slot_data['location_signals']`, and the client uses it to turn memory diffs into
`LocationChecks`.

## Repo layout

```
ys_origin/             # the apworld (folder name = AP world id)
  __init__.py          #   YsOriginWorld (World subclass)
  items.py             #   item table + ids + groups
  locations.py         #   location table + ids, grouped by region
  regions.py           #   Darm Tower floor regions + connections
  rules.py             #   access logic (spell/movement/oxygen gating)
  options.py           #   YAML options (character, goal, ...)
  docs/                #   game page + setup guide (shown on the AP website)
client/
  __init__.py
  memory.py            # ProcessMemory: ctypes RPM/WPM, typed read/write
  offsets.py           # all static offsets (UNKNOWN = None for TBD)
  game_state.py        # poll(), detect_checks(), apply_item()
  ap_client.py         # CommonContext subclass + game-watcher loop
tools/
  scan.py              # interactive value scanner (when you know the value)
  snapdiff.py          # snapshot-diff scanner (when you don't — flags, equipment)
  flaglog.py           # passive g_flags change-logger — play normally, it maps
                       #   every location flag + paired chest item to a CSV
requirements.txt
pyproject.toml
README.md
```

> Note: the spec called this folder `apworld/`, but an AP world's directory name
> becomes its identifier when zipped to `.apworld`, so it's named `ys_origin/`.

## Quick start

### 1. Run the offset scanner (fill in unknown offsets)

With Ys Origin running and a save loaded:

```powershell
python -m tools.scan        # from the repo root
```

The scanner attaches to `yso_win.exe`, validates the connection against the
known EXP anchor (`+0x7028C0`), then drops into a Cheat-Engine-style REPL:

```
scan int 5                 # find every int32 currently == 5  (an item count)
# ... change the value in-game (pick up another gem) ...
narrow 6                    # keep only addresses that now read 6
narrow 7                    # repeat until a few candidates remain
poke 9                      # (optional) test-write the sole survivor to confirm
save emerald_count          # persist it to client/offsets.json
```

Other commands: `float`/`int`/`uint`/`short`/`byte`/`double` types,
`changed` / `unchanged` (filter without knowing the value), `list`, `count`,
`offset` (dump survivors as a raw dict), `saved` / `forget <field>`, `anchor`,
`reset`, `help`.

Survivors inside the module image are reported as `yso_win.exe+0xXXXX` — those
are the stable static offsets. Survivors *outside* the module are flagged as
likely-dynamic (heap/stack) and `save` refuses them.

### Snapshot-diff scanner (for unknown values: flags, equipment, counters)

`tools/scan.py` works when you know the value to search for. For values whose
number or type you *don't* know — equipment IDs, progression flags, gem counts,
spell tiers — and which the game stores with several mirror copies, use the
snapshot-diff scanner:

```powershell
python -m tools.snapdiff
```

It captures whole-region snapshots and diffs them around a single deliberate
change. Because Ys Origin keeps the menu **paused**, you can snapshot, change one
thing, and snapshot again with nothing else drifting:

```
snap a                 # before
# ... equip a different armor in-game ...
snap b
diff a b changed       # cells that changed
# ... re-equip the original ...
snap c
narrow changed         # keep cells that toggled back (drops one-way noise)
narrow unchanged       # (do nothing first) drop self-ticking cells
xref a b               # show each candidate's value in a / b / live
save armor_id          # persist the winner
poke int 257           # test-write to tell the master copy from a mirror
```

Scope defaults to the module image (where the static save-state lives); use
`scope committed` to diff all memory, or `scope <start> <end>` for a custom
range. `save` and `poke` share the same `offsets.json` and field validation as
`scan.py`.

> **Master vs mirror:** many values (especially equipment) have several mirror
> copies. Reading any mirror is fine for *detection*; but to make a *write* take
> effect you must find the authoritative master — `poke` a candidate and check
> whether the game actually reacts (mirrors get overwritten on the next frame).

### Offset persistence

`save <field>` writes the confirmed module-relative offset into
**`client/offsets.json`** (a sidecar, committed to the repo). `offsets.py` loads
this file at import and overlays it onto the hard-coded defaults, so a saved
offset takes effect on the next client start with **no code editing** — and the
JSON is the project's accumulating offset map. `<field>` must be one of the names
in the `Offsets` dataclass (e.g. `double_jump`, `wind_spell`, `emerald_count`,
`weapon_level`, `current_floor`, `key_items_base`); the scanner lists them if you
mistype. Use `save <field> <index>` to pick from several candidates without
narrowing to one.

### 2. Build & install the apworld

```powershell
Compress-Archive -Path ys_origin -DestinationPath ys_origin.zip
Rename-Item ys_origin.zip ys_origin.apworld
# copy ys_origin.apworld into Archipelago/custom_worlds/
```

Then generate a seed from the AP Launcher (Ys Origin will appear in the game
list). See `ys_origin/docs/setup_en.md` for the full flow.

### 3. Run the client

```powershell
$env:AP_ROOT = "C:\path\to\Archipelago"   # your Archipelago checkout
python -m client.ap_client <host:port> <slot> [password]
```

The client subclasses Archipelago's `CommonContext`, so it needs the Archipelago
source on `PYTHONPATH` (provided via `AP_ROOT`) plus AP's deps (run it with the
Archipelago venv's Python). `<slot>` is your player name (e.g. `Hugo`); it's set
non-interactively so no prompt appears. Run as Administrator if attaching fails.

## Running the vertical slice end-to-end

Reproduces the proven loop (tested against Archipelago 0.6.8, Python 3.12):

```powershell
# in your Archipelago checkout, with its venv active:
#   - place this repo's ys_origin/ folder in worlds/  (or zip to custom_worlds/)
#   - put a player YAML in Players/  (game: Ys Origin, character: hugo)
python Generate.py --player_files_path Players --outputpath output    # makes AP_*.zip
python MultiServer.py --host 127.0.0.1 --port 38281 output\AP_*.zip   # host

# in this repo (game running, save loaded):
$env:AP_ROOT = "C:\path\to\Archipelago"
& C:\path\to\Archipelago\venv\Scripts\python.exe -m client.ap_client 127.0.0.1:38281 Hugo
```

Then trigger a mapped location in-game (open a chest / step on a plate). The
server logs `Hugo sent <item> ... (<location>)` and the client writes that item
into your game, while the vanilla item the chest would have given is suppressed
(see [Content replacement](#content-replacement)).

### Content replacement

The chest's location/event flag still flips (that is our check signal and plays
the normal cutscene/door), but the vanilla **item** it grants is reverted so the
AP item replaces it rather than stacking on top. There is no static per-chest
contents table to patch and we do no code injection, so this happens after the
fact at the unified `g_flags[]` item-array level (`client/suppression.py`):

- The client tracks a per-slot **baseline** = the value each item slot should
  have given only AP grants (primed from the save on attach). Every poll, a slot
  above its baseline is a vanilla grant and gets rewritten back down; a slot
  below it is legitimate consumption and lowers the baseline. AP grants raise the
  baseline (and write that value), so they are never mistaken for vanilla ones.
- You may see the vanilla item for up to one poll (~500 ms) before it vanishes.
- **Skill slots are left alone.** Lowering a skill entry (e.g. Protective Bubble)
  is unsafe — the game freezes if it later casts a skill whose entry is `-1`, and
  the equipped-skill slot needed to safely unequip it is not yet mapped. The key
  *item* that grants a skill (the Cerulean Flabellum) is a normal key item and is
  still reverted; only the granted skill slot stays (cosmetic).
- Set `YSO_SUPPRESS=0` to disable suppression and keep the additive loop.

> Setup gotchas: Archipelago's `Generate.py` needs `pkg_resources`
> (`pip install "setuptools<81"`). To avoid pulling every bundled world's
> dependencies, keep only `worlds/generic` and `worlds/ys_origin`. The apworld
> location names must exactly equal the client's `LOCATION_FLAG_OFFSETS` keys
> (and grantable item names the `ITEM_OFFSETS` keys) — that's how checks/grants
> map across the network.

## Memory layer notes

- `client/memory.py` is **pure `ctypes`** — no third-party dependency, works on
  any CPython 3.11+ regardless of wheel availability. `pymem` is offered as an
  optional drop-in (`pip install ys-origin-ap[pymem]`) but is not used by
  default; the public API (`read_int32`, `write_float`, ...) mirrors it.
- The game is a 64-bit build; pointer reads use 8-byte width.
- Every unmapped offset is `None`. Using one raises `OffsetNotMapped` with the
  field name, so received items never silently fail to apply.
- All reads tolerate the process going away (`is_alive()` guards the poller);
  the client reconnects to both the game and the AP server automatically.

## Known offsets

Confirmed by live RE against **v1.1.1.0** (32-bit, image base `0x400000`).
Full details, the array semantics, and the safety rules are in
**[RE_FINDINGS.md](RE_FINDINGS.md)**. Summary of the working model:

- **Grant items/skills/key-items** → write the item array (`~+0x36BAxx`), value
  ≥ 1 (**never clear to −1 — it freezes the game**). Confirmed: Roda Fruit
  `+0x36BA78`, Celcetan Panacea `+0x36BA80`, Cerulean Flabellum `+0x36BAC8`,
  Protective Bubble skill `+0x36BAEC`. (see `ITEM_OFFSETS`)
- **Detect a check** → read the event/location-flag array (`~+0x36BCB0`–`+0x36C0FC`,
  stable per-location indices). Confirmed flags for chests/altars/plates (see
  `LOCATION_FLAG_OFFSETS`). The array is **bidirectional** — writing a flag (then
  re-entering the room) forces world state (doors/plates).
- **Currency / level** → SP `+0x36A75C`, Level `+0x36A760` (both writable; level
  triggers a full stat recalc).
- **Equipment (read-only mirrors)** → armor `+0x34C0F0`, accessory `+0x33A4A8`
  (fine for detection; not writable masters).

Confirmed single-field offsets live in `client/offsets.json` (auto-loaded);
array entries live in the `ITEM_OFFSETS` / `LOCATION_FLAG_OFFSETS` registries in
`client/offsets.py`.

> **Note:** the offsets originally noted in this project (e.g. EXP at `+0x7028C0`
> as a float) came from a community Cheat Engine table for a **different build**
> and do **not** apply to v1.1.1.0. See RE_FINDINGS.md.

Still unmapped (map via `tools/snapdiff.py` as encountered): gems,
double_jump / dash, weapon/boots, current_floor, and the full per-location flag
index → name table.

## Development

```powershell
python -m client.memory      # imports cleanly (no AP needed)
python -m tools.scan         # needs the game running
```

The apworld modules import Archipelago symbols (`BaseClasses`, `Options`, ...),
so they only import inside an Archipelago tree — that's expected for an apworld.
To smoke-test generation, install the apworld and run AP's generator, or run
your test from within the Archipelago checkout.

## References

- AP `CommonClient`: <https://github.com/ArchipelagoMW/Archipelago/blob/main/CommonClient.py>
- AP `AutoWorld`: <https://github.com/ArchipelagoMW/Archipelago/blob/main/worlds/AutoWorld.py>
- Memory-client example (Super Metroid): <https://github.com/ArchipelagoMW/Archipelago/tree/main/worlds/sm>
- apworld spec: <https://github.com/ArchipelagoMW/Archipelago/blob/main/docs/apworld%20specification.md>
- Ys Origin Cheat Engine table: <https://fearlessrevolution.com/viewtopic.php?t=8573>
