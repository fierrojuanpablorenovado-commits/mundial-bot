"""
statsbomb_prior.py — Prior pre-torneo desde StatsBomb Open Data

Baja Mundial 2018 + 2022 (gratis, GitHub raw). Para cada selección:
  - Partidos jugados
  - Goles anotados / partido
  - Goles concedidos / partido
  - attack_strength = goals_for_pg / WC_AVG (1.30)
  - defense_strength = WC_AVG / goals_against_pg

El más reciente (2022) pesa 70%, 2018 pesa 30%. Selecciones que no jugaron
ningún Mundial reciente quedan con default 1.0/1.0.

Output: statsbomb_priors.json en el repo (commiteable, no en gitignore).

Uso: python statsbomb_prior.py  → genera el archivo
     learning_tracker lo carga al inicializar model_state si no existe.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import requests

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
WC_2022 = (43, 106)  # (competition_id, season_id)
WC_2018 = (43, 3)

OUTPUT = Path(__file__).parent / "statsbomb_priors.json"
WC_AVG_GOALS_PER_TEAM = 1.30


def fetch_matches(comp_id: int, season_id: int) -> list[dict]:
    url = f"{BASE}/matches/{comp_id}/{season_id}.json"
    print(f"  GET {url}")
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def aggregate_team_stats(matches: list[dict]) -> dict[str, dict]:
    """team_name → {games, gf, ga}"""
    stats = defaultdict(lambda: {"games": 0, "gf": 0, "ga": 0})
    for m in matches:
        h = m["home_team"]["home_team_name"]
        a = m["away_team"]["away_team_name"]
        hs = m.get("home_score", 0) or 0
        as_ = m.get("away_score", 0) or 0
        stats[h]["games"] += 1
        stats[h]["gf"] += hs
        stats[h]["ga"] += as_
        stats[a]["games"] += 1
        stats[a]["gf"] += as_
        stats[a]["ga"] += hs
    return dict(stats)


def to_strength(stats: dict) -> dict:
    """Calcula attack/defense strength por equipo."""
    result = {}
    for team, s in stats.items():
        if s["games"] == 0:
            continue
        gf_pg = s["gf"] / s["games"]
        ga_pg = s["ga"] / s["games"]
        result[team] = {
            "games": s["games"],
            "gf_per_game": round(gf_pg, 3),
            "ga_per_game": round(ga_pg, 3),
            "attack_strength": round(gf_pg / WC_AVG_GOALS_PER_TEAM, 3),
            "defense_strength": round(WC_AVG_GOALS_PER_TEAM / max(ga_pg, 0.5), 3),
        }
    return result


def merge_priors(s2022: dict, s2018: dict, w_2022: float = 0.7) -> dict:
    """Combina dos Mundiales ponderando 2022 más."""
    all_teams = set(s2022) | set(s2018)
    merged = {}
    for team in all_teams:
        a22 = s2022.get(team, {}).get("attack_strength", 1.0)
        d22 = s2022.get(team, {}).get("defense_strength", 1.0)
        a18 = s2018.get(team, {}).get("attack_strength", 1.0)
        d18 = s2018.get(team, {}).get("defense_strength", 1.0)

        has_22 = team in s2022
        has_18 = team in s2018

        if has_22 and has_18:
            atq = w_2022 * a22 + (1 - w_2022) * a18
            def_ = w_2022 * d22 + (1 - w_2022) * d18
            games = s2022[team]["games"] + s2018[team]["games"]
        elif has_22:
            atq, def_ = a22, d22
            games = s2022[team]["games"]
        else:
            atq, def_ = a18, d18
            games = s2018[team]["games"]

        merged[team] = {
            "attack_strength": round(atq, 3),
            "defense_strength": round(def_, 3),
            "wc_games": games,
            "in_2022": has_22,
            "in_2018": has_18,
        }
    return merged


def generate() -> dict:
    print("[statsbomb_prior] Bajando Mundial 2022…")
    m22 = fetch_matches(*WC_2022)
    print(f"  → {len(m22)} partidos")
    s22 = to_strength(aggregate_team_stats(m22))

    print("[statsbomb_prior] Bajando Mundial 2018…")
    m18 = fetch_matches(*WC_2018)
    print(f"  → {len(m18)} partidos")
    s18 = to_strength(aggregate_team_stats(m18))

    merged = merge_priors(s22, s18)

    OUTPUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[statsbomb_prior] Generado: {OUTPUT}")
    print(f"  Selecciones con prior: {len(merged)}")
    return merged


def load_priors() -> dict:
    """Carga priors. Si no existe el archivo, retorna {} (model usa 1.0/1.0)."""
    if not OUTPUT.exists():
        return {}
    return json.loads(OUTPUT.read_text(encoding="utf-8"))


def get_team_prior(team_name: str) -> tuple[float, float]:
    """Returns (attack_strength, defense_strength) — default (1.0, 1.0)."""
    priors = load_priors()
    # Match exacto
    if team_name in priors:
        p = priors[team_name]
        return p["attack_strength"], p["defense_strength"]
    # Match por substring (StatsBomb usa "United States" vs football-data usa "USA")
    name_lower = team_name.lower()
    for k, p in priors.items():
        if name_lower in k.lower() or k.lower() in name_lower:
            return p["attack_strength"], p["defense_strength"]
    return 1.0, 1.0


if __name__ == "__main__":
    merged = generate()
    print("\n─── Top 10 atacantes históricos ───")
    top = sorted(merged.items(), key=lambda x: -x[1]["attack_strength"])[:10]
    for team, p in top:
        marker = "22+18" if p["in_2022"] and p["in_2018"] else "22" if p["in_2022"] else "18"
        print(f"  {team:30s}  atq {p['attack_strength']:.2f}  def {p['defense_strength']:.2f}  ({p['wc_games']} pts {marker})")

    print("\n─── Bottom 10 (más débiles) ───")
    bottom = sorted(merged.items(), key=lambda x: x[1]["attack_strength"])[:10]
    for team, p in bottom:
        marker = "22+18" if p["in_2022"] and p["in_2018"] else "22" if p["in_2022"] else "18"
        print(f"  {team:30s}  atq {p['attack_strength']:.2f}  def {p['defense_strength']:.2f}  ({p['wc_games']} pts {marker})")
