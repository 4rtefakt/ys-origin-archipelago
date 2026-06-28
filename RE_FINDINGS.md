# Ys Origin — Reverse-Engineering Findings

Confirmed against **Ys Origin v1.1.1.0** (Steam, `yso_win.exe`) via live RE.
All offsets are **module-relative** (image base `0x400000`). Resolve an absolute
address with `mem.resolve(offset)` (= `base_address + offset`).

## Process / build facts

| Fact | Value |
|---|---|
| Architecture | **32-bit** (WOW64) — pointers are 4 bytes |
| Image base | `0x400000` (no ASLR; static offsets are stable across launches) |
| Module size | `0x41B000` (~4.3 MB) |

> **The community Cheat Engine table is a *different build*** ("Steam Apr 13
> 2018"). Its static `.data` offsets do **not** apply to v1.1.1.0 (the `.data`
> section is shifted ~`+0x30xxxx` → `+0x33xxxx`/`+0x34xxxx`), and e.g. EXP is a
> dynamic int32 here, not a static float. Its *AOB code patterns* and
> *entity-relative struct offsets* (HP=entity+0x98, jump flag=entity+0x388, …)
> still transfer, but the player-entity pointer hasn't been mapped.

## The two core arrays (the rando backbone)

### 1. Item / skill array — GRANT items here (`~+0x36BAxx`)

Each entry is an `int32`: `-1` = never obtained, `>= 1` = obtained / count.
**Items, key items, AND the skills key-items grant all live in this one array.**

- **Granting works** (write the entry to ≥1). Verified: set Panacea to 9 → showed ×9.
- Consumable *counts* may be written to any positive value.
- **Safety (refined by live testing):** the `-1` write *itself* is safe for items,
  key-items, **and** skills — the entry cleanly disappears, no freeze. The game
  only freezes when it later **casts a skill** whose entry is `-1` (dangling
  runtime-object deref; verified — restoring to 1 recovered it). So:
  - `apply_item` only ever grants (≥1), enforcing a hard floor (`GRANT_SAFE_MIN`).
  - **Suppression** (`client/suppression.py`) may revert anything *except* the
    skill slots in `SKILL_ITEMS` (Protective Bubble), which are left cosmetic to
    avoid the cast-freeze until the equipped-skill slot is mapped (so we could
    unequip before reverting). The Flabellum key item is reverted normally.

**Content replacement (suppression).** A chest grants its item via the event-
script VM (opcode `0x64` = `g_flags[idx] = value`, id/value baked into per-map
heap bytecode) — there is **no static contents table to patch**, and we do no
code injection. So the vanilla grant is neutralized *after the fact* at this
array: the client keeps a per-slot **baseline** (the value a slot should have
from AP grants alone) and on each poll reverts any slot that climbed above it,
while AP grants raise the baseline. See README "Content replacement".

Confirmed entries:

| Item | Offset | Notes |
|---|---|---|
| Roda Fruit | `+0x36BA78` | consumable |
| Celcetan Panacea | `+0x36BA80` | consumable |
| Cerulean Flabellum | `+0x36BAC8` | key item; grants the bubble skill |
| Protective Bubble | `+0x36BAEC` | the skill granted by the Flabellum |

### 2. Event / location-flag array — DETECT checks here (`~+0x36BCB0`–`+0x36C0FC`)

Each entry is an `int32` flag: `0` = not done, `1` = collected/triggered.
**Holds chests, item pickups, pressure plates, doors, and story events**, with
**stable per-location indices** (verified by gaps in the set flags — not
collection-order). Estimated bounds `+0x36BCB0`–`+0x36C0FC` (~276 flags).

- **Bidirectional (verified):** the game *reads* these on room load and *honors
  written values*. Reloaded a pre-plate save, wrote the plate flag to 1,
  re-entered the room → plate pressed + its door opened. So the client can both
  detect checks (read) and force world state (write doors/plates/events).
  Effect updates on **room re-entry**, not necessarily instantly.

Confirmed flags (assigned in area/progression order — adjacent indices):

| Location | Offset |
|---|---|
| Chest 1 — Panacea | `+0x36BDD4` |
| Flabellum Altar | `+0x36BDDC` |
| Pressure Plate (4F) | `+0x36BDE4` |
| Chest 2 — Roda Fruit (4F) | `+0x36BDE8` |
| Pressure Plate 2 — Door East (4F) | `+0x36BDF0` |
| (heavily-gated story flag) | `+0x36BCB8` (checked 116× in code) |

## Player stats block (`~+0x36A7xx`)

| Field | Offset | Type | Writable? |
|---|---|---|---|
| SP (blessing currency) | `+0x36A75C` | int32 | ✅ (cap ~9.99M; HUD truncates leading digit) |
| Level | `+0x36A760` | int32 | ✅ (writing triggers full HP/STR/DEF recalc) |
| DEF | `+0x36A740` | float | (read) |

`current_sp` and `current_level` are adjacent int32s. HP/STR/EXP are nearby as
floats (not individually pinned — not rando-critical).

## Equipment (read-only mirrors)

| Field | Offset | Notes |
|---|---|---|
| Equipped armor | `+0x34C0F0` | Ebony=256, Leather=257 |
| Equipped accessory | `+0x33A4A8` | one of 5 mirrors; **unreliable across game states** |

Equipment cells are **mirrors** — good for *reading* (detection) but writing
them doesn't re-equip (the game re-asserts from a master + recalc). Fine for the
client, which only reads them. The writable equip *master* was not hunted.

## Deferred / not cleanly mapped

- **Gems** (Emerald, etc.) — an Emerald increments a "Force Shield power"
  counter, but diffs were combat-noisy; not cleanly pinned. Re-map with minimal
  combat between snapshots.
- **Blessings** (SP-bought permanent upgrades) — scattered per-subsystem flags +
  transient "just purchased" flags (`+0x364798`/`+0x36498C`) that reset. No
  clean array. Armor blessing flag = `+0x36A684` (isolated, persistent). Each
  blessing needs its own isolated purchase to pin.
- **double_jump / dash, weapon_level, boots, current_floor** — unmapped; map via
  snapshot-diff as encountered.

## How offsets are discovered

1. **`tools/snapdiff.py`** (workhorse): snapshot the module, change ONE thing
   in-game (game paused in a menu where possible), snapshot again, diff. Toggle
   back / "unchanged while idle" passes strip noise. Confirmed-then-`save`.
2. **`tools/scan.py`**: value scanner for when you already know the number.
3. Both `save` to `client/offsets.json` (single fields) or you add array entries
   to the `ITEM_OFFSETS` / `LOCATION_FLAG_OFFSETS` registries in `offsets.py`.

> Key structural fact (from Ghidra): individual array entries have **no direct
> code xrefs** — they're set/read via a generic indexed get/set function. That's
> why byte-pattern scanning and xref hunting can't pin them, and why
> **snapshot-diff (observing the data change) is the reliable method.** Mapping
> a flag *index → location name* fundamentally requires play-through correlation;
> no static tool provides it.

## Ghidra (set up for future deep dives)

- JDK 21: `D:\ghidra-work\jdk\jdk-21.0.11+10`; Ghidra 12.1.2: `D:\ghidra-work\ghidra\…`
- Analyzed project kept at `D:\ghidra-work` (project `YsOproj`).
- Re-run a script without re-analysis:
  `analyzeHeadless D:\ghidra-work YsOproj -process yso_win.exe -noanalysis -postScript X.java -scriptPath D:\ghidra-work`
  (set `JAVA_HOME` to the JDK).
- **Ghidra 12 dropped Jython** — use **Java** GhidraScripts (`.java`), not `.py`.
- The game's text/data archive (`release/data_us.na`) is **encrypted**; item
  names load into `.data` at runtime (not in the on-disk exe).
