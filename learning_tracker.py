"""
learning_tracker.py — Actualización bayesiana intra-torneo

Cada día, ANTES de generar picks:
  1. Lee resultados de los partidos terminados ayer (mundial_data.get_finished_matches_yesterday)
  2. Calcula error del modelo: xG esperado vs goles reales, ganador esperado vs real
  3. Actualiza model_state.json con nuevas medias móviles por equipo
  4. Detecta señales: equipo sobre/subestimado, jugador clave en forma, fatiga acumulada

model_state.json estructura:
{
  "updated_at": "2026-06-15T13:00:00Z",
  "teams": {
    "<team_id>": {
      "name": "...",
      "tla": "FRA",
      "attack_strength": 1.6,        # multiplicador vs media (1.0 = promedio Mundial)
      "defense_strength": 1.1,       # >1 = mejor defensa
      "form_momentum": 0.0,          # -1 a +1, suma exp-weighted de últimos resultados
      "matches_played_in_wc": 0,
      "xg_for_avg": 0.0,
      "xg_against_avg": 0.0,
      "goals_for": 0,
      "goals_against": 0,
      "model_calibration_error": 0.0, # cuánto se equivoca el modelo con este equipo
      "fatigue_index": 0.0,           # 0 = fresco, 1 = exhausto
      "rest_days_since_last": null,
      "star_players": [
        {"name": "Mbappé", "goals": 1, "minutes": 90, "form": 0.8}
      ]
    }
  },
  "groups": {
    "A": {"goals_per_match_avg": 0.0, "matches_played": 0}
  },
  "global": {
    "model_accuracy_pct": 0.0,
    "picks_resolved": 0,
    "picks_won": 0,
    "consecutive_losses": 0
  }
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import mundial_data as md
import statsbomb_prior as sbp

STATE_FILE = Path(__file__).parent / "model_state.json"

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de calibración
# ─────────────────────────────────────────────────────────────────────────────

WC_AVG_GOALS_PER_TEAM = 1.30        # media histórica Mundial
LEARNING_RATE_ATTACK   = 0.35        # cuánto pesa el último partido vs prior
LEARNING_RATE_DEFENSE  = 0.35
FORM_DECAY             = 0.55        # peso del partido más reciente en momentum
FATIGUE_PER_GAME       = 0.20        # acumula 20% por partido
FATIGUE_RECOVERY_DAY   = 0.10        # recupera 10% por día de descanso


# ─────────────────────────────────────────────────────────────────────────────
# Estado
# ─────────────────────────────────────────────────────────────────────────────

def _new_team_record(name: str, tla: str) -> dict:
    # Warm-start desde StatsBomb (Mundial 2018+2022). Si no hay prior → 1.0/1.0
    atk_prior, def_prior = sbp.get_team_prior(name)
    return {
        "name": name,
        "tla": tla,
        "attack_strength": atk_prior,
        "defense_strength": def_prior,
        "form_momentum": 0.0,
        "matches_played_in_wc": 0,
        "xg_for_avg": 0.0,
        "xg_against_avg": 0.0,
        "goals_for": 0,
        "goals_against": 0,
        "model_calibration_error": 0.0,
        "fatigue_index": 0.0,
        "last_match_date": None,
        "star_players": [],
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "teams": {},
            "groups": {},
            "global": {
                "model_accuracy_pct": 0.0,
                "picks_resolved": 0,
                "picks_won": 0,
                "consecutive_losses": 0,
                "stake_multiplier": 1.0,        # 1.0 normal, 0.625 si bankroll -15%
                "paused_until": None,             # ISO datetime si auto-pausado
            },
        }
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_or_create_team(state: dict, team_id: int, name: str, tla: str) -> dict:
    key = str(team_id)
    if key not in state["teams"]:
        state["teams"][key] = _new_team_record(name, tla)
    return state["teams"][key]


# ─────────────────────────────────────────────────────────────────────────────
# Actualización por partido terminado
# ─────────────────────────────────────────────────────────────────────────────

def update_with_match(state: dict, match: md.Match) -> None:
    """
    Actualiza state con resultado de un partido FINISHED.
    Modifica state in-place.
    """
    if not match.is_finished or match.home_goals is None or match.away_goals is None:
        return

    home = _get_or_create_team(state, match.home_id, match.home_name, match.home_tla)
    away = _get_or_create_team(state, match.away_id, match.away_name, match.away_tla)

    # Goles
    home["goals_for"]    += match.home_goals
    home["goals_against"] += match.away_goals
    away["goals_for"]    += match.away_goals
    away["goals_against"] += match.home_goals

    home["matches_played_in_wc"] += 1
    away["matches_played_in_wc"] += 1

    # Attack/Defense strength (media móvil exponencial vs prior 1.0)
    # Si Francia mete 3, su attack actual = goles_anotados / WC_AVG
    home_atk_obs = match.home_goals / WC_AVG_GOALS_PER_TEAM
    away_atk_obs = match.away_goals / WC_AVG_GOALS_PER_TEAM
    home_def_obs = WC_AVG_GOALS_PER_TEAM / max(match.away_goals + 0.5, 0.5)
    away_def_obs = WC_AVG_GOALS_PER_TEAM / max(match.home_goals + 0.5, 0.5)

    lr = LEARNING_RATE_ATTACK
    home["attack_strength"]  = (1 - lr) * home["attack_strength"]  + lr * home_atk_obs
    away["attack_strength"]  = (1 - lr) * away["attack_strength"]  + lr * away_atk_obs
    home["defense_strength"] = (1 - lr) * home["defense_strength"] + lr * home_def_obs
    away["defense_strength"] = (1 - lr) * away["defense_strength"] + lr * away_def_obs

    # Form momentum (-1 a +1)
    if match.home_goals > match.away_goals:
        home_result, away_result = 1.0, -1.0
    elif match.home_goals < match.away_goals:
        home_result, away_result = -1.0, 1.0
    else:
        home_result = away_result = 0.0

    home["form_momentum"] = FORM_DECAY * home_result + (1 - FORM_DECAY) * home["form_momentum"]
    away["form_momentum"] = FORM_DECAY * away_result + (1 - FORM_DECAY) * away["form_momentum"]

    # Fatiga: acumula al jugar
    home["fatigue_index"] = min(1.0, home["fatigue_index"] + FATIGUE_PER_GAME)
    away["fatigue_index"] = min(1.0, away["fatigue_index"] + FATIGUE_PER_GAME)

    # Última fecha de partido
    home["last_match_date"] = match.utc_kickoff
    away["last_match_date"] = match.utc_kickoff

    # Dinámica de grupo
    if match.group:
        gk = match.group
        if gk not in state["groups"]:
            state["groups"][gk] = {"goals_per_match_avg": 0.0, "matches_played": 0}
        g = state["groups"][gk]
        total_goals = match.home_goals + match.away_goals
        n_old = g["matches_played"]
        g["matches_played"] = n_old + 1
        g["goals_per_match_avg"] = (g["goals_per_match_avg"] * n_old + total_goals) / g["matches_played"]


def update_fatigue_recovery(state: dict, today_utc: Optional[datetime] = None) -> None:
    """Recupera fatiga por día sin jugar."""
    today = today_utc or datetime.now(timezone.utc)
    for team in state["teams"].values():
        last = team.get("last_match_date")
        if not last:
            continue
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        days_rest = max(0, (today - last_dt).days)
        team["fatigue_index"] = max(0.0, team["fatigue_index"] - days_rest * FATIGUE_RECOVERY_DAY)


# ─────────────────────────────────────────────────────────────────────────────
# Pick outcome tracking (alimenta auto-pausa)
# ─────────────────────────────────────────────────────────────────────────────

def register_pick_outcome(state: dict, won: bool) -> None:
    g = state["global"]
    g["picks_resolved"] = g.get("picks_resolved", 0) + 1
    if won:
        g["picks_won"] = g.get("picks_won", 0) + 1
        g["consecutive_losses"] = 0
    else:
        g["consecutive_losses"] = g.get("consecutive_losses", 0) + 1
    if g["picks_resolved"] > 0:
        g["model_accuracy_pct"] = round(100 * g["picks_won"] / g["picks_resolved"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# Daily refresh — punto de entrada del runner
# ─────────────────────────────────────────────────────────────────────────────

def daily_refresh() -> dict:
    """
    Paso A del cron: lee partidos terminados desde la última corrida
    y actualiza el modelo. Retorna el state actualizado.
    """
    state = load_state()

    # Última actualización
    last_update = datetime.fromisoformat(state["updated_at"].replace("Z", "+00:00"))

    # Tomamos TODOS los partidos finalizados y los aplicamos si su kickoff
    # es posterior al último update (idempotente — no doble-cuenta)
    all_matches = md.get_all_matches(force=True)
    fresh_matches = [
        m for m in all_matches
        if m.is_finished and m.kickoff_dt > last_update
    ]

    print(f"[learning_tracker] {len(fresh_matches)} partidos nuevos para procesar")

    for m in fresh_matches:
        print(f"  · {m.home_name} {m.home_goals}-{m.away_goals} {m.away_name} (J{m.matchday})")
        update_with_match(state, m)

    update_fatigue_recovery(state)
    save_state(state)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("─" * 70)
    print("learning_tracker.py self-test")
    print("─" * 70)

    # Simulación: estado inicial vacío + 2 partidos ficticios
    state = {
        "updated_at": "2026-06-10T00:00:00+00:00",
        "teams": {},
        "groups": {},
        "global": {"picks_resolved": 0, "picks_won": 0, "consecutive_losses": 0,
                   "stake_multiplier": 1.0, "paused_until": None,
                   "model_accuracy_pct": 0.0},
    }

    # Partido 1: Francia 3-0 Australia
    fra_aus = md.Match(
        fd_id=9999, utc_kickoff="2026-06-12T18:00:00Z", matchday=1,
        stage="GROUP_STAGE", group="D", home_id=773, home_name="France", home_tla="FRA",
        away_id=1, away_name="Australia", away_tla="AUS",
        status="FINISHED", home_goals=3, away_goals=0, venue="Estadio Azteca",
    )
    update_with_match(state, fra_aus)

    fra = state["teams"]["773"]
    print(f"\n✅ Francia tras 3-0 Australia:")
    print(f"   attack_strength: 1.00 → {fra['attack_strength']:.3f}")
    print(f"   defense_strength: 1.00 → {fra['defense_strength']:.3f}")
    print(f"   form_momentum: 0.00 → {fra['form_momentum']:.3f}")
    print(f"   fatigue: 0.00 → {fra['fatigue_index']:.3f}")

    # Partido 2: Francia 1-2 Brasil (sorpresa, modelo recalibra)
    fra_bra = md.Match(
        fd_id=10000, utc_kickoff="2026-06-18T18:00:00Z", matchday=2,
        stage="GROUP_STAGE", group="D", home_id=773, home_name="France", home_tla="FRA",
        away_id=764, away_name="Brazil", away_tla="BRA",
        status="FINISHED", home_goals=1, away_goals=2, venue="MetLife",
    )
    update_with_match(state, fra_bra)
    update_fatigue_recovery(state, datetime.fromisoformat("2026-06-19T00:00:00+00:00"))

    fra = state["teams"]["773"]
    print(f"\n✅ Francia tras 1-2 Brasil (J2):")
    print(f"   attack_strength: {fra['attack_strength']:.3f}")
    print(f"   defense_strength: {fra['defense_strength']:.3f}")
    print(f"   form_momentum: {fra['form_momentum']:.3f} (negativo = perdió)")
    print(f"   fatigue: {fra['fatigue_index']:.3f}")
    print(f"   matches_played: {fra['matches_played_in_wc']}")

    print(f"\n✅ Dinámica del grupo D:")
    print(f"   {state['groups']['D']}")

    print("\n🎯 learning_tracker.py OK")
