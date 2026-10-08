"""Access logic for the Ys Origin apworld.

Two layers, both applied here:

* **Coarse backstop** — boss-medallion gates on the zone->zone entrances (each
  zone needs the medallion from the zone below, where that medallion is in the
  pool). Guarantees a beatable seed even before room logic is authored.
* **Room logic** — per-edge item/skill requirements from ``room_logic.json``
  (e.g. the wind altar's far door needs the Ventus Bracelet). Authored
  zone-by-zone; un-authored scenes stay on the free, zone-gated default edge.

Completion = reaching the summit stairs (S_6097) through the Devil Medallion door.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rule_builder.rules import And, CanReachRegion, Has, HasAny, True_

from .data_tables import (
    CLERIA_ORE,
    FLOOR_BOSS_SCENES,
    PROGRESSIVE_SKILLS,
    CONNECTIONS,
    GOAL_ITEM,
    LOCATION_REQUIRES,
    RODA_FRUIT,
    ROO_LOCATIONS,
    active_gates,
    gear_upgrade_gates,
    char_name,
    character_req,
    edge_requirements,
    interzone_climb_rules,
    open_scene_edge_requirements,
    warp_edge_rules,
    zone_ore_requirements,
    scene_region,
    split_term,
)

if TYPE_CHECKING:
    from . import YsOriginWorld


def _all_of(terms: list):
    """AND the terms, or a free edge if there are none.

    Deliberately does NOT lean on ``And()``'s empty behaviour: that flipped from
    False_() to True_() in June 2026, i.e. AFTER the 0.6.7 release this world
    supports. On 0.6.7 an empty And() is False_(), which would silently make a
    "no requirements" edge impassable and the seed unbeatable. Being explicit is
    correct on every version.
    """
    if not terms:
        return True_()
    return And(*terms)


# artifact name -> the progressive chain that now carries it. When the chain is
# in the pool the artifact itself is not, so a rule naming it would gate on an
# item that can never be found — which made most of the tower unreachable and
# fill error out with 15 unplaced progression items.
_SKILL_SUBST = {str(d["artifact"]): prog for prog, d in PROGRESSIVE_SKILLS.items()}


# The three elemental artifacts Dalles' barriers demand (see the completion rule).
DALLES_SKILLS = ("Cerulean Flabellum", "Levinstrike Warhammer", "Crimson Lotusblade")

# Set once at the top of set_rules; empty when the option is off. Module-level so
# the two rule builders below need no signature change.
_ACTIVE_SUBST: dict = {}


def _sub(name: str) -> str:
    return _ACTIVE_SUBST.get(name, name)


def _req_rule(req: list):
    """Room-logic requirement expr -> Rule Builder rule.

    ``req`` is a list of terms ANDed together; a term that is itself a list is an
    OR-group. Mirrors the old ``req_satisfied`` evaluator exactly, including its
    fail-closed behaviour on unknown item names: ``Has`` reads the prog_items
    counter, so a name that is not a real item is simply never satisfied.
    """
    return _all_of(
        [HasAny(*[_sub(split_term(x)[0]) for x in t]) if isinstance(t, (list, tuple))
         else _has_term(t) for t in req]
    )


def _has_term(term: str):
    """``"Roda Fruit#3"`` -> Has("Roda Fruit", 3); a bare name -> Has(name)."""
    name, n = split_term(term)
    return Has(_sub(name), n) if n > 1 else Has(_sub(name))


def _gate_rule(item: str | None, ore_n: int, anchor: str | None = None):
    """(item AND ore-count AND reach-anchor), skipping the parts that don't apply."""
    terms = []
    if item is not None:
        terms.append(Has(_sub(item)))
    if ore_n:
        terms.append(Has(CLERIA_ORE, ore_n))
    if anchor is not None:
        # Rule Builder registers the indirect condition for this region itself,
        # so the warp edge is re-evaluated when the anchor floor becomes
        # reachable mid-sweep. No manual register_indirect_condition needed.
        terms.append(CanReachRegion(anchor))
    return _all_of(terms)


def set_rules(world: "YsOriginWorld") -> None:
    """Forward (linear) rules by default; the bidirectional warp-network rules
    when random spawn is on."""
    global _ACTIVE_SUBST
    _ACTIVE_SUBST = dict(_SKILL_SUBST) if world.options.progressive_skills.value else {}
    if getattr(world, "open_mode", False):
        _set_rules_open(world)
    else:
        _set_rules_forward(world)
    _set_blessing_price_rules(world)
    _set_roo_rules(world)
    _set_gear_upgrade_rules(world)
    _set_location_requires(world)


def _set_blessing_price_rules(world: "YsOriginWorld") -> None:
    """Gate each blessing shop slot behind the tower depth its PRICE implies.

    Without this, every shop slot is reachable in sphere 1 and the fill is free to
    park a sphere-1 progression item behind a five-figure SP wall — the playtest
    report was a 500k-SP slot holding one.

    The gate is REACHABILITY OF A FLOOR, not possession of a boss medallion.
    Medallions look like a depth proxy but aren't one: with statue warps on, the
    tower is traversable without them (_set_rules_open drops the medallion
    backbone entirely and gates the hub on statue unlocks + Cleria Ore + a floor
    anchor), so only the final medallion is really forced. Gating on a medallion
    would lock a player who warped to 22F out of a slot they can trivially
    afford. Reaching the floor anchor is the thing we actually mean, and it is
    correct in both graph modes because reachability already includes the warps.

    Cheap slots get no rule at all, so the bottom of the shop stays open from 1F.
    """
    for loc_name, anchor_region in getattr(world, "blessing_gates", {}).items():
        try:
            location = world.get_location(loc_name)
        except KeyError:
            continue        # category disabled for this world
        world.set_rule(location, CanReachRegion(anchor_region))


def _set_location_requires(world: "YsOriginWorld") -> None:
    """Checks that need more than reaching their own room.

    Two kinds of term. A scene (``S_xxxx``) must be reachable too: the Dreaming
    Idol chain (CleriaCore scripts) - Dino's gift needs Feena's S_4017 step
    (293/294), which only starts once Yunica has been to S_5080 (268,
    S_EVT5080_YUNICA); the S_4017 charging event needs the S_5102 Black Pearl
    event (269). A single region override cannot say "both rooms", so each
    listed scene becomes a CanReachRegion term. Anything else is an item, for a
    chest inside a reachable room that its own ledge or hidden bridge still
    gates; it is character-transformed like a room edge. Scenes this world does
    not create are skipped, and so are locations it does not have."""
    for loc_name in LOCATION_REQUIRES:
        if loc_name in ROO_LOCATIONS:
            continue                      # folded into the fruit rule
        try:
            location = world.get_location(loc_name)
        except KeyError:
            continue
        terms = _location_require_terms(world, loc_name)
        if terms:
            world.set_rule(location, terms[0] if len(terms) == 1 else And(*terms))


def _location_require_terms(world: "YsOriginWorld", loc_name: str) -> list:
    """The LOCATION_REQUIRES entry for one location, as rule terms."""
    reqs = LOCATION_REQUIRES.get(loc_name, [])
    live = set(world._region_names())
    scenes = [r for r in reqs if r.startswith("S_")]
    items = character_req([r for r in reqs if not r.startswith("S_")],
                          char_name(world.options))
    terms = [CanReachRegion(scene_region(s)) for s in scenes
             if scene_region(s) in live]
    return terms + [_has_term(t) for t in items]


def _set_roo_rules(world: "YsOriginWorld") -> None:
    """Gate each Roo trade behind the Roda Fruits it costs.

    Every Roo consumes ONE fruit (`0x69 Flag_SubInt` on 0x57 in its AGERU script)
    and vanilla stocks exactly six fruits for six Roos, so the supply is exact.
    The fruits are interchangeable and the player picks the order, so the correct
    encoding is by COUNT: the k-th Roo location requires k fruits. Without this,
    fill would treat the Roos as free sphere-1 locations and could strand
    progression behind fruits the player has no reason to have collected.

    Skipped silently when the `event` category is off for this seed.
    """
    for i, loc_name in enumerate(ROO_LOCATIONS, start=1):
        try:
            location = world.get_location(loc_name)
        except KeyError:
            continue
        world.set_rule(location, _all_of(
            [Has(RODA_FRUIT, i)] + _location_require_terms(world, loc_name)))


def _set_gear_upgrade_rules(world: "YsOriginWorld") -> None:
    """Gate "Strengthen <piece>" behind owning that piece.

    The upgrade writes a level into the raval array at the EQUIPPED piece's own
    item index, so the check simply cannot fire until you have the piece — and
    without a rule fill would treat all ten as free sphere-1 slots. The starting
    armor is worn from turn one and takes no gate. With progressive_armor on the
    specific piece is not in the pool at all, so the gate becomes the Nth step of
    that ladder instead.
    """
    progressive = bool(world.options.progressive_armor.value)
    for loc_name, (item, count) in gear_upgrade_gates(
            char_name(world.options), progressive).items():
        if not item:
            continue                      # starting gear: reachable immediately
        try:
            location = world.get_location(loc_name)
        except KeyError:
            continue                      # blessing category off for this seed
        world.set_rule(location, Has(item, count) if count > 1 else Has(item))


def _set_rules_forward(world: "YsOriginWorld") -> None:
    mw = world.multiworld
    player = world.player

    # Coarse: boss-medallion gate on each zone entrance, plus (optional) a Cleria
    # Ore = weapon-level requirement so the warp network can't strand you on a
    # floor your weapon can't dent. The generator then guarantees enough ore is
    # obtainable before each zone is in logic.
    gates = active_gates()
    ore_req = zone_ore_requirements(int(world.options.weapon_requirements.value))
    for zone in set(gates) | set(ore_req):
        srcs = [s for s, d in CONNECTIONS if d == zone]
        if not srcs:
            continue
        entrance = mw.get_entrance(f"{srcs[0]} -> {zone}", player)
        world.set_rule(entrance, _gate_rule(gates.get(zone), ore_req.get(zone, 0)))

    # Fine: per-edge room-logic requirements (items/skills), transformed for the
    # selected character (substitute/relax items they can't receive — e.g. Toal
    # gets Cleria Ring for Mask of Eyes, and lacks Blue Necklace/Evil Ring so
    # those edges relax to free). These sit on scene->scene (or zone->scene)
    # entrances and never collide with the zone gates (zone->zone entrances).
    char = char_name(world.options)
    for (src, dst), req in edge_requirements().items():
        creq = character_req(req, char)
        if not creq:
            continue  # fully relaxed for this character -> free edge
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        world.set_rule(entrance, _req_rule(creq))


def _set_rules_open(world: "YsOriginWorld") -> None:
    """Open (random-spawn) rules: per-edge requirements on the bidirectional room
    graph, Cleria-Ore + medallion gates on the inter-zone climbs, and the warp
    hub (spawn statue free; other statues need their unlock item + the warped-to
    zone's Cleria Ore). The coarse zone backbone / active_gates is dropped — the
    medallions live on the boss-door + inter-zone climb edges instead."""
    mw = world.multiworld
    player = world.player
    char = char_name(world.options)
    weapon_on = int(world.options.weapon_requirements.value)
    locks = bool(world.options.statue_warp_locks.value)

    def gate(src, dst, item, ore_n, anchor=None):
        """Attach an (item AND ore-count AND reach-anchor) access rule to an edge."""
        if item is None and ore_n == 0 and anchor is None:
            return                          # free edge
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        world.set_rule(entrance, _gate_rule(item, ore_n, anchor))

    # Per-scene room logic (bidirectional graph), character-transformed.
    for (src, dst), req in open_scene_edge_requirements().items():
        creq = character_req(req, char)
        if not creq:
            continue
        entrance = mw.get_entrance(f"{src} -> {dst}", player)
        world.set_rule(entrance, _req_rule(creq))

    # Inter-zone climbs: next zone's medallion + that zone's Cleria-Ore count.
    for (src, dst), (med, ore_n) in interzone_climb_rules(weapon_on).items():
        gate(src, dst, med, ore_n)

    # Warp hub: spawn statue free; others need their unlock item (when locks are
    # on), the warped-to zone's Cleria Ore (when weapon requirements are on), and a
    # reachable floor within max_warp_floors_skip of the destination (so a lone
    # unlock can't leapfrog you across the tower).
    max_skip = int(world.options.max_warp_floors_skip.value)
    for (src, dst), (unlock, ore_n, anchor) in warp_edge_rules(
            world.start_statue_scene, locks, weapon_on, max_skip).items():
        gate(src, dst, unlock, ore_n, anchor)


def set_completion_condition(world: "YsOriginWorld") -> None:
    # The Devil Medallion is not the win, the door it opens is: S_6053's
    # OPEN_THE_DOOR consumes it to open the way to S_6099 -> S_6097 -> the summit
    # (S_7000), where the final fight has no further gate. Holding the medallion
    # did not prove that door was reachable.
    #
    # Dalles (every character fights him at the summit; Toal goes on to Darm)
    # raises three barriers in his second form, each broken only by its own
    # element, so the fight cannot be won without all three skills.
    rule = And(Has(GOAL_ITEM), CanReachRegion(scene_region("S_6097")),
               *(Has(_sub(a)) for a in DALLES_SKILLS))
    if world.options.goal.value == world.options.goal.option_defeat_all_bosses:
        # Every floor boss's arena must be reachable as well; the mod sends the
        # goal only once all of them are dead (g_flags[220..225]) and the final
        # boss falls. Open mode can warp past a boss, so this is not implied by
        # reaching the Devil Medallion.
        rule = And(rule, *(CanReachRegion(scene_region(s)) for s in FLOOR_BOSS_SCENES))
    world.set_completion_rule(rule)
