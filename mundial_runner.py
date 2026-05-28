"""
mundial_runner.py — Orquestador del cron diario

Flujo:
  1. Reset auto-pausa si ya expiró
  2. Si está pausado → manda WhatsApp informando y termina
  3. learning_tracker.daily_refresh() actualiza model_state con resultados de ayer
  4. analyze_mundial.analyze_day() genera picks con stake_multiplier vigente
  5. Si DRY_RUN=true → solo WhatsApp con picks simulados
  6. Si DRY_RUN=false → coloca apuestas via altenar_api
  7. WhatsApp resumen final
  8. Guarda picks del día a bets_history.json
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
import whatsapp


DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")
BETS_HISTORY_FILE = Path(__file__).parent / "bets_history.json"

# Si JP no quiere odds reales hoy (porque PlayDoit aún no abre líneas), el bot manda
# análisis sin apuestas. Cuando se conecta a Altenar API real, se cambia esta función.
def _odds_provider_stub(match):
    """
    TODO: conectar a altenar_api.fetch_odds(match) cuando se mapeen los eventos.
    Por ahora retorna None → analyze_mundial saltará todos los partidos.
    """
    return None


def _save_picks_history(picks: list) -> None:
    history = []
    if BETS_HISTORY_FILE.exists():
        try:
            history = json.loads(BETS_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []
    today_entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "dry_run": DRY_RUN,
        "picks": [
            {
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
            }
            for p in picks
        ],
    }
    history.append(today_entry)
    BETS_HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False),
                                  encoding="utf-8")


def _format_whatsapp(picks: list, today_label: str, bk_state: dict) -> str:
    header = f"🏆 MUNDIAL IA | {today_label} · {len(picks)} pick{'s' if len(picks) != 1 else ''}"
    if DRY_RUN:
        header += " (DRY RUN)"
    lines = [header, ""]
    lines.append(f"💰 Bankroll: ${bk_state['current_mxn']:.0f} MXN")
    lines.append(f"📊 Multiplier: {bk_state['stake_multiplier']:.3f}x")
    lines.append("")

    if not picks:
        lines.append("Sin picks hoy — disciplina antes que cantidad.")
        return "\n".join(lines)

    for i, p in enumerate(picks, 1):
        lines.append(f"{i}. {p.home} vs {p.away}")
        lines.append(f"   📅 J{p.matchday} {p.group or 'KO'} · Kickoff {p.kickoff_utc[11:16]} UTC")
        lines.append(f"   🎯 {p.label} @{p.odds:.2f}")
        lines.append(f"   📈 Edge +{p.edge_pct:.1f}% · P modelo {p.model_prob*100:.0f}%")
        lines.append(f"   💵 Stake ${p.stake_mxn:.0f} · {p.confidence}")
        if p.veto_reason and p.veto_reason not in ("OK", "pending", "veto-skipped (no OPENAI_API_KEY)"):
            lines.append(f"   🧠 IA: {p.veto_reason[:80]}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    print("=" * 70)
    print(f"mundial_runner — {datetime.now(timezone.utc).isoformat()}")
    print(f"DRY_RUN={DRY_RUN}")
    print("=" * 70)

    bk = bankroll.load()
    bankroll.reset_pause_if_expired(bk)

    if bankroll.is_paused(bk):
        msg = (f"🏆 MUNDIAL IA | PAUSADO\n"
               f"Auto-pausa activa hasta {bk['paused_until']}.\n"
               f"Bankroll: ${bk['current_mxn']:.0f} MXN "
               f"(de ${bk['initial_mxn']:.0f}).")
        print(msg)
        whatsapp.send(msg)
        return 0

    # Día CDT México (UTC-6) actual
    today_cdt = datetime.now(timezone.utc) - timedelta(hours=6)
    today_label = today_cdt.strftime("%a %d-%b").lower()

    try:
        picks = am.analyze_day(
            date_local=today_cdt,
            bankroll_mxn=bk["current_mxn"],
            stake_multiplier=bk["stake_multiplier"],
            odds_provider=_odds_provider_stub,
        )
    except Exception as e:
        err = f"🏆 MUNDIAL IA | ERROR\nFallo en analyze_day:\n{type(e).__name__}: {str(e)[:200]}"
        print(err)
        traceback.print_exc()
        whatsapp.send(err)
        return 1

    # TODO real-bet flow: si no DRY_RUN, llamar altenar_api.place_bet por pick
    # Por ahora se queda en notificación + history.
    _save_picks_history(picks)
    msg = _format_whatsapp(picks, today_label, bk)
    print("\n" + msg)
    whatsapp.send(msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
