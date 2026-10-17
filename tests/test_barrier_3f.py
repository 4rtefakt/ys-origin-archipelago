"""The 3F barrier (S_1013, "3F Transfer Room") and the first trip to 4F.

"3f transfer might be bugged for me as hugo" (Discord, Oct 2026): the barrier
zaps Yunica and Hugo to 4F (S_1009) unless the Blue Necklace is equipped, and
breaks for good when it is. Only that zap leads to the 4F rooms, and vanilla
hands out the necklace after them; a seed can give it first. The mod therefore
answers "not equipped" for the necklace in S_1013 until S_1009 has been entered
(hook_vm.cpp Hook_IsEquip), and puts back a barrier that a save already broke
too early (hook_ap.cpp repair_3f_barrier).

These tests pin the script facts both rest on, from the extracted XSO corpus,
and the hooked function's entry in the exe. Skipped when the game data is not
present (CI has no game assets).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = Path(r"C:\Users\kores\repos\YsOrigin-CleriaCore\assets\MAP")
_EXE = Path(r"D:\SteamLibrary\steamapps\common\Ys Origin\yso_win.exe")

FLAG_EQ, FLAG_SET, IS_EQUIP = 0x5F, 0x64, 0x8C
NECKLACE, BROKEN, SEEN_4F = 55, 444, 310


def _ops(rel: str):
    if not _CORPUS.is_dir():
        pytest.skip("XSO corpus not available")
    sys.path.insert(0, str(_ROOT))
    from tools.xso_dis import XSO
    p = _CORPUS / rel
    return [(i.sub, i.operands) for i in XSO(p.read_bytes(), p.name).disasm()
            if i.sub is not None]


def _corpus_writes(flag: int):
    """Every gameplay script that stores to g_flags[flag] (the S_0100 debug
    room's flag-setting scripts left out)."""
    if not _CORPUS.is_dir():
        pytest.skip("XSO corpus not available")
    sys.path.insert(0, str(_ROOT))
    from tools.xso_dis import XSO
    out = set()
    for p in _CORPUS.rglob("*.XSO"):
        rel = p.relative_to(_CORPUS).as_posix()
        if rel.startswith("S_01/") or "TALKDEBUG" in rel:
            continue
        try:
            x = XSO(p.read_bytes(), p.name)
        except ValueError:
            continue
        for i in x.disasm():
            if i.sub == FLAG_SET and i.operands[:1] == [flag]:
                out.add((rel, i.operands[1]))
    return out


def test_the_barrier_tests_the_necklace_and_breaks_for_good():
    ev = _ops("S_10/S_1013/S_EVT1013.XSO")
    assert (IS_EQUIP, [NECKLACE]) in ev
    assert (FLAG_SET, [BROKEN, 1]) in ev
    assert (FLAG_EQ, [141, 1]) in ev          # Toal: Boost instead
    root = _ops("S_10/S_1013/S_1013.XSO")
    assert (FLAG_EQ, [BROKEN, 0]) in root      # the barrier only while 444 == 0


def test_444_is_written_only_by_the_barrier_event():
    """So 444 = 1 means the barrier event broke it, and putting it back to 0
    only brings the barrier back."""
    assert _corpus_writes(BROKEN) == {("S_10/S_1013/S_EVT1013.XSO", 1)}


def test_310_means_s1009_was_entered():
    """S_1009's root sets 310 on every entry, and nothing else writes it: 310 == 0
    is "never been to 4F's statue room", the condition both fixes key on."""
    assert (FLAG_SET, [SEEN_4F, 1]) in _ops("S_10/S_1009/S_1009.XSO")
    assert _corpus_writes(SEEN_4F) == {("S_10/S_1009/S_1009.XSO", 1)}


def test_the_hooked_equip_test_is_where_the_mod_expects_it():
    """hook_vm.cpp detours FUN_00443CE0 (__fastcall, ECX = item id): yso_win.exe
    v1.1.1.0 starts it with `cmp ecx, -1`, and the event VM's 0x8C handler calls
    it at 0x5689EA."""
    if not _EXE.is_file():
        pytest.skip("yso_win.exe not available")
    b = _EXE.read_bytes()
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    nsec = struct.unpack_from("<H", b, pe + 6)[0]
    opt = struct.unpack_from("<H", b, pe + 20)[0]
    base = struct.unpack_from("<I", b, pe + 24 + 28)[0]

    def at(va: int, n: int) -> bytes:
        for k in range(nsec):
            s = pe + 24 + opt + 40 * k
            vs, rva, rs, raw = struct.unpack_from("<IIII", b, s + 8)
            if rva <= va - base < rva + max(vs, rs):
                o = va - base - rva + raw
                return b[o:o + n]
        raise AssertionError(hex(va))

    assert at(0x443CE0, 5) == bytes.fromhex("83F9FF7429")
    call = at(0x5689EA, 5)
    assert call[0] == 0xE8
    assert 0x5689EA + 5 + struct.unpack_from("<i", call, 1)[0] == 0x443CE0


def test_the_mod_wires_both_fixes():
    vm = (_ROOT / "mod" / "src" / "hook_vm.cpp").read_text(encoding="utf-8")
    assert "kIsEquip = 0x00443CE0" in vm
    assert "f[0x1F9] == 1013 && f[310] == 0 && f[444] == 0" in vm
    assert "MH_CreateHook((void*)kIsEquip" in vm
    ap = (_ROOT / "mod" / "src" / "hook_ap.cpp").read_text(encoding="utf-8")
    assert "repair_3f_barrier(scene);" in ap
