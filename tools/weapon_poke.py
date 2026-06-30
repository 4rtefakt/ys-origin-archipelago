"""Live weapon-level test poke (no mod deploy needed).

Attaches to a running yso_win.exe and replicates what the mod's
``apply_weapon_level`` does, but from outside the process via RPM/WPM:
sets the persistent weapon-level record, the 4 weapon stat slots, the recompute
dirty bit, and pushes the recomputed stat block into the live player entity.

This validates the weapon-upgrade RE (see RE_FINDINGS.md "Weapon level / Cleria
Ore upgrade") WITHOUT rebuilding/deploying the mod.

What to watch, in order of certainty:
  1. Open the EQUIP screen -> the weapon level should immediately read higher.
     (The menu reads g_flags[0x94] directly, so this confirms the record cell.)
  2. Kill any enemy -> that triggers the stat recompute (FUN_00420C40 runs right
     after the EXP add), folding the new weapon stats into your damage. Hits that
     used to do 1 should now do real damage.

Usage (game must be RUNNING, in a room, ideally near a weak enemy):
    python tools/weapon_poke.py            # set weapon to max (tier value 10)
    python tools/weapon_poke.py 4          # set to the tier for 4 Cleria Ore
    python tools/weapon_poke.py --read     # just print current values
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from client.memory import ProcessMemory

# module-relative offsets (image base 0x400000); write_offset_* add base_address.
GFLAGS_WEAPON = 0x0036BB6C      # g_flags[0x94]  weapon-level record (tier value)
STAT_ARRAY = 0x0036A634        # stat slots; weapon = idx 0..3 (stride 8)
DIRTY_BITS = 0x0036B914        # |= 0x10 -> recompute re-applies from the table
ENTITY_PTR = 0x0034C09C        # *() = live player entity
STAT_BLOCK = 0x0036A72C        # -> entity+0x94 (recomputed HP/STR/DEF/...)

# Cleria Ore count -> g_flags[0x94] tier value (start=1, max=10).
TIER = [1, 2, 4, 6, 8, 10]


def read_state(m: ProcessMemory) -> None:
    wl = m.read_offset_int32(GFLAGS_WEAPON)
    slots = [m.read_offset_int32(STAT_ARRAY + i * 8) for i in range(4)]
    ent = m.read_offset_int32(ENTITY_PTR) & 0xFFFFFFFF
    dirty = m.read_offset_int32(DIRTY_BITS)
    print(f"  g_flags[0x94] (weapon record) = {wl}")
    print(f"  stat slots 0..3              = {slots}")
    print(f"  dirty bits (0x76B914)        = 0x{dirty & 0xFFFFFFFF:X}")
    print(f"  player entity ptr            = 0x{ent:08X}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    read_only = "--read" in args
    args = [a for a in args if a != "--read"]
    count = int(args[0]) if args else 5
    tier = TIER[min(max(count, 0), 5)]

    m = ProcessMemory.attach("yso_win.exe")
    print("Attached. Current state:")
    read_state(m)
    if read_only:
        return

    print(f"\nSetting weapon to Cleria-Ore-count {count} -> tier value {tier} ...")
    m.write_offset_int32(GFLAGS_WEAPON, tier)          # persistent record / menu
    for i in range(4):
        m.write_offset_int32(STAT_ARRAY + i * 8, tier)  # 4 weapon stat slots
    dirty = m.read_offset_int32(DIRTY_BITS)
    m.write_offset_int32(DIRTY_BITS, dirty | 0x10)      # request recompute

    # Push the current stat block into the live entity (mirrors VM sub-op 0x7F).
    ent = m.read_offset_int32(ENTITY_PTR) & 0xFFFFFFFF
    if ent:
        block = m.read_bytes(m.resolve(STAT_BLOCK), 0x40)
        m.write_bytes(ent + 0x94, block[: 0x3C])  # +0x94..+0xCC (HP/STR/DEF/...)
        print(f"  pushed stat block -> entity 0x{ent:08X}+0x94")
    else:
        print("  (no player entity resolved — push skipped)")

    print("\nNew state:")
    read_state(m)
    print(
        "\nNow: (1) open the EQUIP screen — weapon level should read higher;\n"
        "     (2) kill any enemy to trigger the recompute, then check your damage."
    )


if __name__ == "__main__":
    main()
