"""
bankroll.py — Gestor de bankroll y reglas duras de auto-pausa

Persiste a bankroll_state.json:
{
  "initial_mxn": 1000.0,
  "current_mxn": 1000.0,
  "stake_multiplier": 1.0,
  "paused_until": null,
  "history": [
    {"date": "2026-06-11T13:00:00Z", "bankroll": 1000.0, "delta": 0, "reason": "init"}
  ]
}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

STATE_FILE = Path(__file__).parent / "bankroll_state.json"

INITIAL_DEFAULT = float(os.environ.get("BANKROLL_INITIAL", "1000"))
SOFT_PAUSE_PCT  = float(os.environ.get("SOFT_PAUSE_PCT", "15"))   # -15% → Kelly suave
STOP_LOSS_PCT   = float(os.environ.get("STOP_LOSS_PCT", "30"))    # -30% → pausa 48h


def load() -> dict:
    if not STATE_FILE.exists():
        state = {
            "initial_mxn": INITIAL_DEFAULT,
            "current_mxn": INITIAL_DEFAULT,
            "stake_multiplier": 1.0,
            "paused_until": None,
            "history": [{
                "date": datetime.now(timezone.utc).isoformat(),
                "bankroll": INITIAL_DEFAULT,
                "delta": 0,
                "reason": "init",
            }],
        }
        save(state)
        return state
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_paused(state: dict) -> bool:
    pu = state.get("paused_until")
    if not pu:
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(pu.replace("Z", "+00:00"))


def apply_pick_result(state: dict, stake: float, won: bool, payout: float = 0.0) -> dict:
    """payout = lo que se recibe si gana (stake × odds). Si pierde, delta = -stake."""
    delta = (payout - stake) if won else -stake
    state["current_mxn"] = round(state["current_mxn"] + delta, 2)
    state["history"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "bankroll": state["current_mxn"],
        "delta": round(delta, 2),
        "reason": "win" if won else "loss",
    })
    _check_thresholds(state)
    save(state)
    return state


def _check_thresholds(state: dict) -> None:
    """Aplica reglas duras de auto-pausa según drawdown del bankroll."""
    drawdown_pct = (state["initial_mxn"] - state["current_mxn"]) / state["initial_mxn"] * 100

    if drawdown_pct >= STOP_LOSS_PCT:
        # Stop-loss: pausa 48h
        until = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        state["paused_until"] = until
        state["stake_multiplier"] = 0.5
        state["history"].append({
            "date": datetime.now(timezone.utc).isoformat(),
            "bankroll": state["current_mxn"],
            "delta": 0,
            "reason": f"STOP_LOSS -{drawdown_pct:.1f}% → pausa 48h",
        })
    elif drawdown_pct >= SOFT_PAUSE_PCT:
        state["stake_multiplier"] = 0.625  # Kelly 1/5 → efectivo 1/8
        state["history"].append({
            "date": datetime.now(timezone.utc).isoformat(),
            "bankroll": state["current_mxn"],
            "delta": 0,
            "reason": f"SOFT_PAUSE -{drawdown_pct:.1f}% → multiplier 0.625",
        })


def reset_pause_if_expired(state: dict) -> None:
    pu = state.get("paused_until")
    if pu and not is_paused(state):
        state["paused_until"] = None
        save(state)


if __name__ == "__main__":
    s = load()
    print(json.dumps(s, indent=2))
