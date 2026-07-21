# Playtester Feedback Triage — July 2026 (Discord `#future-game-design`)

Triage of the playtest reports from CRIT MAGNET, Shiro, and others (07/17–07/19),
checked against the current code on `master`. Each item has a **verdict**
(real bug / not a bug / needs live repro / feature request), the **evidence**
(`file:line`), and a **recommended fix**. Nothing here is fixed yet — this is the
review pass; several fixes need in-game / live-RE verification the CI environment
can't do (no Archipelago tree, no running binary), which is called out per item.

Default config context that matters for reading this: **`character` defaults to
`hugo`** (`ys_origin/options.py:27`) and **`goal` defaults to `defeat_darm`**
(`ys_origin/options.py:30-35`).

---

## Priority 1 — real bugs, high impact

### P1.1 — Character-restricted story checks are unsendable (and can strand a multiworld)

**Reports:** "Wailing Blue: 1F Save — Dreaming Idol", "Rado Inside 4 (Feena) —
Dreaming Idol", "Corrupted Blood: Outer Corridor 3 — Black Pearl" never sent.

**Verdict: REAL BUG.** Locations are never filtered by character. A location is
"active" purely by *type* — `_active_locations()` → `locations_by_region(enabled)`
filters on `l["type"]` only (`ys_origin/__init__.py:115-116`,
`ys_origin/data_tables.py:661-667`). But several story-event locations only ever
fire on **one** character's route:

- **Dreaming Idol** locations are Yunica-only events (`character_items.json:106-108`;
  the second one's id is literally `EV_4017_YUNICA_3`, `locations.json:1266-1283`).
- **Black Pearl** is `["yunica","hugo"]`, never Toal (`character_items.json:44-47`,
  `locations.json:1247-1264`).

On the wrong character the underlying event never plays, so the detect flag never
flips and the check can never send. The reporter hitting all three is almost
certainly on **Toal** (Toal fails all three; Hugo fails only the two Idols).

**Compound severity:** because these locations stay active, AP can place *another
player's progression* on an unsendable check → the seed BKs. The same root cause
also silently affects two unreported locations (Silent Sands "Mask of Eyes Path" /
Evil Ring, and Wailing Blue "2F Gemma Room" / Blue Necklace).

**Recommended fix (pure Python, highest ROI):** filter locations by the seed's
character. Add a character predicate to `locations_by_region` / `_active_locations`
so a location is only created when its vanilla event exists for the chosen
character (reuse `location_vanilla_item(n, char)` / `character_items.json`
membership, mirroring how `vanilla_items(enabled, char, …)` already gates the item
pool at `ys_origin/__init__.py:124-126`). This resolves 1F/Rado Idol + Black Pearl
+ the two unreported cases at once. **Verify with a generate on each of the three
characters** (tests can't run here — no `Options` module).

### P1.2 — Hugo/Yunica clears never auto-report GOAL (the "released after Dalles" report)

**Report:** "Playing as Hugo, the game released normally following the cutscene
directly after killing Dalles."

**Verdict: REAL BUG (the on-screen ending is correct; the AP goal is the bug).**
Hugo and Yunica *do* end their story at Dalles — the credits/return-to-title after
the post-Dalles cutscene is vanilla-correct, not a softlock (`hook_ap.cpp:79-92`).
The bug is that goal auto-report is decided **only by scene number**, and the
default set is **Toal's ending only**: `static std::set<int> g_goal_scenes{7002}`
(`mod/src/hook_ap.cpp:92`). A Hugo/Yunica clear enters a *different* ending scene,
so `hook_ap.cpp:1143-1149` takes the "scene N is not a known ending" branch —
logs/overlays the scene number but **does not** send `StatusUpdate(GOAL)`. The
runtime never consults the `goal` slot-data value, and the Python client never
sends a status either. Net: the multiworld is stranded until a manual `!release`.

Because **`hugo` is the default character**, the default-configured run is exactly
the one whose goal never fires out of the box.

**Recommended fix (needs one live capture, then a 1-line default):** on the first
Hugo/Yunica clear, read the post-Dalles ending scene number the mod already
self-reports (overlay hint / `%TEMP%\yso_ap_mod.log`), then add it to
`g_goal_scenes` at `hook_ap.cpp:92` (players can set `goal_scene=<N>` in
`yso_ap.cfg` as an immediate workaround). Also worth correcting the stale default
`goal: defeat_darm` for the Hugo slice — Hugo never fights Darm (7099); his route
ends at Dalles, and the `Goal` option isn't consumed by the runtime check anyway
(`options.py:30-35`).

### P1.3 — Element weapons grant BOTH the vanilla power AND the AP item (double-dip)

**Report:** "wind element vanilla but spoiler log says shop … still giving an AP
item but also the vanilla one at the same time … should have found the Warhammer
(lightning) but didn't … randomization of these is not working."

**Verdict: REAL BUG.** The suppress/grant machinery only ever operates on the
*item* cell, never the separate *element-power* cell. The mod sinks a vanilla grant
only when `g_supp_item[idx]` is set (`mod/src/hook_vm.cpp:74-83`), and
`suppress_items` is built solely from each location's nominal vanilla item
(`ys_origin/__init__.py:320-325`). Elemental altars set **two** cells: the artifact
item (`0x6B/0x6C/0x6D`, which *is* suppressed) **and** the element-power/bracelet
cell (`Ventus/Terra/Ignis = 0x74/0x75/0x76`, which is **not** — those aren't any
location's vanilla item). So the power write passes through `DecideStore` and the
vanilla element is granted, while the box-open flag still fires the AP check → both
at once (`ys_origin/data_tables.py:809-815`, `tests/test_item_curation.py:38-41`).
The dead safeguard at `hook_vm.cpp:80-81` special-cases those indices but sits
inside the `if (g_supp_item[idx] …)` branch that's never true for them, so it never
runs.

**Also verify (possible mismap):** `SKILL_GRANTS` maps `Levinstrike Warhammer →
Terra Bracelet (0x75)` with a comment calling `0x75` "thunder", but
`RE_FINDINGS.md:258` labels `0x75` **Terra = earth**. Confirm each artifact→power
pairing so receiving the AP item grants the *right* element.

**Recommended fix (mod + Python; needs in-game verification):** add the element
power cells to the suppression set so the vanilla power is sinked when the altar
fires, and confirm the `SKILL_GRANTS` element pairing. See P1.4 — same root cause,
same fix shape.

### P1.4 — Double-jump ring: chest grants the effect, receiving the item does nothing

**Report:** "getting the ring that allows you to double jump does nothing. But
opening the chest where that ring usually is gives you the effect."

**Verdict: REAL BUG (same root cause as P1.3, both directions).** The double-jump
ability lives in a *separate, currently-unmapped* cell (`client/offsets.py:80-81`
`double_jump=UNKNOWN`; `RE_FINDINGS.md:113`). The "ring" is the **Gold Bracelet**,
item id `0x5B`, vanilla content of chest `S_4002/S_BOX01` "Double Jump"
(`items.json:88`, `locations.json:627-645`).

- **Chest grants it:** the chest sets the ability via its own cell, which the mod
  can't suppress (it isn't `0x5B`, and the ability cell isn't even a mapped
  g_flags index) → the vanilla double-jump leaks on.
- **Receiving it does nothing:** `on_items_received` calls `ap_give(0x5B, 1)`
  (`hook_ap.cpp:1040-1042`, `:570-577`), which only writes `g_flags[0x5B]`. There's
  no `SKILL_GRANTS` entry for Gold/Silver Bracelet (`data_tables.py:816-820` lists
  only the three elemental artifacts) and the ability cell is unmapped, so nothing
  enables it.

**Recommended fix (blocked on live RE first):** map the double_jump (and dash /
Silver Bracelet `0x5A`) ability cells via snapshot-diff (`tools/snapdiff.py`), then
give them the same treatment as P1.3 — add the ability cell to suppression and add
a `SKILL_GRANTS` mapping so the received item turns the ability on. Until the cell
is mapped this can't be fixed correctly.

### P1.5 — "Reach 18F/19F/20F/22F/23F" floor checks don't fire in the upper tower

**Reports:** "Reach 18F/19F/20F/22F/23F" and "My 20 Floor check never sent."

**Verdict: REAL BUG.** Two compounding defects, both concentrated in the upper
tower (exactly the reported range):

1. **The floor cell is warp-unreliable.** All floor checks detect on
   `0x36BC58` (= `g_flags[0xCF]`), which reads the *climbed-to* floor, not the
   *warped-to* one (`data_tables.py:355-361, 545-549`). The upper tower is reached
   largely by warping to statues (18/20/21F), so the cell never reads N.
2. **Crossing fires only on upward arrival from below.** The mod requires
   `cur == pf.floor_n && pf.floor_n > prev` (`hook_ap.cpp:1254`) and the Python
   client requires `curr.current_floor >= fl > pf` (`game_state.py:244`). Several of
   these floors are entered by **dropping down** ("20F Drop-Off",
   `locations.json:3767-3772`; "19F Drop-Off"), so `prev > N` and the `> prev`
   guard never passes.

**Recommended fix (needs in-game verification):** (a) fire on *any* transition that
lands on N (drop the `> prev` / `> pf` "from below only" constraint, keeping the
one-shot `g_poll_fired` guard so it fires once); and (b) for warps, seed the
floor-reached set from the warp destination floor when a statue warp completes
(the warp code already knows the destination floor). (a) alone recovers the
drop-off floors; (b) is needed for warp-only floors.

### P1.6 — "Outer Corridor 3 — Mantid Medallion" never sent

**Verdict: REAL BUG (detection-method gap).** This location is `method:"scene"
(S_5102)` — the build heuristic couldn't find a unique event flag and fell back to
scene (`locations.json:1228-1245`, `build_locations.py:241-242`). Consequences: it's
`is_excluded` → filler-only, and it **can never fire on the external Python client**
(no scene handling, `offsets.py:276-321`); on the mod it fires only if
`current_scene` actually reads 5102 during the grant. Item is `shared`, so this is
a detection-method gap, not character.

**Recommended fix (needs live capture):** capture the real per-character box-open
flag for that chest (via `tools/flaglog.py` while opening it) and convert the
location from `scene` to `flag` detection, so it fires on both clients.

---

## Priority 2 — real bug candidates, need live reproduction

### P2.1 — Five "shared" chests reported as not sending

**Reports:** "Flooded Prison: Water Dragon Protect #2", "Silent Sands: Boss Room
Key", "Flames of Guilt: 10F Path 2 #2", "Silent Sands: Double Jump #2",
"Corrupted Blood: Toal's Room".

**Verdict: DATA WIRING IS CORRECT — needs live repro.** All five are real chests;
each detect flag matches its extracted `box_flag`, all indices are `< 0x200` (in
VM-hook range), there are no flag-offset collisions, and all are published in
`location_signals`/`location_detect` (`locations.json:264-282, 795-813, 425-437,
646-658, 884-896` vs `master_chests.csv:14,42,23,34,47`). Note **"Boss Room Key"
holds the Creeper Medallion (progression)** — high impact if it truly fails.

Most likely cause if genuine: a **mod VM-hook coverage gap.** The mod fires flag
checks solely from the single grant-store splice at `0x567D17`
(`hook_vm.cpp:32,52-86`), and `hook_watch.cpp:3-8` documents that some chests'
box-flag writes may not pass through that one store. The Python client polls the
flag cell directly and would *not* miss them — a useful diagnostic split.

**Next step (not a code change yet):** ask the reporter which client they used and
for their `%TEMP%\yso_ap_mod.log`; look for an `ap_on_check` / `C <idx>` line when
each chest is opened. If the mod misses writes the Python client catches, the real
fix is broadening the mod's grant-store hook coverage (in `hook_vm.cpp`).

---

## Priority 3 — not bugs (working as designed / expected). Reply to testers.

### P3.1 — "Spending SP in the shop gives the normal bonus on top of the check"
**Intended and documented.** `options.py:227-228`: *"Purchases either way grant the
blessing effect AND the multiworld check."* Also `README.md:167-168, 183-184`. Code
does exactly this (`hook_ap.cpp:1495-1499`). Not a bug.

### P3.2 — "SP cost reduction in shop doesn't work"
**No such feature exists.** The only price options are `blessing_costs`
(`vanilla`/`shuffled`) and `blessing_cost_min`/`max` (`options.py:221-248`).
`blessing_cost_max` is applied at **generation time** and baked into the seed —
changing the yaml requires **regenerating**. The F5 overlay shop (the only place
prices are reduced) exists only under `blessing_costs: shuffled`; in `vanilla` mode
you buy from the game's own statue menu at fixed prices. Docs/UX gap, not a bug.

### P3.3 — "Progression balancing does not affect Vanilla SP (30k grind for Floor 4 blue crystal)"
**Not a bug.** `progression_balancing` is the *stock Archipelago* option
(`Ys-Origin.yaml:116`); it only reorders progression placement across the
multiworld and never touches in-game SP prices. Nothing in the world consumes it
for SP. The vanilla statue menu always charges vanilla prices — even under
`blessing_costs: shuffled`, only the F5 overlay shop is discounted
(`README.md:212`). To pay less, set `blessing_costs: shuffled` and buy via F5. Real
issue here is documentation/UX (nothing warns the vanilla menu stays at vanilla
prices).

### P3.4 — "The Roo checks are not randomized"
**Expected.** The only Roo locations are `Explore: Roo Start/End`, both
`type:room, method:scene`, **no items** (`locations.json:3128-3129, 3207-3208`).
`room` is filler-only even when enabled, and `RoomChecks` is **off by default**
(`options.py:58`). By default they don't exist; enabled, they only hold filler.

### P3.5 — "My friend got a levinhammer in the shop; I guess I missed mine"
**Normal multiworld behavior.** Shop slots legitimately hold arbitrary scouted
multiworld items, including progression like element weapons
(`tests/test_shop_tracker.py:32`). All 23 shop bit-slots map to unique verified bits
(`tools/blessing_bits.json` `COMPLETE`, matches `locations.json`), so no slot's
check silently fails. The player's own copy is placed elsewhere and arrives when
that location is checked. Nothing missed.

---

## Priority 4 — feature request

### P4.1 — Skippable dialogue / cutscenes ("some go on forever mashing A")
**~80% already built, undocumented.** The mod already implements cutscene
fast-forward: **hold Right Ctrl** and timed waits + camera/actor/fade waits collapse
(`hook_vm.cpp:335-429`, `Hook_Wait` `:359-369`, `Hook_WaitTail` + load-guard
`:387-406`, poll/hotkey `:415-429`); the New-Game intro auto-fast-forwards. What's
missing vs. the Switch "press X twice":

1. **Discoverability / binding** — the trigger is an undocumented hold-Right-Ctrl,
   not in either README, and gamepad users (the "mashing A" audience) have no button
   for it.
2. **Dialogue text auto-advance** — the `0xF3` text-advance op is deliberately left
   untouched (`hook_vm.cpp:343-348`) because it's shared with interactive prompts
   (shops, NPC choices, save). This is the "forever mashing A" part.

**Recommended path:**
- **Option A (low risk, ship first):** expose a rebindable FF hotkey via `yso_ap.cfg`
  (parser pattern at `menu.cpp:34-49`) and/or read a gamepad button in
  `cutscene_ff_poll()` from the DI state the mod already sees
  (`hook_input.cpp:63-70`); document it. Zero new RE, reuses `g_cutscene_ff`.
- **Option B (medium risk, opt-in):** also complete the `0xF3` dialog-advance op
  while FF is held, **context-gated** so it never auto-dismisses interactive
  prompts — gate on `scene == 2` (intro, safe today) or off when a shop/choice modal
  is active (`apshop::is_capturing()`, already referenced in `hook_input.cpp:20`).
  Respect the warning at `hook_vm.cpp:343-348`.

---

## Suggested order of work

1. **P1.1 character location filtering** — pure Python, highest ROI, fixes 5
   checks and a BK risk. Verify by generating a seed per character.
2. **P1.5(a) floor-crossing "from below only"** — small, self-contained; recovers
   the drop-off floors. P1.5(b) warp-seeding needs in-game verification.
3. **P1.2 Hugo goal scene** — capture the ending scene once live, add to the
   default set; quick and high-impact (default character).
4. **P1.3 element double-dip** — mod + Python; verify in-game.
5. **P4.1 Option A cutscene-skip exposure** — low-risk, well-received request.
6. **P1.4 double-jump / P1.6 Mantid / P2.1 five chests** — blocked on live RE or
   repro; capture data first.
7. **Docs pass** — P3.2/P3.3 are UX gaps: document that vanilla-menu SP is never
   rescaled and that `blessing_cost_*` is generation-time, and add the cutscene-FF
   hotkey to the README.
