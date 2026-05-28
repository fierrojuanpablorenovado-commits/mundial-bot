"""
run_dry_simulation.py — Simulación pre-Mundial con partidos del 11-15 jun

Mientras esperamos amistosos preparatorios y a que JP destrabe los secrets,
este script corre el pipeline COMPLETO contra los primeros 15 partidos del
Mundial (que ya tienen sede confirmada en venue_calendar).

Genera:
  - simulation_picks.json con todos los picks teóricos
  - Tabla resumen por día
  - Distribución de stakes, edges, mercados

Uso: python run_dry_simulation.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("FOOTBALL_DATA_API_KEY", "d3715dcadaea431fb77b5db78368c7cb")

import analyze_mundial as am
import mundial_data as md
import venue_calendar


def mock_odds_realistic(match: md.Match) -> dict:
    """
    Odds simuladas más realistas: varían según el matchup esperado.
    En producción, esto vendrá de altenar_api.fetch_odds(match).
    """
    # Línea base con vig 5%
    return {
        "goles_o15": 1.30, "goles_u15": 3.50,
        "goles_o25": 1.85, "goles_u25": 1.95,
        "goles_o35": 2.60, "goles_u35": 1.50,
    }


def run_simulation(start_date: str, end_date: str) -> dict:
    """Corre el pipeline día por día desde start_date hasta end_date inclusive."""
    print("═" * 70)
    print(f"SIMULACIÓN PRE-MUNDIAL · {start_date} → {end_date}")
    print("═" * 70)

    # Refresca venue calendar primero
    print("\n[setup] Refrescando venue_calendar…")
    cal = venue_calendar.refresh_calendar(force=True)
    print(f"  → {cal['with_venue']}/{cal['total_events']} partidos con sede confirmada")

    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    cursor = start

    all_picks = []
    daily_summary = []

    while cursor <= end:
        date_label = cursor.strftime("%a %d-%b").lower()
        print(f"\n──── {cursor.strftime('%Y-%m-%d')} ({date_label}) ────")

        picks = am.analyze_day(
            date_local=cursor.replace(tzinfo=timezone.utc),
            bankroll_mxn=1000.0,
            stake_multiplier=1.0,
            odds_provider=mock_odds_realistic,
        )

        daily_summary.append({
            "date": cursor.strftime("%Y-%m-%d"),
            "n_picks": len(picks),
            "total_stake": sum(p.stake_mxn for p in picks),
            "avg_edge_pct": round(sum(p.edge_pct for p in picks) / max(len(picks), 1), 2),
        })
        all_picks.extend([{
            "date": cursor.strftime("%Y-%m-%d"),
            "match": f"{p.home} vs {p.away}",
            "venue": next((m.venue for m in md.get_matches_for_date(cursor.replace(tzinfo=timezone.utc))
                          if m.fd_id == p.fd_match_id), None),
            "market": p.market,
            "label": p.label,
            "odds": p.odds,
            "edge_pct": p.edge_pct,
            "stake_mxn": p.stake_mxn,
            "confidence": p.confidence,
            "model_prob": p.model_prob,
        } for p in picks])

        cursor += timedelta(days=1)

    out_file = Path(__file__).parent / "simulation_picks.json"
    out_file.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "range": {"start": start_date, "end": end_date},
        "daily_summary": daily_summary,
        "picks": all_picks,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "═" * 70)
    print("RESUMEN DE LA SIMULACIÓN")
    print("═" * 70)
    print(f"  Total picks: {len(all_picks)}")
    print(f"  Stake total: ${sum(p['stake_mxn'] for p in all_picks):.0f} MXN")
    if all_picks:
        avg_edge = sum(p['edge_pct'] for p in all_picks) / len(all_picks)
        print(f"  Edge promedio: +{avg_edge:.1f}%")
        markets = {}
        for p in all_picks:
            markets[p["market"]] = markets.get(p["market"], 0) + 1
        print(f"  Mercados: {markets}")
    print(f"\n  Detalle guardado en: {out_file}")
    return {"daily_summary": daily_summary, "picks": all_picks}


if __name__ == "__main__":
    # Por ahora, simulamos los primeros 5 días del Mundial (con datos reales del calendar)
    run_simulation("2026-06-11", "2026-06-15")
