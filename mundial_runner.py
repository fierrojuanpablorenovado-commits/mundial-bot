"""
mundial_runner.py — Orquestador del cron diario del mundial-bot

PIPELINE COMPLETO (en orden):
  0. Reset auto-pausa expirada + check pause activa
  1. resolution.resolve_pending_picks() → resuelve picks de ayer (W/L/Void)
                                        → actualiza bankroll + learning_tracker
  2. learning_tracker.daily_refresh()    → recalibra modelo con resultados frescos
  3. mundial_odds.odds_provider_real     → catálogo PlayDoit con cuotas vivas
  4. analyze_mundial.analyze_day()       → genera picks del día
  5. Si DRY_RUN=true → solo persiste a bets_history, no apuesta
     Si DRY_RUN=false → loop sobre picks, place_bet_api por cada uno
  6. Genera daily_report.json estructurado
  7. notify.send() → respeta kill-switch (default OFF, solo stdout)

REGLAS DURAS aplicadas automáticamente:
  - bankroll cae 30% → pausa 48h
  - bankroll cae 15% → stake_multiplier 0.625
  - max 3 picks/día, max 1 por partido
  - edge [+6%, +35%]
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import bankroll
import learning_tracker as lt
import analyze_mundial as am
import mundial_data as md
import mundial_odds
import notify
import resolution

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")
BETS_HISTORY_FILE = Path(__file__).parent / "bets_history.json"
DAILY_REPORT_FILE = Path(__file__).parent / "daily_report.json"


def _load_bets_history() -> list:
    if not BETS_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(BETS_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_bets_history(history: list) -> None:
    BETS_HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_picks(picks: list, dry_run: bool) -> None:
    history = _load_bets_history()
    today_entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "picks": [
            {
                "fd_match_id": p.fd_match_id,
                "match": f"{p.home} vs {p.away}",
                "kickoff_utc": p.kickoff_utc,
                "matchday": p.matchday,
                "group": p.group,
                "market": p.market,
                "label": p.label,
                "model_prob": p.model_prob,
                "odds": p.odds,
                "edge_pct": p.edge_pct,
                "stake_mxn": p.stake_mxn,
                "confidence": p.confidence,
                "veto_reason": p.veto_reason,
                "status": "placed" if not dry_run else "dry_run",
                "placed_at": datetime.now(timezone.utc).isoformat(),
            }
            for p in picks
        ],
    }
    history.append(today_entry)
    _save_bets_history(history)


def _place_real_bets(picks: list) -> list:
    """Coloca las apuestas reales via altenar_api. Returns resultados."""
    import altenar_api
    results = []
    user = os.environ.get("PLAYDOIT_USER", "")
    pwd = os.environ.get("PLAYDOIT_PASS", "")
    if not user or not pwd:
        print("[runner] ⚠️ sin PLAYDOIT_USER/PASS — no se puede apostar real")
        return [{"pick": p, "success": False, "error": "no_credentials"} for p in picks]

    print(f"[runner] login PlayDoit...")
    token = altenar_api.api_login(user, pwd)
    if not token:
        print("[runner] ❌ login falló")
        return [{"pick": p, "success": False, "error": "login_failed"} for p in picks]

    for p in picks:
        # Obtenemos odd_id y market_id del provider
        odds_data = mundial_odds.odds_provider_real(
            next(m for m in md.get_all_matches() if m.fd_id == p.fd_match_id)
        )
        if not odds_data or "_altenar_markets" not in odds_data:
            results.append({"pick": p, "success": False, "error": "no_altenar_event"})
            continue
        market_info = odds_data["_altenar_markets"].get(p.market)
        if not market_info:
            results.append({"pick": p, "success": False, "error": "market_not_found"})
            continue
        event_id = odds_data["_altenar_event_id"]
        # Verificar que la cuota no haya cambiado >5%
        current_price = float(market_info["price"])
        original_price = float(p.odds)
        change_pct = abs(current_price - original_price) / original_price * 100
        if change_pct > 5:
            results.append({"pick": p, "success": False,
                            "error": f"odds_drift_{change_pct:.1f}%"})
            print(f"[runner] ⛔ cuota cambió {change_pct:.1f}% — cancelo pick")
            continue

        bet_result = altenar_api.place_bet_api(
            token=token, event_id=event_id,
            market_id=market_info["market_id"],
            odd_id=market_info["odd_id"],
            price=current_price, stake=p.stake_mxn,
        )
        results.append({"pick": p, "success": bet_result.get("success"),
                        "response": bet_result.get("response")})
    return results


def _write_daily_report(state: dict, bk: dict, picks: list, resolution_summary: dict,
                        all_matches_today: list[md.Match], place_results: list) -> None:
    report = {
        "date": datetime.now(timezone.utc).isoformat(),
        "dry_run": DRY_RUN,
        "bankroll": {
            "current_mxn": bk["current_mxn"],
            "initial_mxn": bk["initial_mxn"],
            "drawdown_pct": round((bk["initial_mxn"] - bk["current_mxn"]) / bk["initial_mxn"] * 100, 2),
            "stake_multiplier": bk["stake_multiplier"],
            "paused_until": bk.get("paused_until"),
        },
        "resolution_yesterday": resolution_summary,
        "model_global": state.get("global", {}),
        "today_matches": [
            {
                "fd_id": m.fd_id, "home": m.home_name, "away": m.away_name,
                "kickoff_utc": m.utc_kickoff, "venue": m.venue,
                "matchday": m.matchday, "group": m.group, "status": m.status,
            } for m in all_matches_today
        ],
        "picks_generated": len(picks),
        "picks": [
            {
                "match": f"{p.home} vs {p.away}", "kickoff": p.kickoff_utc,
                "market": p.market, "label": p.label, "odds": p.odds,
                "model_prob": round(p.model_prob, 4), "edge_pct": round(p.edge_pct, 2),
                "stake_mxn": p.stake_mxn, "confidence": p.confidence,
                "veto_reason": p.veto_reason,
            } for p in picks
        ],
        "place_results": [
            {"match": f"{r['pick'].home} vs {r['pick'].away}",
             "market": r['pick'].market, "stake": r['pick'].stake_mxn,
             "success": r.get("success"), "error": r.get("error"),
             "response_preview": str(r.get("response", ""))[:300]}
            for r in place_results
        ],
    }
    DAILY_REPORT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def _format_notification(picks: list, bk: dict, today_label: str,
                          resolution_summary: dict) -> str:
    header = f"🏆 MUNDIAL IA | {today_label} · {len(picks)} pick(s)"
    if DRY_RUN:
        header += " [DRY RUN]"

    lines = [header, ""]
    lines.append(f"💰 Bankroll: ${bk['current_mxn']:.0f} / ${bk['initial_mxn']:.0f}")
    lines.append(f"📊 Multiplier: {bk['stake_multiplier']:.3f}x")

    if resolution_summary.get("resolved", 0) > 0:
        lines.append(f"\n📈 Ayer: W:{resolution_summary['won']} "
                     f"L:{resolution_summary['lost']} V:{resolution_summary['void']} "
                     f"P&L: ${resolution_summary['pnl_mxn']:+.0f}")

    lines.append("")
    if not picks:
        lines.append("Sin picks hoy — disciplina antes que cantidad.")
        return "\n".join(lines)

    for i, p in enumerate(picks, 1):
        lines.append(f"{i}. {p.home} vs {p.away}")
        lines.append(f"   J{p.matchday} {p.group or 'KO'} · {p.kickoff_utc[11:16]} UTC")
        lines.append(f"   🎯 {p.label} @{p.odds:.2f}")
        lines.append(f"   📈 Edge +{p.edge_pct:.1f}% · P {p.model_prob*100:.0f}%")
        lines.append(f"   💵 ${p.stake_mxn:.0f} · {p.confidence}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 70)
    print(f"mundial_runner — {datetime.now(timezone.utc).isoformat()}")
    print(f"DRY_RUN={DRY_RUN}")
    print("=" * 70)

    # Paso 0: bankroll + check pausa
    bk = bankroll.load()
    bankroll.reset_pause_if_expired(bk)
    if bankroll.is_paused(bk):
        msg = f"🏆 MUNDIAL IA | PAUSADO hasta {bk['paused_until']}. Bankroll ${bk['current_mxn']:.0f}"
        print(msg)
        notify.send(msg)
        return 0

    # Paso 1: resolver picks de ayer
    print("\n[PASO 1] Resolviendo picks pendientes...")
    try:
        res_summary = resolution.resolve_pending_picks()
        print(f"  → resueltos {res_summary['resolved']} "
              f"(W:{res_summary['won']} L:{res_summary['lost']} V:{res_summary['void']}) "
              f"P&L ${res_summary['pnl_mxn']:+.2f}")
        # Recargar bankroll después de resolución
        bk = bankroll.load()
    except Exception as e:
        print(f"  ⚠️ resolución falló: {e}")
        res_summary = {"resolved": 0, "won": 0, "lost": 0, "void": 0, "pnl_mxn": 0.0}

    # Paso 2: actualizar modelo bayesiano
    print("\n[PASO 2] Actualizando modelo con resultados frescos...")
    try:
        state = lt.daily_refresh()
    except Exception as e:
        print(f"  ⚠️ daily_refresh falló: {e}")
        state = lt.load_state()

    # Paso 3-4: analizar día con odds reales
    today_cdt = datetime.now(timezone.utc) - timedelta(hours=6)
    today_label = today_cdt.strftime("%a %d-%b").lower()

    print(f"\n[PASO 3-4] Analizando día {today_cdt.date()}...")
    try:
        picks = am.analyze_day(
            date_local=today_cdt,
            bankroll_mxn=bk["current_mxn"],
            stake_multiplier=bk["stake_multiplier"],
            odds_provider=mundial_odds.odds_provider_real,
        )
    except Exception as e:
        err = f"🏆 MUNDIAL IA | ERROR analyze_day:\n{type(e).__name__}: {str(e)[:200]}"
        print(err); traceback.print_exc()
        notify.send(err)
        return 1

    # Paso 5: persistir + (opcional) place bets
    print(f"\n[PASO 5] {len(picks)} picks generados — persistiendo...")
    _persist_picks(picks, dry_run=DRY_RUN)

    place_results = []
    if not DRY_RUN and picks:
        print(f"[PASO 5b] Colocando {len(picks)} apuestas reales...")
        place_results = _place_real_bets(picks)
        won_count = sum(1 for r in place_results if r.get("success"))
        print(f"  → {won_count}/{len(picks)} apuestas colocadas exitosamente")

    # Paso 6: daily report estructurado
    all_today = md.get_matches_for_date(today_cdt) if picks else []
    _write_daily_report(state, bk, picks, res_summary, all_today, place_results)
    print(f"\n[PASO 6] daily_report.json escrito ({DAILY_REPORT_FILE.name})")

    # Paso 7: notificación (respeta kill-switch)
    msg = _format_notification(picks, bk, today_label, res_summary)
    print("\n" + msg)
    notify.send(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
