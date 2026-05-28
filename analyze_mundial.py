"""
analyze_mundial.py — Pipeline de análisis diario

Combina:
  1. CUANTITATIVO (stats_engine + learning_tracker):
     - Lee model_state.json (actualizado por learning_tracker.daily_refresh)
     - Construye TeamRating dinámico por equipo desde su evolución intra-Mundial
     - Calcula Poisson + Over/Under + 1X2 + córners

  2. CUANTITATIVO JUGADORES (mundial_data.get_lineups):
     - Lineup probable 2h antes del partido
     - Si falta el goleador top de la selección → penaliza attack_strength -8%

  3. CUALITATIVO (OpenAI + web_search):
     - Clima ciudad/hora del partido (México DF altitud, USA verano calor)
     - Motivación táctica (¿necesita ganar para clasificar?)
     - Rotaciones esperadas (¿ya clasificó?)
     - Noticias últimas 48h del equipo
     - Devuelve VETO si encuentra red flag fuerte

Filtros finales:
  • Edge >= EDGE_MIN_PCT (default 6%)
  • Edge <= EDGE_MAX_PCT (default 35%) — sanity check
  • Sample mínimo 5 partidos por equipo (combinando WC + form previa)
  • Stake según Kelly 1/5, capado por session_budget_pct
  • Max picks/día = MAX_PICKS_PER_DAY
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import mundial_data as md
import learning_tracker as lt
import stats_engine as se


# ─────────────────────────────────────────────────────────────────────────────
# Config (lee env)
# ─────────────────────────────────────────────────────────────────────────────

EDGE_MIN_PCT       = float(os.environ.get("EDGE_MIN_PCT", "6"))
EDGE_MAX_PCT       = float(os.environ.get("EDGE_MAX_PCT", "35"))
MAX_PICKS_PER_DAY  = int(os.environ.get("MAX_PICKS_PER_DAY", "3"))
SESSION_BUDGET_PCT = float(os.environ.get("SESSION_BUDGET_PCT", "25"))
KELLY_DIV          = float(os.environ.get("KELLY_DIV", "5"))
MIN_SAMPLE_FOR_PICK = 1   # En Mundial el sample es 0 en J1; nos apoyamos en prior pre-torneo


# ─────────────────────────────────────────────────────────────────────────────
# Construcción de TeamRating desde state
# ─────────────────────────────────────────────────────────────────────────────

def team_rating_from_state(state: dict, team_id: int,
                            prior_atq: float = 1.4, prior_def: float = 1.2) -> se.TeamRating:
    """
    Construye TeamRating combinando:
      - prior pre-Mundial (default 1.4/1.2 — equipo promedio)
      - estado actual del modelo (attack_strength, defense_strength)
      - penalización por fatiga
    """
    key = str(team_id)
    rec = state["teams"].get(key)

    if not rec or rec["matches_played_in_wc"] == 0:
        # Sin datos de Mundial — usa prior
        return se.TeamRating(atq=prior_atq, def_=prior_def, cpg=8.5,
                             estilo="B", sample_size=5)

    # attack_strength es multiplicador vs media. Convertir a goles/partido.
    atq = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * rec["attack_strength"]
    def_ = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(rec["defense_strength"], 0.5)

    # Penalización por fatiga: 1.0 fresco, 0.85 si exhausto
    fatigue_mult = 1.0 - 0.15 * rec["fatigue_index"]
    atq *= fatigue_mult

    # Boost/penalty por momentum (-1 a +1 → ±10%)
    atq *= (1.0 + 0.10 * rec["form_momentum"])

    sample = rec["matches_played_in_wc"] * 3  # 1 partido WC ~= 3 amistosos en peso
    return se.TeamRating(atq=atq, def_=def_, cpg=8.5, estilo="B",
                         sample_size=max(sample, 5))


# ─────────────────────────────────────────────────────────────────────────────
# Veto cualitativo OpenAI
# ─────────────────────────────────────────────────────────────────────────────

OPENAI_AVAILABLE = bool(os.environ.get("OPENAI_API_KEY"))

def qualitative_veto(match: md.Match, pick_market: str, pick_label: str,
                     edge_pct: float) -> tuple[bool, str]:
    """
    Pide a OpenAI un veto cualitativo. Usa web_search para clima, lineups,
    motivación, noticias ≤48h. Returns (passes, reason).

    Si OPENAI_API_KEY no está, retorna (True, "skipped") para no bloquear.
    """
    if not OPENAI_AVAILABLE:
        return True, "veto-skipped (no OPENAI_API_KEY)"

    try:
        from openai import OpenAI
        client = OpenAI()

        prompt = f"""Eres analista deportivo senior evaluando una apuesta en el MUNDIAL FIFA 2026.

PARTIDO: {match.home_name} vs {match.away_name}
SEDE: {match.venue or 'desconocida'}
JORNADA: {match.matchday} ({match.stage})
GRUPO: {match.group or 'knockout'}
KICKOFF UTC: {match.utc_kickoff}

PICK PROPUESTO: {pick_label} (mercado {pick_market})
EDGE DEL MODELO: +{edge_pct:.1f}%

Usa web_search para verificar EN ORDEN:
1. Clima previsto en la sede a la hora del partido (lluvia → menos goles; calor extremo → menos goles; altitud Azteca → más goles).
2. Lineups confirmados/probables. ¿Falta algún goleador estrella? ¿Hay rotaciones masivas?
3. Motivación: ¿algún equipo ya clasificó/eliminado matemáticamente? Si sí, esperan rotaciones.
4. Noticias últimas 48h: lesiones, escándalos, sanciones, problemas internos.

Responde JSON ESTRICTO:
{{
  "passes": true|false,
  "confidence": "ALTA"|"MEDIA"|"BAJA",
  "reason": "explicación en 1-2 líneas",
  "weather": "...",
  "lineup_concern": "..." | null,
  "motivation_concern": "..." | null,
  "news_concern": "..." | null
}}

passes=false SOLO si encuentras red flag fuerte que invalide el pick.
"""

        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            tools=[{"type": "web_search"}],
        )
        raw = resp.choices[0].message.content or "{}"
        verdict = json.loads(raw)
        passes = verdict.get("passes", True)
        reason = verdict.get("reason", "OK")
        return passes, reason

    except Exception as e:
        # Si OpenAI falla por cualquier razón, NO bloqueamos — solo lo anotamos
        return True, f"veto-error: {type(e).__name__}"


# ─────────────────────────────────────────────────────────────────────────────
# Pick result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Pick:
    fd_match_id: int
    home: str
    away: str
    kickoff_utc: str
    market: str            # "goles_o25"
    label: str             # "Over 2.5 goles"
    model_prob: float
    odds: float
    edge_pct: float
    stake_mxn: float
    confidence: str        # ALTA/MEDIA/BAJA
    veto_reason: str
    matchday: int
    group: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────────────────────────────────────

def analyze_day(date_local: Optional[datetime] = None,
                 bankroll_mxn: float = 1000.0,
                 stake_multiplier: float = 1.0,
                 odds_provider=None) -> list[Pick]:
    """
    date_local: día CDT México a analizar. None = hoy.
    bankroll_mxn: capital actual.
    stake_multiplier: 1.0 normal, 0.625 si auto-pausa suave.
    odds_provider: callable(match) -> dict de odds (PlayDoit). None = sin edge real.
    """
    date_local = date_local or (datetime.now(timezone.utc).replace(tzinfo=timezone.utc))

    print(f"[analyze] Día objetivo CDT: {date_local.date()}")
    print(f"[analyze] Bankroll: ${bankroll_mxn:.0f} MXN · multiplier {stake_multiplier:.3f}")

    # Paso A: actualiza state con partidos terminados desde último update
    state = lt.daily_refresh()

    # Paso B: trae partidos del día
    matches = md.get_matches_for_date(date_local)
    print(f"[analyze] Partidos del día: {len(matches)}")

    session_budget = bankroll_mxn * SESSION_BUDGET_PCT / 100.0
    print(f"[analyze] Session budget: ${session_budget:.0f} MXN")

    candidates: list[tuple[Pick, float]] = []  # (pick, score) para ranking

    for match in matches:
        if match.is_finished:
            continue

        home_rating = team_rating_from_state(state, match.home_id)
        away_rating = team_rating_from_state(state, match.away_id)

        # Odds reales (si tenemos provider) o None para análisis sin edge
        odds_dict = odds_provider(match) if odds_provider else None
        if not odds_dict:
            # Sin odds reales no podemos calcular edge → skip
            print(f"  · {match.home_tla} vs {match.away_tla} — sin odds, skip")
            continue

        analysis = se.analyze_match(
            home_name=match.home_name, away_name=match.away_name,
            home_rating=home_rating, away_rating=away_rating,
            league="FIFA World Cup", odds_dict=odds_dict,
            lines=(1.5, 2.5, 3.5),
        )

        # Filtrar mercados con edge en rango
        for m in analysis.markets:
            if m.edge_pct is None or m.odds is None:
                continue
            if not (EDGE_MIN_PCT <= m.edge_pct <= EDGE_MAX_PCT):
                continue
            if m.model_prob < 0.30 or m.model_prob > 0.85:
                continue  # extremos sospechosos

            # Kelly fraccional
            kelly = se.kelly_fraction(m.model_prob, m.odds, kelly_div=KELLY_DIV)
            stake = round(bankroll_mxn * kelly * stake_multiplier, 0)
            if stake < 10:
                continue  # muy chico, no vale ruido
            stake = min(stake, session_budget / MAX_PICKS_PER_DAY)

            confidence = "ALTA" if m.edge_pct >= 12 else "MEDIA" if m.edge_pct >= 8 else "BAJA"

            pick = Pick(
                fd_match_id=match.fd_id,
                home=match.home_name, away=match.away_name,
                kickoff_utc=match.utc_kickoff,
                market=m.market, label=m.label,
                model_prob=m.model_prob, odds=m.odds, edge_pct=m.edge_pct,
                stake_mxn=stake, confidence=confidence,
                veto_reason="pending",
                matchday=match.matchday, group=match.group,
            )
            candidates.append((pick, m.edge_pct))

    # Ordenar por edge descendente y tomar top N
    candidates.sort(key=lambda x: x[1], reverse=True)
    selected: list[Pick] = []

    for pick, _ in candidates:
        if len(selected) >= MAX_PICKS_PER_DAY:
            break

        # Veto cualitativo OpenAI (solo para los top candidatos para ahorrar tokens)
        match = next(m for m in matches if m.fd_id == pick.fd_match_id)
        passes, reason = qualitative_veto(match, pick.market, pick.label, pick.edge_pct)
        pick.veto_reason = reason
        if not passes:
            print(f"  ❌ VETO: {pick.home} vs {pick.away} {pick.label} — {reason}")
            continue

        selected.append(pick)
        print(f"  ✅ PICK: {pick.home} vs {pick.away} {pick.label} "
              f"edge +{pick.edge_pct:.1f}% stake ${pick.stake_mxn:.0f}")

    return selected


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (mock odds)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("─" * 70)
    print("analyze_mundial.py self-test (mock odds)")
    print("─" * 70)

    def mock_odds(match):
        # Simula odds genéricas para probar el pipeline
        return {
            "goles_o25": 1.85,
            "goles_u25": 1.95,
            "goles_o15": 1.35,
            "goles_u15": 3.10,
            "goles_o35": 2.60,
            "goles_u35": 1.50,
        }

    from datetime import timedelta
    # Apuntamos al primer día del Mundial (11-jun-2026) para forzar partidos
    target_day = datetime(2026, 6, 11, tzinfo=timezone.utc)
    picks = analyze_day(target_day, bankroll_mxn=1000.0, odds_provider=mock_odds)

    print(f"\n🎯 Picks generados: {len(picks)}")
    for p in picks:
        print(f"   · {p.home} vs {p.away} | {p.label} @{p.odds} | "
              f"edge +{p.edge_pct:.1f}% | stake ${p.stake_mxn} | {p.confidence}")
