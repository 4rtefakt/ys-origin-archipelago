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
