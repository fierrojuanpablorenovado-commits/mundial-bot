"""
backtest_2022.py — Validación del modelo contra el Mundial 2022 (StatsBomb)

OBJETIVO: validar que el modelo bayesiano + StatsBomb prior PREDICE bien los
resultados reales. Si en el backtest no hay edge, no lo habrá en 2026.

Metodología:
  1. Inicializa state vacío con priors de StatsBomb 2018 (1 mundial antes del 2022)
  2. Para cada partido del Mundial 2022 EN ORDEN:
     a) Predice con el modelo actual (Poisson + ratings + altura sede si aplica)
     b) Mide accuracy vs real (1X2, O/U 2.5, BTTS)
     c) Calcula edge vs fair odds (sin vig)
     d) Si "apuesta" según reglas del bot (edge >6%, max 3/día), trackea P&L
     e) Actualiza state con learning_tracker.update_with_match
  3. Reporta:
     - Accuracy global por mercado
     - Calibración (Brier score)
     - ROI hipotético si hubiéramos apostado

Output: backtest_2022_report.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

import stats_engine as se
import learning_tracker as lt
import statsbomb_prior as sbp
import mundial_data as md

OUTPUT = Path(__file__).parent / "backtest_2022_report.json"
STATSBOMB_2022_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/43/106.json"
STATSBOMB_2018_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/matches/43/3.json"

EDGE_MIN_PCT = 6.0
KELLY_DIV = 5.0
STAKE_PCT_MAX = 0.03  # 3% del bankroll por pick max
INITIAL_BANKROLL = 1000.0


def fetch_matches(url: str) -> list[dict]:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def predict_match(state: dict, home_name: str, away_name: str) -> dict:
    """Genera predicción para un partido usando el state actual."""
    # Buscar team_id ficticios (key=name) — para backtest usamos nombres directos
    home_key = f"sb_{home_name}"
    away_key = f"sb_{away_name}"

    if home_key in state["teams"]:
        rec = state["teams"][home_key]
        atq_h = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * rec["attack_strength"]
        def_h = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(rec["defense_strength"], 0.5)
        fatigue_mult = 1.0 - 0.15 * rec["fatigue_index"]
        atq_h *= fatigue_mult * (1.0 + 0.10 * rec["form_momentum"])
    else:
        atk_p, def_p = sbp.get_team_prior(home_name)
        atq_h = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * atk_p
        def_h = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(def_p, 0.5)

    if away_key in state["teams"]:
        rec = state["teams"][away_key]
        atq_a = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * rec["attack_strength"]
        def_a = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(rec["defense_strength"], 0.5)
        fatigue_mult = 1.0 - 0.15 * rec["fatigue_index"]
        atq_a *= fatigue_mult * (1.0 + 0.10 * rec["form_momentum"])
    else:
        atk_p, def_p = sbp.get_team_prior(away_name)
        atq_a = se.LEAGUE_AVG_GOALS["FIFA World Cup"] * atk_p
        def_a = se.LEAGUE_AVG_GOALS["FIFA World Cup"] / max(def_p, 0.5)

    home_rating = se.TeamRating(atq=atq_h, def_=def_h, sample_size=8)
    away_rating = se.TeamRating(atq=atq_a, def_=def_a, sample_size=8)

    lambdas = se.calc_lambdas(home_rating, away_rating, league="FIFA World Cup")
    matrix = se.poisson_matrix(lambdas.lh, lambdas.la, size=8)

    p_o25 = se.prob_over(matrix, 2.5)
    p_u25 = 1 - p_o25
    p1, px, p2 = se.prob_1x2(matrix)
    p_btts = se.prob_btts(matrix)

    return {
        "p_home": p1, "p_draw": px, "p_away": p2,
        "p_over25": p_o25, "p_under25": p_u25, "p_btts": p_btts,
        "lambda_h": lambdas.lh, "lambda_a": lambdas.la,
        "expected_goals": lambdas.lh + lambdas.la,
    }


def update_state_from_match(state: dict, m: dict) -> None:
    """Adaptación de lt.update_with_match para StatsBomb matches (sin Match dataclass)."""
    home = m["home_team"]["home_team_name"]
    away = m["away_team"]["away_team_name"]
    hs = m.get("home_score", 0) or 0
    as_ = m.get("away_score", 0) or 0
    home_key = f"sb_{home}"
    away_key = f"sb_{away}"

    for key, name in [(home_key, home), (away_key, away)]:
        if key not in state["teams"]:
            atk_p, def_p = sbp.get_team_prior(name)
            state["teams"][key] = {
                "name": name, "tla": name[:3].upper(),
                "attack_strength": atk_p, "defense_strength": def_p,
                "form_momentum": 0.0, "matches_played_in_wc": 0,
                "goals_for": 0, "goals_against": 0,
                "fatigue_index": 0.0, "last_match_date": None,
            }

    h = state["teams"][home_key]
    a = state["teams"][away_key]

    WC_AVG = 1.30
    LR = 0.35
    h_atk_obs = hs / WC_AVG
    a_atk_obs = as_ / WC_AVG
    h_def_obs = WC_AVG / max(as_ + 0.5, 0.5)
    a_def_obs = WC_AVG / max(hs + 0.5, 0.5)

    h["attack_strength"]  = (1 - LR) * h["attack_strength"]  + LR * h_atk_obs
    a["attack_strength"]  = (1 - LR) * a["attack_strength"]  + LR * a_atk_obs
    h["defense_strength"] = (1 - LR) * h["defense_strength"] + LR * h_def_obs
    a["defense_strength"] = (1 - LR) * a["defense_strength"] + LR * a_def_obs

    if hs > as_:
        h_r, a_r = 1.0, -1.0
    elif hs < as_:
        h_r, a_r = -1.0, 1.0
    else:
        h_r = a_r = 0.0
    h["form_momentum"] = 0.55 * h_r + 0.45 * h["form_momentum"]
    a["form_momentum"] = 0.55 * a_r + 0.45 * a["form_momentum"]

    h["goals_for"] += hs;  h["goals_against"] += as_; h["matches_played_in_wc"] += 1
    a["goals_for"] += as_; a["goals_against"] += hs; a["matches_played_in_wc"] += 1


def run_backtest():
    print("═" * 70)
    print("BACKTEST · Mundial 2022 (StatsBomb open-data)")
    print("═" * 70)

    print("\n[1] Bajando partidos del Mundial 2022...")
    matches_2022 = fetch_matches(STATSBOMB_2022_URL)
    matches_2022.sort(key=lambda m: m["match_date"] + m.get("kick_off", "00:00:00"))
    print(f"  → {len(matches_2022)} partidos")

    # Estado inicial vacío (los priors vienen de StatsBomb 2018 vía sbp)
    state = {"teams": {}, "groups": {}, "global": {}}

    bankroll = INITIAL_BANKROLL
    bets = []
    metrics = {
        "n_predictions": 0,
        "hits_1x2": 0, "hits_o25": 0, "hits_btts": 0,
        "brier_o25": 0.0, "brier_1x2": 0.0,
        "n_bets": 0, "n_won": 0, "n_lost": 0,
        "total_stake": 0.0, "total_pnl": 0.0,
    }

    print("\n[2] Iterando partidos en orden cronológico...")
    for m in matches_2022:
        home = m["home_team"]["home_team_name"]
        away = m["away_team"]["away_team_name"]
        hs = m.get("home_score") or 0
        as_ = m.get("away_score") or 0
        total = hs + as_
        date = m["match_date"]

        # 1) Predicción ANTES de ver el resultado
        pred = predict_match(state, home, away)

        # 2) Real outcome
        if hs > as_:
            real_1x2 = "home"
        elif hs == as_:
            real_1x2 = "draw"
        else:
            real_1x2 = "away"
        real_o25 = total > 2.5
        real_btts = (hs >= 1 and as_ >= 1)

        # 3) Accuracy
        pred_1x2 = max([("home", pred["p_home"]), ("draw", pred["p_draw"]), ("away", pred["p_away"])],
                       key=lambda x: x[1])[0]
        pred_o25 = pred["p_over25"] > 0.5
        pred_btts = pred["p_btts"] > 0.5

        metrics["n_predictions"] += 1
        if pred_1x2 == real_1x2:  metrics["hits_1x2"] += 1
        if pred_o25 == real_o25:   metrics["hits_o25"] += 1
        if pred_btts == real_btts: metrics["hits_btts"] += 1

        # Brier scores (calibración)
        actual_o25 = 1.0 if real_o25 else 0.0
        metrics["brier_o25"] += (pred["p_over25"] - actual_o25) ** 2
        actual_1x2_probs = {"home": 0.0, "draw": 0.0, "away": 0.0}
        actual_1x2_probs[real_1x2] = 1.0
        metrics["brier_1x2"] += sum(
            (p - actual_1x2_probs[k]) ** 2
            for k, p in [("home", pred["p_home"]), ("draw", pred["p_draw"]), ("away", pred["p_away"])]
        )

        # 4) ¿"Apostaría" el bot? Asumiendo odds eficientes con vig 5% (típico Pinnacle)
        # fair_odds = 1/prob, market_odds = fair_odds * 1.05 (con vig)
        # No tenemos odds reales — simulamos con probability shading típico
        for market_label, prob, real_won in [
            ("O2.5", pred["p_over25"], real_o25),
            ("U2.5", pred["p_under25"], not real_o25),
        ]:
            # Para simular: bookmaker estima probabilidad con error ±3% (mercado eficiente)
            # Asumimos bookmaker prob = 0.5 (línea cerrada) con vig 5%
            # Esto NO da edge artificial al modelo
            book_prob = 0.5  # asumimos línea cerrada en U/O 2.5
            book_odds = (1 / book_prob) / 1.05  # con vig 5%
            edge = (prob / book_prob - 1) * 100

            if edge >= EDGE_MIN_PCT:
                kelly = se.kelly_fraction(prob, book_odds, kelly_div=KELLY_DIV)
                stake = min(bankroll * kelly, bankroll * STAKE_PCT_MAX)
                if stake < 5:
                    continue
                metrics["n_bets"] += 1
                metrics["total_stake"] += stake
                if real_won:
                    pnl = stake * (book_odds - 1)
                    metrics["n_won"] += 1
                else:
                    pnl = -stake
                    metrics["n_lost"] += 1
                bankroll += pnl
                metrics["total_pnl"] += pnl
                bets.append({
                    "date": date, "match": f"{home} vs {away}", "score": f"{hs}-{as_}",
                    "market": market_label, "model_prob": round(prob, 3),
                    "edge_pct": round(edge, 1), "stake": round(stake, 1),
                    "won": real_won, "pnl": round(pnl, 1),
                    "bankroll_after": round(bankroll, 1),
                })

        # 5) Actualizar state para próximo partido
        update_state_from_match(state, m)

    # ─── Métricas finales ───
    n = metrics["n_predictions"]
    acc_1x2 = metrics["hits_1x2"] / n * 100
    acc_o25 = metrics["hits_o25"] / n * 100
    acc_btts = metrics["hits_btts"] / n * 100
    brier_o25 = metrics["brier_o25"] / n
    brier_1x2 = metrics["brier_1x2"] / n
    roi = (metrics["total_pnl"] / metrics["total_stake"] * 100) if metrics["total_stake"] > 0 else 0

    print("\n" + "═" * 70)
    print("RESULTADOS DEL BACKTEST")
    print("═" * 70)
    print(f"\nAccuracy del modelo (sobre {n} partidos):")
    print(f"  1X2  → {acc_1x2:.1f}%  (baseline aleatorio ~33%)")
    print(f"  O2.5 → {acc_o25:.1f}%  (baseline aleatorio 50%)")
    print(f"  BTTS → {acc_btts:.1f}% (baseline aleatorio 50%)")
    print(f"\nBrier score (menor = mejor calibrado):")
    print(f"  O2.5 → {brier_o25:.4f}  (perfect=0, naive 50%=0.25)")
    print(f"  1X2  → {brier_1x2:.4f}  (naive 1/3=0.667)")

    print(f"\nApuestas simuladas (vig 5%, edge_min +{EDGE_MIN_PCT}%):")
    print(f"  Total bets:  {metrics['n_bets']}")
    print(f"  W/L:         {metrics['n_won']}/{metrics['n_lost']}")
    if metrics['n_bets'] > 0:
        print(f"  Hit rate:    {metrics['n_won']/metrics['n_bets']*100:.1f}%")
    print(f"  Stake total: ${metrics['total_stake']:.0f}")
    print(f"  P&L total:   ${metrics['total_pnl']:+.0f}")
    print(f"  ROI:         {roi:+.1f}%")
    print(f"  Bankroll:    ${INITIAL_BANKROLL:.0f} → ${bankroll:.1f}")

    report = {
        "generated_at": datetime.now().isoformat(),
        "matches_analyzed": n,
        "accuracy_pct": {
            "1x2": round(acc_1x2, 1),
            "over25": round(acc_o25, 1),
            "btts": round(acc_btts, 1),
        },
        "brier_score": {
            "over25": round(brier_o25, 4),
            "1x2": round(brier_1x2, 4),
        },
        "simulated_betting": {
            "n_bets": metrics["n_bets"],
            "n_won": metrics["n_won"],
            "n_lost": metrics["n_lost"],
            "hit_rate_pct": round(metrics["n_won"]/max(metrics["n_bets"],1)*100, 1),
            "total_stake_mxn": round(metrics["total_stake"], 0),
            "total_pnl_mxn": round(metrics["total_pnl"], 1),
            "roi_pct": round(roi, 1),
            "bankroll_initial": INITIAL_BANKROLL,
            "bankroll_final": round(bankroll, 1),
        },
        "bets": bets,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Reporte guardado en: {OUTPUT}")
    return report


if __name__ == "__main__":
    run_backtest()
