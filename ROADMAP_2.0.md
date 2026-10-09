# Ys Origin × Archipelago — 2.0 Roadmap & Feedback Triage

This is the working plan for **2.0**, born from the community feedback on the AP
Discord after the v1.9.0 public drop. It sorts every request into what's
**doable now**, what's **already understood**, what's **unknown / needs design**,
and what's **blocked on reverse-engineering (RE)**. It stays on this dev branch
until the whole thing is stable enough to cut as 2.0.

Nothing here is a promise of a ship date — it's a filter, so effort lands on the
things that are both wanted and tractable.

---

## Legend

| Tag | Meaning |
|---|---|
| 🟢 **DOABLE** | Understood end-to-end, no new RE, mostly apworld/Python or wiring work. Can start today. |
| 🔵 **KNOWN** | The mechanism is already mapped (offsets/ops exist in `RE_FINDINGS.md` or the mod), just not wired into a feature yet. |
| 🟡 **DESIGN** | Technically reachable, but the open question is *what it should do*, not *can we*. Needs a decision before code. |
| 🟠 **NEEDS RE** | Blocked on finding offsets / structs / ops we don't have yet. Cost is dominated by RE, not coding. |
| ⚪ **OUT / NON-CODE** | Different game, community/admin action, or already handled. Tracked so it isn't lost, not planned as engineering. |

---

## Master triage table

| # | Request | Source | Bucket | 2.0? |
|---|---|---|---|---|
| 1 | User-editable **item classifications** (move to a file / yaml override) | M. | ✅ DONE | **Shipped on this branch** |
| 2 | **Goal reporting for Yunica/Hugo** (Dalles ending scene) | Known issue | 🟠 NEEDS RE | **Yes** — stability blocker |
| 3 | **Permanent stat-bonus items** (XP/STR/DEF pool items) | Release "doesn't work yet" | 🔵 KNOWN | **Yes** — offsets exist |
| 4 | **Enemy stat / difficulty scaling** (TTTD-style) | HothRaka | 🟠 NEEDS RE | Stretch |
| 5 | **Boost Mode** support | Release "doesn't work yet" | 🟠 NEEDS RE (partial) | Stretch |
| 6 | **Bestiary / enemy-entry checks** | Release "doesn't work yet" | 🟠 NEEDS RE | Later |
| 7 | **Potsanity** (breakables as checks) | Release "doesn't work yet" | 🟠 NEEDS RE | Later |
| 8 | **EXP-on-skip** handling polish | 4rtefakt (self) | 🟡 DESIGN | **Yes** — mostly exists |
| 9 | Logic completeness / route coverage | 4rtefakt (self) | 🟢 DOABLE | **Yes** — the real 2.0 gate |
| 10 | Felghana randomizer | RaindropDry | ⚪ OUT | No — different game |
| 11 | List on the AP google sheet / "is it stable?" | Linonrim | ⚪ NON-CODE | Gated on stability |
| 12 | Post to #apworld-news, request pin perms | Woli | ⚪ NON-CODE | On 2.0 cut |
| 13 | README spoiler tag | HothRaka | ⚪ DONE | Already fixed |

---

## The details

### 1. ✅ User-editable item classifications — *shipped on this branch*

> **Implemented.** New `item_classification_overrides` option (an `OptionDict`,
> name → `filler`/`useful`/`progression`/`trap`). Parsed once in
> `generate_early` (invalid names/tiers dropped + logged, never fatal) and
> applied as the **last** word in `create_item`, so it overrides every default
> including the Cleria-Ore / statue-warp promotions — a player may even downgrade
> a default-progression item when they know a skip makes it non-essential (fill
> then fails loudly rather than producing a broken seed). The default tiers are
> written out as a readable reference in the `Ys-Origin.yaml` comment (that's the
> "hard to parse" fix M. asked for). Covered by `tests/test_item_curation.py`.

Original analysis follows.


> *"is there any chance of moving the item classifications to the items file? i
> like to mess around with those so the less important progression items don't
> end up eating priority locations in my sessions, but it's kinda hard to parse
> as it is right now."* — M.

You already said yes to this ("i can add it and make that commented out by
default … i'll add that to next release"). It's the cleanest win in the list.

**Where it lives today:** classification is derived, not declared —
`ys_origin/data_tables.py` builds `_item_class` from each location's `"class"`
field in `data/locations.json`, and `item_classification()` layers hard rules on
top (goal/gate items forced `progression`; statue unlocks + progressive gear
forced `useful`). There is no single human-readable "here are the tiers" file,
which is exactly M.'s "hard to parse" complaint.

**Plan:**
- Add an `OptionList` (or `OptionDict`) option, e.g. `item_classification_overrides`,
  defaulting to `[]` and shipped **commented-out** in `Ys-Origin.yaml` with the
  full current tier list written out as the comment (so it doubles as the
  readable reference M. wants).
- Apply overrides in `item_classification()` as the **last** layer, *after* the
  forced rules — but guard the forced ones: never let a player downgrade a true
  progression/gate item (`GOAL_ITEM`, `GATE_ITEMS`) below `progression`, or the
  seed becomes unfillable. Overrides can freely retune everything else
  (filler↔useful, and non-essential progression → useful).
- A generated, always-in-sync reference dump (name → default tier) so the yaml
  comment never drifts from the code. `tools/` is the natural home for the
  generator.

**Effort:** small. **Risk:** low, if the fill-safety guard is in. Tests:
extend `tests/test_item_curation.py`.

---

### 2. 🟠 Goal reporting for Yunica / Hugo — *the top stability blocker*

> Known issue (release post): *"Goal reporting is verified on [Toal]'s route
> only. Yunica/Hugo end on [Dalles] and that ending scene isn't captured yet."*

This is the single most important 2.0 item, because "does the seed actually
*complete* for my character" is the bar between a demo and a real apworld.

**State today (`mod/src/hook_ap.cpp`):** goal fires on entering a scene in
`g_goal_scenes` (default `{7002}` = Toal's Darm ending). It's already been made
configurable — `goal_scene=` accepts a comma-separated list — and unlisted 7xxx
scenes reached after real gameplay now self-report loudly to the log. So the
*plumbing* is done; what's missing is the **actual scene id(s)** for the
Yunica/Hugo Dalles ending.

**Plan (pure RE, low code):**
- Capture the Dalles ending scene id for Yunica and for Hugo via the existing
  `tools/scenefind.py` / `scenelog.py` while playing each route to credits (the
  self-report log already tells us the candidate id).
- Fold the confirmed id(s) into the default `g_goal_scenes` set and the yaml
  default, keyed/documented per character.
- Bonus safety: if the ids differ per character, pick the default from the
  `character` option at connect rather than shipping a superset.

**Effort:** small code, real playtime. **Bucket is RE only because the id
must be observed live.** Blocks the "is it stable" answer (#11).

---

### 3. 🔵 Permanent stat-bonus items — *offsets already exist*

Listed under "what doesn't work yet," but this is closer than it reads. The
stat-drop item ids are already catalogued in `RE_FINDINGS.md`:

> `0x42–0x4D` = stat drops (Recovery / Strength / Defense / MP)

…and the player stat block (`0x76A72C..0x76A767`, STR/DEF/etc.) plus the
recompute path (`FUN_00420C40`) are mapped and already driven by the
level/weapon features. So a permanent +STR / +DEF / +MaxHP pool item would reuse
the **exact** pending-apply-on-EndScene architecture the weapon/level scaling
already uses.

**Open question (small):** vanilla stat drops are one-shot consumables that bump
a stat. To make them *permanent multiworld items* we need a persistent tally
(how many of each received) mirrored the way weapon tier is (`g_flags[0x94]`),
so a save reload re-applies the sum rather than double-dipping. That's the same
baseline/suppression discipline already in `client/suppression.py`.

**Plan:** add pool items (Progressive or flat STR/DEF/HP bonuses), a per-stat
received counter in slot state, and a pending-apply that writes
`stat_block + accumulated_bonus` on the main thread. Gate behind an option
(default off).

**Effort:** medium. **Bucket KNOWN** — no new RE expected; risk is the
persistence/recompute interaction, which the weapon feature already solved once.

---

### 4. 🟠 Enemy stat / difficulty scaling — *the most interesting stretch goal*

> *"Enemy stat scaling maybe? TTTD has a thing where all the enemies in a chapter
> will have their stats scaled and then which chapter it aims for in terms of
> difficulty is randomized … Idk how much you can really enforce stat scaling
> though."* — HothRaka

Your read was right: *"the enemies seemed to have a fixed level … maybe I can
nerf/buff their level to scale too."*

**What we have:** the enemy entity struct is partially known — `enemy+0x1c` is
the **base EXP** field (used live in the EXP hooks in `hook_vm.cpp`), and the
player-entity HP chain (`entity+0x98`) is mapped. We do **not** have enemy HP /
ATK / DEF / level offsets, nor how enemies get spawned/initialized per room.

**What's needed (RE):**
- Find the enemy stat fields (HP/ATK/DEF/level) relative to the enemy entity,
  the same way `tools/entfind.py` pinned player HP.
- Find where enemies are initialized on room load (a spawn table or per-scene
  init) — scaling has to hook *creation*, or it fights the game re-asserting
  vanilla stats, exactly like the equipment-mirror problem in `RE_FINDINGS.md`.
- Decide the scaling model (see design note).

**Design note:** true "aim a zone at another zone's difficulty" (the TTTD idea)
needs a per-zone difficulty knob applied at spawn. A cheaper first cut is a
**global enemy stat multiplier** (seed- or option-driven) — much less RE, gives
90% of the "randomized difficulty" feel, and is a natural stepping stone. Start
there; the per-zone version is a later escalation.

**Effort:** high (RE-dominated). **2.0:** stretch — ship the global multiplier
if the enemy struct falls quickly; defer per-zone scaling.

---

### 5. 🟠 Boost Mode — *partially unblocked*

Ys Origin's Boost gauge. Interesting because one boost-related offset is
**already in use**: the EXP award reads a boost factor at `0x76A5FC`
(`boost[0x76a5fc]` in `hook_vm.cpp`). That's a foothold, not the whole system —
we'd still need the gauge fill/drain state and the activation path to expose
"Boost Mode" as a mechanic or option.

**Effort:** medium RE. **2.0:** stretch. Lower priority than #2/#3 because no
one specifically asked for it — it's a self-identified gap, not community demand.

---

### 6 & 7. 🟠 Bestiary/enemy-entry checks & Potsanity — *coupled, defer*

Both are "more checks" features and both depend on RE we don't have:

- **Bestiary checks** need enemy-kill/first-encounter detection → the same enemy
  entity + a bestiary-flag array (unmapped). It *couples* with #4 (enemy RE), so
  they should be scheduled together — do the enemy struct once, get both.
- **Potsanity** needs the breakable-object break events. The event-flag array
  (`~+0x36BCB0`) already captures plates/doors/chests, so *if* pot breaks set
  flags there, the existing `tools/xso_catalog.py` pipeline could enumerate them
  offline — that's the thing to check first. If pots don't set persistent flags,
  it's a much bigger lift.

**Effort:** high. **2.0:** later. First cheap experiment: grep the offline
grant/flag dump for pot-break flags before committing.

---

### 8. 🟡 EXP-on-skip handling — *mostly already shipped, needs a default call*

You raised this yourself:

> *"if you skip to 18F, you deal 1dmg and get oneshot, so should you get your lvl
> raised automatically? Or XP boost on lower floors? or both? or make the logic
> only allow 18F once you have the weapon for it?"*

Good news: **all four options already exist** in `options.py` —
`level_scaling` (`off`/`level_floor`/`exp_multiplier`/`both`),
`weapon_requirements`, `starting_level`, and the EXP multiplier pair. This isn't
a "build it" item, it's a **"pick and document the right defaults"** item, plus
validating they feel right across routes.

**Plan:** decide the recommended combo (current default is
`exp_multiplier` + `weapon_requirements`), document *why* in the README so
players stop hitting the 1-damage wall by accident, and consider a single
"difficulty preset" that sets the cluster in one knob. Fold learnings from #9's
route testing.

**Effort:** small (docs + defaults). **Bucket DESIGN** — the code is done.

---

### 9. 🟢 Logic completeness / full route coverage — *the actual 2.0 gate*

> **Progress (offline).** `tests/test_logic_reachability.py` now audits the
> forward (non-random) logic without a game or the AP tree, reusing the module's
> own `req_satisfied` evaluator: (a) the AP invariant — all items in hand ⇒ every
> location + the goal reachable, per character, both weapon settings; (b) vanilla
> placement admits a real clear ordering (weapon gating off); (c) structural
> guards — every requirement/gate names a real item (the fail-closed typo trap),
> every graph endpoint is a real region, gated zones have no ungated backdoor.
> All green today, so they're regression guards. Still open: open-mode
> (random-spawn) reachability, weapon-gated fill ordering (needs an assumed-fill
> model), and live per-character playtests to credits.


> *"Logic isnt completely covered tho and I need to check more routes … its
> definetely not as polished as most."* — you

This is the thing that makes 2.0 *2.0*. Everything else is a feature; this is
correctness. It's 🟢 because it's understood work (extend/verify
`ys_origin/data/room_logic.json` + `rules.py`, play each character to credits),
just a lot of it.

**Plan:** systematic per-character reachability passes; expand
`tests/test_warp_limits.py`-style offline logic tests to cover the routes;
treat any "generated seed that can't be completed" as a release blocker. This
plus #2 are the two gates on answering Linonrim's "is it stable?" honestly.

---

### 14. ✅/🟡 Over-classified progression in warp mode — *implemented (accessibility-aware), pending in-game generate pass*

> **Correction to the first analysis.** The initial "still-critical 6" (Mask of
> Eyes, the Moon Crests, …) was measured under `accessibility: full` (every
> location reachable). Under `minimal` the real question is only "can you reach
> the goal," and there **no** progression item is critical: removing any one
> leaves the goal reachable, and the kept set (warps + Cleria Ore + Devil
> Medallion) reaches the goal *jointly* with every gate item removed. So the gates
> can be demoted and fill routes advancement through the warps.
>
> **Correction #2 (the AP accessibility model).** An earlier draft of this section
> assumed the legacy three-tier `locations`/`items`/`minimal` split, where `items`
> meant "all progression items reachable". That tier no longer exists: AP defines
> exactly `option_full = 0` and `option_minimal = 2`, with `items`, `locations` and
> `none` as **aliases**. So `accessibility: items` — the shipped default in
> `Ys-Origin.yaml` — *is* `full`, and demoting there would strand the side
> locations the room-gate core provably gates, failing generation. Aliases also
> never reach `current_key` (it reads `name_lookup`, built from `option_*` only),
> so that key is only ever `"full"` or `"minimal"`.
>
> **Implemented.** `generate_early` sets `lean_open_progression` = open mode AND
> `accessibility.current_key == "minimal"`; `create_item` then demotes every
> would-be-progression gate item (all except the goal, Cleria Ore, and the warp
> unlocks) to `useful`. Under `full`/`items` (or any unrecognised key) nothing is
> demoted, so a strict seed never risks a stranded location. Player overrides still
> win. Guarded by `tests/test_logic_criticality.py` (goal-reachability + joint
> lean-set tests). **Ship-gated on a real `generate` pass** (needs the AP tree,
> i.e. your machine) confirming open-mode seeds still fill and clear under
> `minimal` — and that the default `items` seed is unchanged from today.

Original (full-accessibility) analysis follows.

Player observation (confirmed): in open/warp mode you can beat a seed with only a
few warps + Cleria Ore + the Devil Medallion, levelling at each warp — so most of
the items tagged `progression` are never actually required.

**Ground truth (`tests/test_logic_criticality.py`).** A faithful offline model of
the open-mode warp graph (reusing `req_satisfied` + the real
`warp_edge_rules`/`interzone_climb_rules`/`open_scene_edge_requirements` builders)
computes, per item, whether removing it strands any location. Result is robust
across **every character and every spawn**, and stable for `max_warp_floors_skip`
∈ {0,5,10}:

- **19 of 26 progression items are never critical** in open mode — the five zone
  medallions, most keys, both dragon weapons, all three bracelets, Black Pearl,
  Blue Necklace, Cerulean Flabellum, Dreaming Idol. A chain of warps ascends the
  tower without ever climbing, so the climb-gating medallions drop off the path.
- **Irreducible critical core:** the goal (Devil Medallion) + the room-gate items
  warps can't bypass — Mask of Eyes (Cleria Ring for Toal), Blue/Red Moon Crest,
  Water Dragon's Scales, Evil Ring, Bronze Key.

This is **not a beatability bug** (over-tagging is safe — fill still guarantees
those items reachable); it's the over-classification M. complained about, from the
logic side, and it over-constrains fill.

**Planned change (clean + robust, needs your in-game generation pass):** make the
gate-item classification *derived from the active mode* — in open mode, demote the
provably-non-critical gate items to `useful` (keep the goal + critical core +
warps as progression); forward mode is unchanged (there you must climb, so they
stay progression). The criticality is character-dependent (Toal's Cleria Ring vs
Mask of Eyes), so it's computed per-world from the same logic, not a hardcoded
list — the test above guards that the demoted set never includes a truly-required
item. Ship-gated on a real `generate` pass confirming seeds still fill + clear.

---

### 10–13. ⚪ Out of scope / non-code (tracked, not engineered)

- **10 — Felghana randomizer** (RaindropDry): different game (*Oath in
  Felghana*). Your call stands — TODO-list, not 2.0. Noted so it isn't lost.
- **11 — "Is it stable? / not in the google sheets"** (Linonrim): this is the
  *outcome* of 2.0, not a task. Gate the AP-spreadsheet listing on #2 + #9
  landing. Answer honestly until then: "beta, playable, logic still filling in."
- **12 — #apworld-news post + pin perms** (Woli): do this when 2.0 is cut, not
  before — a stability-blocked project doesn't want more eyes yet. Draft the
  release post as part of the 2.0 checklist.
- **13 — README spoiler tag** (HothRaka): already fixed in-thread. No action.

---

## Suggested 2.0 sequencing

1. **#1 item-classification override** — promised, small, unblocks a player's
   workflow immediately. Ship-ready first.
2. **#2 Yunica/Hugo goal capture** + **#9 route logic** — the two correctness
   gates. Nothing calls itself "stable" until both are done.
3. **#3 permanent stat items** + **#8 EXP-default polish** — known-offset
   features that round out the "warp-ahead is fair" story.
4. **Stretch:** #4 global enemy multiplier (if the enemy struct maps quickly),
   then #5 Boost.
5. **Later / post-2.0:** #6 bestiary + #7 potsanity (do the shared enemy RE once,
   collect both), per-zone enemy scaling.
6. **On cut:** #12 announce, #11 request spreadsheet listing.

---

## RE shopping list (what unblocks the most)

The high-leverage unknown is the **enemy entity struct** — mapping HP/ATK/DEF/
level + the room-load spawn/init path unblocks #4, #6, and half of #5 at once.
That's the one deep-RE session with the best payoff. Everything else on the
"needs RE" list is either a single observed scene id (#2) or an offline
flag-dump grep (#7 first-pass).

---

## After 2.0.1 (added 2026-10-09)

### 15. 🔵 Lila Shell hint menu — *designed, not built*

**Idea (4rtefakt).** The Lila Shell normally calls an NPC for story advice, which
is mostly wrong in a randomizer. Using it should instead open a hint menu, in
the game's own UI, not an overlay.

**Flow.**
1. A normal dialogue box: "Hint points: 12. A hint costs 5."
2. The game's own menu window with the picker (below) and "Never mind".
3. Picking an entry sends `!hint <item>`; the answer arrives as a dialogue box
   and lands in the hint list.

**The picker** (so nobody types an item name): this slot's progression items
not yet received, ordered by where the tower first needs them
(`data_tables.ITEM_GATE_FLOOR` already has that depth; publish the order in
slot_data). First row, preselected: "Whatever I need next" = the top of that
list. The native menu holds 16 records plus a header, so a longer list needs a
"More..." row.

**How, retail mod.** Serve a generated script in place of the Shell's:
* Item use runs `StartScript("@Riranokaigara")` (`FUN_00434970`, name in `ecx`;
  `@` = `data\map\s_common\<name>.xso`, then `.z`; `0x40a460` formats the path,
  `0x5defa0` on the archive object `[0x765554]` checks it exists). Toal's
  `@DarkRiranokaigara` is unreachable in retail.
* Still to trace: the read of the file's bytes from the archive, which is where
  the generated script has to be fed in. A stored-block zlib stream avoids
  needing a compressor.
* The script uses only shipped ops (CleriaCore `WNDDIALOG_DRAW.md`):
  `0xd3 Window_Msg(speaker, face, name, text)` with inline strings (string-pool
  indices), `0xd7 Menu_Init`, `0xd8 Menu_Add(text, label)`, `0xd9
  Menu_Select(title)`, `0xda Menu_AssignCancel(label)`. A choice jumps to its
  label, where a `0x64 Flag_SetInt` on a free flag tells the mod which row was
  picked. XSO layout: `docs/formats/XSO.md` in CleriaCore (header, code words,
  string pool after the code, label table).
* The client already has the numbers: `get_hint_points()`,
  `get_hint_cost_points()` in apclientpp.

**How, CleriaCore port.** Two engine additions, then the mod wires them: an
"item used" event that covers the Shell (API 4's `item_used` fires for the
Panacea only), and a call that opens the game's native menu with entries the
mod supplies. Until then the mod's page can offer the same picker in place of
its type-a-name field.

**Apworld.** The Shell becomes a starting item (it is a pool item today, with
one location, `Corrupted Blood: Staircase — Lila Shell`, which then takes
filler). This changes seeds: ship it with a release, not a hotfix.

### 16. Player feedback, 2.0.1 (Discord, 2026-10-09)

| who | report | status |
|---|---|---|
| CaptainSlug | Received **Progressive Wind Skill** right at the start of a New Game (from `Statue: 1F Save (S_1000)`, and on a second try as the first item) and had no wind skill and no Cerulean Flabellum in the inventory. | **Solved from his log: an old `dinput8.dll` (1.9.x) on a 2.0.1 seed.** Every receipt logs `received 'Progressive Wind Skill' ... no g_flags index, skipped`, and none of the lines a 2.0 dll writes at connect are there. He updated the apworld and not the dll. Guard on master: the apworld requires client version 0.6.7 and the dll reports it, so an older dll is refused at connect; a refusal is now shown; `apworld_version` in slot_data lets the dll warn when the seed is newer than it. |
| Ferrene | Could use the **Lv 2 charged fire** skill with only one fire skill found. | Same area, probably a receipt counted twice; covered by the index-based count. Unconfirmed. |
| Ferrene | With dialogue skip on, stuck at the **Silver Chimes** scene with the goddess's portrait on screen until she warped away. | Open. Matches the standing `cutscene_skip=2` lead (the skip zeroes waits and can cut a scene's tail). |
| Ferrene | "Logic locked the Silver Chimes behind the Rado Tower purified Evil Ring checks, which need the Chimes." | **Not a logic error.** Nothing gates on the Zelkarons: `S_4014`'s ward only sets flag 393 (read by that room alone) and charges a held drained ring. The received Evil Ring is already charged and opens the door. The vanilla "the Evil Ring is charged" scene suggests otherwise; 2.0.1's withheld-item feed line is the mitigation. |
| Ferrene | Finished a full all-bosses run as Yunica (locked statues, random start). | The goal, random start and locks work end to end. |

**Also fixed on master after 2.0.1, unreleased:** the five SP chests no longer
pay their vanilla SP on top of the seed's item (`ap_sp_chest`); the Ruby/Topaz
rename is finished in `ABILITY_GRANTS`.
