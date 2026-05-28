"""Tests del módulo de resolución de picks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import resolution
import mundial_data as md


def _make_match(home_goals, away_goals, status="FINISHED"):
    return md.Match(
        fd_id=1, utc_kickoff="2026-06-11T18:00:00Z",
        matchday=1, stage="GROUP_STAGE", group="A",
        home_id=1, home_name="A", home_tla="A",
        away_id=2, away_name="B", away_tla="B",
        status=status, home_goals=home_goals, away_goals=away_goals,
        venue="Estadio Azteca",
    )


def test_evaluate_over_25_won_when_total_3():
    match = _make_match(2, 1)
    pick = {"market": "goles_o25"}
    assert resolution._evaluate_pick(pick, match) is True


def test_evaluate_over_25_lost_when_total_2():
    match = _make_match(1, 1)
    pick = {"market": "goles_o25"}
    assert resolution._evaluate_pick(pick, match) is False


def test_evaluate_under_25_won_when_total_2():
    match = _make_match(1, 1)
    pick = {"market": "goles_u25"}
    assert resolution._evaluate_pick(pick, match) is True


def test_evaluate_over_35_lost_at_3_goals():
    match = _make_match(2, 1)
    pick = {"market": "goles_o35"}
    assert resolution._evaluate_pick(pick, match) is False


def test_evaluate_btts_si_won():
    match = _make_match(1, 1)
    pick = {"market": "btts_si"}
    assert resolution._evaluate_pick(pick, match) is True


def test_evaluate_btts_si_lost_when_clean_sheet():
    match = _make_match(3, 0)
    pick = {"market": "btts_si"}
    assert resolution._evaluate_pick(pick, match) is False


def test_evaluate_btts_no_won_when_clean_sheet():
    match = _make_match(3, 0)
    pick = {"market": "btts_no"}
    assert resolution._evaluate_pick(pick, match) is True


def test_evaluate_1x2_home_wins():
    match = _make_match(2, 1)
    pick_1 = {"market": "1x2_1"}
    pick_x = {"market": "1x2_x"}
    pick_2 = {"market": "1x2_2"}
    assert resolution._evaluate_pick(pick_1, match) is True
    assert resolution._evaluate_pick(pick_x, match) is False
    assert resolution._evaluate_pick(pick_2, match) is False


def test_evaluate_1x2_draw():
    match = _make_match(1, 1)
    pick_x = {"market": "1x2_x"}
    assert resolution._evaluate_pick(pick_x, match) is True


def test_evaluate_no_score_returns_none():
    match = _make_match(None, None, status="SCHEDULED")
    pick = {"market": "goles_o25"}
    assert resolution._evaluate_pick(pick, match) is None


def test_evaluate_corners_returns_none():
    """Corners no se pueden resolver solo con FD."""
    match = _make_match(2, 1)
    pick = {"market": "corners_o95"}
    assert resolution._evaluate_pick(pick, match) is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
