"""
resolution.py — Resuelve picks pendientes contra resultados reales

CRÍTICO para que el bot APRENDA. Sin este módulo:
  - bankroll.json nunca se actualiza con ganancias/pérdidas reales
  - learning_tracker.register_pick_outcome nunca se llama
  - El consecutive_losses counter no se incrementa → no hay auto-pausa

Flujo (Paso A del cron, antes de generar picks nuevos):
  1. Lee bets_history.json
  2. Identifica picks con status="placed" (apostados, no resueltos)
  3. Para cada uno, busca el partido en mundial_data por fd_match_id
  4. Si partido FINISHED:
     - Calcula si ganó/perdió según el mercado (O/U, BTTS, 1X2)
     - Si ganó: bankroll += stake × (odds - 1)
     - Si perdió: bankroll -= stake
     - Marca pick como "won" o "lost"
     - Notifica learning_tracker
  5. Persiste cambios

Si partido está POSTPONED/CANCELLED → marca pick como "void" y devuelve stake.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bankroll
import learning_tracker as lt
import mundial_data as md

BETS_HISTORY_FILE = Path(__file__).parent / "bets_history.json"


def _load_history() -> list[dict]:
    if not BETS_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(BETS_HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(history: list[dict]) -> None:
    BETS_HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _evaluate_pick(pick: dict, match: md.Match) -> Optional[bool]:
    """
    Devuelve True si ganó, False si perdió, None si void/no resoluble.
    Maneja O/U goles, BTTS, 1X2, corners (corners no resolvible solo con FD).
    """
    if match.home_goals is None or match.away_goals is None:
        return None

    total = match.home_goals + match.away_goals
    market = pick.get("market", "")

    # Goles O/U
    if market.startswith("goles_o") or market.startswith("goles_u"):
        # market = "goles_o25" → line = 2.5
        line_str = market.replace("goles_o", "").replace("goles_u", "")
        try:
            line = int(line_str) / 10.0
        except Exception:
            return None
        if market.startswith("goles_o"):
            return total > line
        else:
            return total < line

    # BTTS
    if market == "btts_si":
        return match.home_goals >= 1 and match.away_goals >= 1
    if market == "btts_no":
        return not (match.home_goals >= 1 and match.away_goals >= 1)

    # 1X2
    if market in ("1x2_1", "1x2_x", "1x2_2"):
        if match.home_goals > match.away_goals:
            return market == "1x2_1"
        elif match.home_goals == match.away_goals:
            return market == "1x2_x"
        else:
            return market == "1x2_2"

    # Corners: no resolvible solo con FD
    if market.startswith("corners_"):
        return None

    return None


def resolve_pending_picks() -> dict:
    """
    Paso principal: resuelve picks pendientes y actualiza bankroll + learning_tracker.
    Returns resumen { resolved: N, won: N, lost: N, void: N, pnl_mxn: X }
    """
    history = _load_history()
    if not history:
        return {"resolved": 0, "won": 0, "lost": 0, "void": 0, "pnl_mxn": 0.0}

    # Tomar todos los picks de todos los days_entry
    all_matches = md.get_all_matches(force=False)
    matches_by_id = {m.fd_id: m for m in all_matches}

    summary = {"resolved": 0, "won": 0, "lost": 0, "void": 0, "pnl_mxn": 0.0}
    state_lt = lt.load_state()
    bk = bankroll.load()

    for day_entry in history:
        picks = day_entry.get("picks", [])
        for pick in picks:
            status = pick.get("status", "placed" if pick.get("placed") else "skipped")
            # Solo resolvemos placed sin resultado todavía
            if status not in ("placed",):
                continue

            fd_id = pick.get("fd_match_id") or pick.get("fd_id")
            if not fd_id:
                continue

            match = matches_by_id.get(fd_id)
            if not match:
                continue

            # Partido cancelado/pospuesto → void
            if match.status in ("CANCELLED", "POSTPONED", "SUSPENDED"):
                pick["status"] = "void"
                pick["resolved_at"] = datetime.now(timezone.utc).isoformat()
                summary["void"] += 1
                summary["resolved"] += 1
                # Devolver stake al bankroll
                stake = float(pick.get("stake_mxn", 0))
                bankroll.apply_pick_result(bk, stake=stake, won=True, payout=stake)
                continue

            if not match.is_finished:
                continue

            won = _evaluate_pick(pick, match)
            if won is None:
                # Mercado no resoluble → marca como void
                pick["status"] = "void"
                pick["resolved_at"] = datetime.now(timezone.utc).isoformat()
                summary["void"] += 1
                summary["resolved"] += 1
                continue

            stake = float(pick.get("stake_mxn", 0))
            odds = float(pick.get("odds", 1.0))
            payout = stake * odds if won else 0.0
            pnl = (payout - stake) if won else -stake

            pick["status"] = "won" if won else "lost"
            pick["resolved_at"] = datetime.now(timezone.utc).isoformat()
            pick["final_score"] = f"{match.home_goals}-{match.away_goals}"
            pick["pnl_mxn"] = round(pnl, 2)

            bankroll.apply_pick_result(bk, stake=stake, won=won, payout=payout)
            lt.register_pick_outcome(state_lt, won=won)

            summary["resolved"] += 1
            summary["pnl_mxn"] += pnl
            if won:
                summary["won"] += 1
            else:
                summary["lost"] += 1

    if summary["resolved"] > 0:
        _save_history(history)
        lt.save_state(state_lt)
        bankroll.save(bk)

    return summary


if __name__ == "__main__":
    print("[resolution] Resolviendo picks pendientes...")
    summary = resolve_pending_picks()
    print(f"  Resueltos: {summary['resolved']} (W:{summary['won']} L:{summary['lost']} V:{summary['void']})")
    print(f"  P&L del día: ${summary['pnl_mxn']:+.2f} MXN")
