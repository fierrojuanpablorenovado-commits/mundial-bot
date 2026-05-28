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
import venues
import weather as wx


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
                            team_name: str = "",
                            context_mult: float = 1.0) -> se.TeamRating:
    """
    Construye TeamRating combinando:
      - state actual del learning_tracker (que ya tiene warm-start StatsBomb)
      - context_mult: multiplicador externo (venue + weather + lineups + lesiones)
      - penalización fatiga + boost momentum
    """
    key = str(team_id)
    rec = state["teams"].get(key)

    if not rec:
        # team_name nuevo — disparamos _new_team_record indirectamente via getter
        # Pero como state es read-only aquí, usamos un fallback con prior StatsBomb
        import statsbomb_prior as sbp
        atk_prior, def_prior = sbp.get_team_prior(team_name)
        atq = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * atk_prior * context_mult
        def_ = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(def_prior, 0.5)
        return se.TeamRating(atq=atq, def_=def_, cpg=8.5, estilo="B", sample_size=5)

    atq = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * rec["attack_strength"]
    def_ = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(rec["defense_strength"], 0.5)

    fatigue_mult = 1.0 - 0.15 * rec["fatigue_index"]
    atq *= fatigue_mult
    atq *= (1.0 + 0.10 * rec["form_momentum"])
    atq *= context_mult  # venue altitude × weather × lineup quality

    sample = max(rec["matches_played_in_wc"] * 3, 5)
    return se.TeamRating(atq=atq, def_=def_, cpg=8.5, estilo="B", sample_size=sample)


# ─────────────────────────────────────────────────────────────────────────────
# Contexto del partido: venue + weather + lineups + lesiones
# ─────────────────────────────────────────────────────────────────────────────

def build_match_context(match: md.Match) -> dict:
    """
    Devuelve dict con:
      - venue_info (con altitude_goal_boost, fatigue_factor)
      - weather (con goal_factor, fatigue_factor adicional)
      - home_context_mult, away_context_mult (combinados para alimentar el rating)
      - lineup_notes / injury_notes (texto para WhatsApp y veto)
    """
    venue_info = venues.lookup(match.venue) or {}
    weather = wx.get_weather_for_match(match.venue, match.utc_kickoff)

    altitude_boost = venue_info.get("altitude_goal_boost", 1.0)
    venue_fatigue  = venue_info.get("fatigue_factor", 1.0)

    weather_goal_factor = weather.get("goal_factor", 1.0) if weather else 1.0
    weather_fatigue     = weather.get("fatigue_factor", 1.0) if weather else 1.0

    # Multiplicador compuesto al ataque de AMBOS equipos
    # altitude_boost va a ambos (afecta partido completo)
    # weather goal_factor va a ambos (lluvia/frío afecta ambos)
    combined_goal_mult = altitude_boost * weather_goal_factor

    # Lineups + lesiones (intenta obtener api-sports fixture id)
    lineup_notes = []
    injury_notes = []
    home_lineup_penalty = 1.0
    away_lineup_penalty = 1.0

    try:
        as_fixture_id = md.get_api_sports_fixture_id(
            match.home_tla, match.away_tla, match.utc_kickoff[:10])
        if as_fixture_id:
            lineups = md.get_lineups_for_fixture(as_fixture_id)
            if lineups:
                for team_lineup in lineups:
                    lineup_notes.append(
                        f"{team_lineup.get('team', {}).get('name', '?')}: "
                        f"{team_lineup.get('formation', '?')}"
                    )
            injuries = md.get_injuries_for_fixture(as_fixture_id)
            if injuries:
                # Cuenta lesionados por equipo
                home_injured = 0
                away_injured = 0
                for inj in injuries:
                    team_name = inj.get("team", {}).get("name", "")
                    player_name = inj.get("player", {}).get("name", "?")
                    if match.home_tla.upper() in team_name.upper() or \
                       match.home_name.upper()[:5] in team_name.upper():
                        home_injured += 1
                        injury_notes.append(f"🏥 {match.home_tla}: {player_name}")
                    else:
                        away_injured += 1
                        injury_notes.append(f"🏥 {match.away_tla}: {player_name}")
                # Cada lesionado clave penaliza 3% (max 15%)
                home_lineup_penalty = max(0.85, 1.0 - 0.03 * home_injured)
                away_lineup_penalty = max(0.85, 1.0 - 0.03 * away_injured)
    except Exception as e:
        print(f"  [context] no se pudo obtener lineups/lesiones: {e}")

    home_mult = combined_goal_mult * home_lineup_penalty
    away_mult = combined_goal_mult * away_lineup_penalty

    return {
        "venue": venue_info,
        "weather": weather,
        "home_context_mult": round(home_mult, 3),
        "away_context_mult": round(away_mult, 3),
        "lineup_notes": lineup_notes,
        "injury_notes": injury_notes,
        "altitude_m": venue_info.get("altitude_m", 0),
        "weather_condition": weather.get("condition") if weather else "unknown",
    }


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

CONTEXTO: api-sports.io free no cubre 2026, así que tú eres la ÚNICA fuente
de lineups y lesiones. Usa web_search agresivamente para verificar EN ORDEN:

1. **LINEUPS confirmados** (búsqueda obligatoria: "{match.home_name} {match.away_name} lineup confirmed {match.utc_kickoff[:10]}"). ¿Falta el goleador top? ¿Rotaciones?
2. **LESIONES y SUSPENSIONES** últimas 48h de ambos equipos.
3. **Clima** previsto en la sede a la hora del partido (lluvia → menos goles; calor extremo → menos goles; altitud Azteca → más goles).
4. **Motivación**: ¿algún equipo ya clasificó/eliminado? Si sí, esperan rotaciones.
5. **Noticias** ≤48h: escándalos, problemas internos, decisiones tácticas anunciadas.

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

        ctx = build_match_context(match)
        print(f"  · {match.home_tla} vs {match.away_tla} @ {ctx['venue'].get('stadium', '?')} "
              f"alt={ctx['altitude_m']}m clima={ctx['weather_condition']}")

        home_rating = team_rating_from_state(state, match.home_id, match.home_name,
                                              ctx["home_context_mult"])
        away_rating = team_rating_from_state(state, match.away_id, match.away_name,
                                              ctx["away_context_mult"])

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

    # Ordenar por edge descendente y tomar top N (max 1 pick por partido)
    candidates.sort(key=lambda x: x[1], reverse=True)
    selected: list[Pick] = []
    selected_match_ids: set[int] = set()

    for pick, _ in candidates:
        if len(selected) >= MAX_PICKS_PER_DAY:
            break
        if pick.fd_match_id in selected_match_ids:
            continue  # diversificación: 1 pick por partido

        # Veto cualitativo OpenAI (solo para los top candidatos para ahorrar tokens)
        match = next(m for m in matches if m.fd_id == pick.fd_match_id)
        passes, reason = qualitative_veto(match, pick.market, pick.label, pick.edge_pct)
        pick.veto_reason = reason
        if not passes:
            print(f"  ❌ VETO: {pick.home} vs {pick.away} {pick.label} — {reason}")
            continue

        selected.append(pick)
        selected_match_ids.add(pick.fd_match_id)
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
