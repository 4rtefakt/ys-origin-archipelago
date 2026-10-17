"""Room gates checked against the game's scripts and walkthroughs (Oct 2026 audit).

Each test pins one gate the logic once got wrong, with where the game shows it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ys_origin_data_tables_gates", _ROOT / "ys_origin" / "data_tables.py")
dt = importlib.util.module_from_spec(_spec)
sys.modules["ys_origin_data_tables_gates"] = dt
_spec.loader.exec_module(dt)


def _edge(src: str, dst: str):
    return dt.edge_requirements().get((dt.scene_region(src), dt.scene_region(dst)))


def _open_edge(src: str, dst: str):
    """The open-mode requirement, or None when there is no such edge."""
    key = (dt.scene_region(src), dt.scene_region(dst))
    if key not in dt.open_scene_connections():
        return None
    return dt.OPEN_SCENE_EDGE_REQS.get(key, [])


def test_6f_small_room_needs_wind_in_and_the_dash_out():
    """S_2002 (6F Small Room): the wind jump gets you in, and the only way back is
    a timed platform the walkthroughs take with the dash (all three characters)."""
    assert _edge("S_2001", "S_2002") == ["Cerulean Flabellum", "Silver Bracelet"]
    assert _open_edge("S_2002", "S_2001") == ["Cerulean Flabellum", "Silver Bracelet"]


def test_8f_statue_is_past_the_long_dive():
    """S_2016 -> S_2100: the 8F statue is past a stretch of deep water too long to
    swim without the Water Dragon's Scales, both ways."""
    assert _edge("S_2016", "S_2100") == ["Water Dragon's Scales"]
    assert _open_edge("S_2100", "S_2016") == ["Water Dragon's Scales"]


def test_24f_bridges_are_raised_from_the_south():
    """S_6004 -> S_6052: the switch bridges are raised from the S_6004 side, so
    open mode has no way back from S_6052, and no spawn at S_6053 behind them."""
    assert _open_edge("S_6004", "S_6052") == []
    assert _open_edge("S_6052", "S_6004") is None
    assert 6053 not in dt.start_statue_candidates(25)
    assert 6082 in dt.start_statue_candidates(25)


def _pearl() -> str:
    return next(n for n in dt.LOCATION_REQUIRES if n.startswith("Corrupted Blood: Outer Corridor 3") and "Black Pearl" in n)


def test_yunicas_black_pearl_waits_for_the_s_5080_event():
    """S_5102: Yunica's Black Pearl event (EV_5102_YUNICA_1) needs flag 268, set only
    by S_EVT5080_YUNICA past the Zeruena ward (Silver Chimes). Hugo's and Toal's
    events need nothing, and Feena's idol needs the pearl event first."""
    pearl = _pearl()
    assert dt.LOCATION_REQUIRES[pearl] == []
    assert dt.LOCATION_REQUIRES_BY_CHARACTER["yunica"][pearl] == ["S_5080", "Silver Chimes"]
    idol = next(n for n in dt.LOCATION_REQUIRES if n.startswith("Silent Sands: Rado Inside 4 (Feena)"))
    assert dt.LOCATION_REQUIRES[idol] == ["S_4017", "S_5102"]
    assert dt.LOCATION_REQUIRES_BY_CHARACTER["yunica"][idol] == ["S_4017", "S_5102", "S_5080", "Silver Chimes"]


def test_s_5080_boss_check_needs_the_chimes():
    """Flag 423 is set once the four Zeruena die; they revive without the Silver Chimes."""
    assert dt.LOCATION_REQUIRES["Boss: Boss Room (S_5080)"] == ["Silver Chimes"]


def test_19f_crimson_vault_is_across_a_double_jump():
    """S_5005 -> S_5007: every walkthrough double-jumps the gap to the vault door."""
    assert _edge("S_5005", "S_5007") == ["Gold Bracelet"]
    assert _open_edge("S_5007", "S_5005") == ["Gold Bracelet"]


def test_18f_s_5003_is_not_entered_from_the_north():
    """S_5003's spear gates open from a switch south of them (TURNSWITCH02, flag
    445), so arriving from S_5100 reaches neither the switch nor the S_5004 drop."""
    assert _edge("S_5003", "S_5100") == ["Gold Bracelet"]
    assert _open_edge("S_5100", "S_5003") is None
