# Ys Origin room graph — manual mapping notes (Hugo)

Working notes for authoring `room_logic.json` (Part 3). Built from live play +
the player's spatial observations. Region key = SCENE id. Once a zone is fully
mapped and every location-bearing scene is reachable, it gets authored into
`room_logic.json` and verified beatable.

Legend: `A -> B [req]` = you can go from room A to room B, gated by `req`
(empty = free). `[wind]` = Ventus Bracelet. `???` = destination/requirement not
yet explored.

## Wailing Blue (S_10xx)

Scenes (scenelist names):
- S_1000  1F  1F Save (Search Party's Base, goddess statue)
- S_1001  2F  2F Path 1
- S_1004  2F  2F Path 2 (Wind Skill altar)
- S_1005  2F  2F Path 3 (Fork)
- S_1006  2F  2F Hidden Entrance
- S_1012  2F  2F Gemma Room
- S_1013  3F  3F Transfer Room
- S_1080  3F  3F Midboss
- S_1002  4F  4F Forward Passage 1
- S_1003  4F  4F Forward Passage 2
- S_1007  4F  4F Forward Passage 3
- S_1008  4F  4F Path 5 (Fork)
- S_1009  4F  4F Save (Forward)
- S_1010  4F  4F Path 4
- S_1014  4F  4F Forward Room
- S_1015  4F  4F Lower (Medal)
- S_1100  4F  4F Outer Corridor 2-1
- S_1101  --  Outer Corridor 1 (Forward)
- S_1011  5F  5F Save
- S_1097  5F  5F Stairs
- S_1099  5F  5F Velagunder (boss)
- S_1102  5F  5F Outer Corridor 2-2

### Edges (confirmed by play)
- S_1000 (1F Save) -> S_1001 (2F Path 1)        [free]   (assumed; confirm)
  NOTE S_1001 has TWO access tiers (internal one-way):
   * entering from the NORTH/elevated entrance (the S_1080 drop) reaches the
     WHOLE room incl. the elevated Bronze Key chest (#2, 0x36BE04) and the
     north exit;
   * entering from any OTHER entrance (ground, from 1F/2F) you CANNOT leave by
     the north and only reach the ground chest (#1 Panacea, 0x36BDD4).
   => model: the Bronze Key chest needs the elevated approach (wind + Blue Moon
      Crest + Epona). Likely split S_1001 into ground vs elevated sub-regions,
      or put that requirement on the chest-#2 location itself.
- S_1001 (2F Path 1) -> S_1004 (2F Path 2)      [free]   (assumed; confirm)
- S_1004 (2F Path 2) -> S_1005 (2F Path 3 Fork) [WIND]   ** CONFIRMED gate **
- S_1005 (2F Path 3 Fork): entered from the WEST (from S_1004). Flat room.
    - NE: open staircase -> S_1013 (3F Transfer Room), entering from its south   [free]
    - NW: LOCKED DOOR -> S_1006 (2F Hidden Entrance, from its south)   [BRONZE KEY]
    - (any access to this room reaches both the West and NE entrances)
- S_1006 (2F Hidden Entrance): entered from south (via S_1005 NW door).
    - No other exit VISIBLE normally. With the MASK OF EYES equipped, a hidden
      NORTH door becomes visible -> S_1012 (2F Gemma Room).
    - S_1006 -> S_1012 (2F Gemma Room)   [MASK OF EYES]
      (Mask of Eyes = ability item that reveals hidden doors, like wind is a
      traversal ability — a progression ability, randomized = Epona drop 0x36C094.)
- S_1012 (2F Gemma Room): DEAD-END (no further exits). Cutscene grants the
  Blue Necklace (0x37).
  ** DATA GAP: no event location exists for this cutscene reward (only the room
  sanity check). So the Blue Necklace is NOT randomized/suppressed -> player gets
  it vanilla. FIX (later): add an event location at S_1012 granting 0x37, with a
  dedicated detection flag the cutscene sets. (player flagged; patch later) **
- S_1013 (3F Transfer Room): entered from the SOUTH (from S_1005 NE staircase).
    - Has an OPEN north transition, BUT entering from the south triggers a story
      EVENT that grabs you mid-room and teleports you to S_1009 (4F Save Forward),
      blocking the north exit for now.
    - S_1013 (south entry, WITHOUT Blue Necklace) -> S_1009 (4F Save Forward)
      [auto event / free]
    - With the BLUE NECKLACE equipped you RESIST the teleport (cutscene instead)
      and can take the NORTH exit -> S_1010 (4F Path 4), entering its SW.
    - S_1013 (north) -> S_1010 (4F Path 4)   [BLUE NECKLACE]
      ** So the Blue Necklace gates the MAIN climb. Deep chain to get it:
         wind -> S_1005 -> [Blue Moon Crest forward loop] -> Epona (Mask of Eyes)
         -> S_1006 [Bronze Key] -> S_1012 [Mask of Eyes] = Blue Necklace. **
- S_1010 (4F Path 4): entered SW (from S_1013 north).
    - Free CHEST -> Leather Greaves (item 0x80 — see data gap; maybe not randomized).
    - NE exit -> S_1008 (4F Path 5 Fork)   [free]
- S_1008 (4F Path 5 Fork): the upper fork.
    - SE transition -> S_1015 (4F Lower (Medal))   [free]
        * S_1015 has a free CHEST = the BEAST MEDALLION (0x4E, flag 0x36BE14),
          the Wailing Blue -> Flooded Prison zone-gate item (vanilla location).
    - NW pressure plate [free] -> opens the NE door.
    - NE door -> S_1100 (4F Outer Corridor 2-1), entering its SW   [free]
- S_1015 (4F Lower (Medal)): Beast Medallion chest room. DEAD-END (confirmed).
- S_1100 (4F Outer Corridor 2-1): straight EAST -> S_1102 (5F Outer Corridor 2-2),
  entering its west   [free]
- S_1102 (5F Outer Corridor 2-2): entered west.
    - SE exit -> S_1011 (5F Save)   [free]
- S_1011 (5F Save): goddess statue (5F - Beast Chamber).
    - NE locked door -> S_1099 (5F Velagunder, boss fight)   [BEAST MEDALLION]
- S_1099 (5F Velagunder): zone boss (Velagunder).
    - North (after beating the boss) -> S_1097 (5F Stairs)   [free / post-boss]
- S_1097 (5F Stairs): LAST room of the zone, north of the boss -> on to Flooded
  Prison (6F). Zone transition gated by Beast Medallion (ZONE_GATE) + boss clear.

### START confirmed
S_1000 (1F Save) -> S_1001 (2F Path 1) -> S_1004 (2F Path 2): all FREE walking.
Zone entry: Menu -> "Wailing Blue" region -> S_1000.

### WAILING BLUE COMPLETE — full edge list (region key = scene)
"Wailing Blue" -> S_1000                       [free]   (zone entry)
S_1000 -> S_1001                               [free]
S_1001 -> S_1004                               [free]
S_1004 -> S_1005                               [Ventus Bracelet]   (WIND gate)
S_1005 -> S_1013                               [free]   (NE staircase)
S_1005 -> S_1006                               [Bronze Key]   (NW door)
S_1013 -> S_1009                               [free]   (default teleport)
S_1013 -> S_1010                               [Blue Necklace]   (resist teleport, N)
S_1009 -> S_1002                               [free]   (south)
S_1009 -> S_1080                               [Blue Moon Crest]   (altar drop)
S_1002 -> S_1003                               [free]
S_1003 -> S_1007                               [free]
S_1007 -> S_1101                               [free]
S_1101 -> S_1014                               [free]
S_1080 -> S_1001                               [free]   (north drop -> ELEVATED S_1001)
S_1006 -> S_1012                               [Mask of Eyes]
S_1010 -> S_1008                               [free]
S_1008 -> S_1015                               [free]   (SE)
S_1008 -> S_1100                               [free]   (NE, after free plate)
S_1100 -> S_1102                               [free]
S_1102 -> S_1011                               [free]
S_1011 -> S_1099                               [Beast Medallion]
S_1099 -> S_1097                               [free]   (post-boss)

Per-location overrides (reached from a different room than their scene):
- "Wailing Blue: 2F Path 1 #2" (Bronze Key chest, elevated) -> region S_1080
- "Wailing Blue: 2F Path 1 #3" (really the Epona/Mask-of-Eyes drop) -> region S_1080
- S_1009 (4F Save Forward): goddess statue (save) here.
    - South transition -> ???   ** UNEXPLORED **
    - North ALTAR for the Blue Moon Crest (0x6F). Placing the crest opens a hole
      in the room to DROP DOWN to a room below.
    - S_1009 -> (drop via altar) -> S_1080 (3F Midboss)   [BLUE MOON CREST]
- S_1080 (3F Midboss): big arena — fight EPONA (Hugo's 1st Epona fight).
    - Defeating Epona DROPS the Mask of Eyes (location flag 0x36C094).
      ** DATA FIX: our locations.json labels flag 0x36C094 as a CHEST "Wailing
      Blue: 2F Path 1 #3" (vanilla Mask of Eyes/Cleria Ring). It is actually the
      EPONA midboss DROP in S_1080, mis-scened to S_1001. Re-assign that location
      to scene S_1080 / room "3F Midboss". This is why the seed placed the Ventus
      Bracelet "at 2F Path 1 #3" yet it was unreachable — it's the Epona drop,
      behind wind + Blue Moon Crest + the midboss. **
    - North door -> drops back into S_1001 (2F Path 1) from the NORTH/elevated
      entrance -> reach the extra elevated CHEST there (vanilla Bronze Key,
      flag 0x36BE04 = "2F Path 1 #2"). [free, post-Epona]
    - So S_1001 (2F Path 1) is REVISITED from above: its elevated Bronze Key
      chest is gated behind wind + Blue Moon Crest + reaching/clearing S_1080.
    - South transition -> S_1002 (4F Forward Passage 1), entering at its NW   [free]
- S_1002 (4F Forward Passage 1): entered NW (from S_1009 south).
    - NE staircase -> S_1003 (4F Forward Passage 2), entering at its SW   [free]
- S_1003 (4F Forward Passage 2): entered SW (from S_1002 NE staircase).
    - Pressure plate [free] -> raises a platform -> reach the CHEST (in-room
      trigger, no item -> chest location is FREE once in S_1003)
    - SE transition -> S_1007 (4F Forward Passage 3)   [free]
- S_1007 (4F Forward Passage 3): entered from North (center).
    - West: a guard; kill it [free] -> opens a jump-pad -> reach a CHEST (Emerald)
      [free, in-room] and a pressure plate.
    - Pressure plate [free] -> opens the NE door.
    - NE door -> S_1101 (Outer Corridor 1 Forward)   [free]
- S_1101 (Outer Corridor 1 Forward): straight corridor -> S_1014 (4F Forward Room) [free]
- S_1014 (4F Forward Room): contains the CHEST with the BLUE MOON CREST (0x6F).
    - Opening that chest triggers an event: a Roo enters; talk to trade a Roda
      Fruit -> Cleria Ore (direct weapon upgrade; ore not stored in inventory).
    - VANILLA places the Blue Moon Crest here, on a free path from S_1005 (post-wind):
       S_1005 -NE-> S_1013 -event-> S_1009 -S-> S_1002 -NE-> S_1003 -SE-> S_1007
       -> S_1101 -> S_1014. But in RANDO the crest is shuffled, so this is only
       where the *location* is — NOT a logic requirement.

> **RULE PRINCIPLE (important):** every gate requirement is the randomized ITEM
> the vanilla game would use, NOT where vanilla puts it. The S_1009 drop needs
> the **Blue Moon Crest item** (wherever AP placed it); the fact vanilla hands it
> to you at S_1014 right before is irrelevant. AP fill guarantees the crest is
> reachable before the drop. (Wind only gates getting *into* this 4F area.)

### Keys / crests seen (randomized items that gate)
- Wind = Ventus Bracelet (0x74)
- Blue Moon Crest (0x6F): altar in S_1009 -> opens drop-down hole
- Keys (which door each opens TBD): Bronze 0x63, Marble 0x64, Amber 0x65,
  Crimson 0x66, Dragonbone 0x67, Obsidian 0x6E; Red Moon Crest 0x5C

### Data fixes found during mapping (apply when authoring)
1. Flag **0x36C094** is labeled chest "Wailing Blue: 2F Path 1 #3" (Mask of
   Eyes / Cleria Ring) but is really the **EPONA midboss drop in S_1080**.
   Re-scene it to S_1080 / "3F Midboss".
2. **Missing location:** the S_1012 Gemma Room cutscene grants the Blue Necklace
   (0x37) but has no event location -> not randomized/suppressed. Add it.
3. (watch for more mis-scened "chest" locations whose flag is really an
   event/boss drop — the script-scene heuristic can misplace them.)
4. **Leather Greaves = item 0x80** (S_1010 chest) is OUTSIDE the 128-entry
   INVINFO table (0x00-0x7F) -> not in items.json, so not randomized/suppressed
   (vanilla grant). Confirm whether 0x80+ ids need handling (a few gives use
   ids >= 0x80; previously flagged "unresolved").

### Open questions / to explore
- NW locked door in S_1005: which key? where does it lead?
- S_1013 north exit: reachable after the teleport event fires once? where to?
- S_1009 south transition + where the Blue Moon Crest drop lands.
- Where the 4F passages (S_1002/1003/1007/1008/1010/1014/1015) and 5F connect.
- 2F Hidden Entrance (S_1006), 2F Gemma Room (S_1012) — how reached?
- 3F Midboss (S_1080) and 5F Velagunder (S_1099) approach.
