"""Tests del módulo de bankroll y reglas duras."""
import sys
import tempfile
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import bankroll


def _fresh_state(initial=1000.0):
    return {
        "initial_mxn": initial,
        "current_mxn": initial,
        "stake_multiplier": 1.0,
        "paused_until": None,
        "history": [],
    }


def test_winning_pick_increases_bankroll():
    s = _fresh_state(1000)
    bankroll.apply_pick_result(s, stake=100, won=True, payout=180)
    assert s["current_mxn"] == 1080  # +80 ganancia neta


def test_losing_pick_decreases_bankroll():
    s = _fresh_state(1000)
    bankroll.apply_pick_result(s, stake=50, won=False)
    assert s["current_mxn"] == 950


def test_soft_pause_at_15_pct_drawdown():
    s = _fresh_state(1000)
    # Perder hasta 850 (15% drawdown)
    bankroll.apply_pick_result(s, stake=150, won=False)
    assert s["current_mxn"] == 850
    assert s["stake_multiplier"] == 0.625  # soft pause activo


def test_stop_loss_at_30_pct_drawdown():
    s = _fresh_state(1000)
    # Perder hasta 700 (30% drawdown)
    bankroll.apply_pick_result(s, stake=300, won=False)
    assert s["current_mxn"] == 700
    assert s["paused_until"] is not None  # pausa activa


def test_no_pause_when_just_below_15_pct():
    s = _fresh_state(1000)
    bankroll.apply_pick_result(s, stake=140, won=False)
    assert s["current_mxn"] == 860  # 14% drawdown
    assert s["stake_multiplier"] == 1.0
    assert s["paused_until"] is None


def test_history_entries_added():
    s = _fresh_state(1000)
    bankroll.apply_pick_result(s, stake=50, won=True, payout=95)
    bankroll.apply_pick_result(s, stake=30, won=False)
    # Cada apply agrega 1 entry; el threshold check puede agregar otro si dispara
    assert len(s["history"]) >= 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
