# -*- coding: utf-8 -*-
"""
value_analyzer.py — Analizador de valor con Poisson sobre datos reales de Altenar 24.

Pipeline completo:
  1. Resuelve equipos por NOMBRE via API Altenar (altenar_stats corregido)
  2. Baja forma real: goles, corners, BTTS, goles 1T — con ponderación por recencia
  3. Poisson bivariada → probabilidad real de TODOS los mercados
  4. Convierte a cuota justa + cuota mínima (edge 5%) + cuota valor (edge 15%)
  5. Opcional: compara contra cuotas de la casa y calcula EV por pata

Uso:
    python value_analyzer.py "Canadá" "Marruecos"
    python value_analyzer.py "Paraguay" "Francia" --ko          # ajuste knockout
    python value_analyzer.py "Canadá" "Marruecos" --odds "corners_o75=-128,btts_si=+120"

Reglas de la metodología Valor Multiplicado:
  - Pata candidata: P real >= 35% Y edge >= 5% vs cuota de la casa
  - Parlay final: EV combinado >= +15%
"""

import argparse
import math
import sys

from altenar_stats import _search_team_id, _get_form, _get_h2h

KO_ADJ = 0.85          # eliminatorias 2026: 2.5 goles/90min vs 2.8 en grupos
RECENCY_DECAY = 0.88   # peso exponencial: partido mas reciente pesa mas
FH_SHARE = 0.42        # % de goles que caen en 1a mitad (calibrado Mundial 2026)


# ── utilidades Poisson ───────────────────────────────────────────────────────

def pois(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def pois_cdf(lam: float, n: int) -> float:
    """P(X <= n)"""
    return sum(pois(lam, k) for k in range(n + 1))


def american_to_decimal(am: str) -> float:
    v = int(str(am).replace("+", ""))
    if str(am).strip().startswith("-"):
        return 1 + 100 / abs(v)
    return 1 + v / 100


def decimal_to_american(dec: float) -> str:
    if dec >= 2.0:
        return f"+{round((dec - 1) * 100)}"
    return f"-{round(100 / (dec - 1))}"


# ── forma ponderada por recencia ─────────────────────────────────────────────

def weighted_form(team_name: str, limit: int = 8) -> dict:
    """Forma con pesos exponenciales: el partido mas reciente pesa mas."""
    tid = _search_team_id(team_name)
    if not tid:
        return {"available": False, "name": team_name}

    raw = _get_form(tid, limit=limit)
    if not raw.get("available"):
        return {"available": False, "name": team_name}

    import requests
    from altenar_stats import BASE, HEADERS, TIMEOUT, _safe_int
    r = requests.get(f"{BASE}/widgets/get-form/{tid}",
                     params={"limit": limit, "extraStats": 1},
                     headers=HEADERS, timeout=TIMEOUT)
    data = r.json()
    name = data.get("name", team_name)
    past = [m for m in (data.get("form") or []) if m.get("type") == "past"][:limit]
    # past viene del mas viejo al mas nuevo o al reves — ordenar por fecha desc
    past.sort(key=lambda m: m.get("start_date") or "", reverse=True)

    gf_w = gc_w = w_sum = 0.0
    corners_w = corners_wsum = 0.0
    fh_w = fh_wsum = 0.0
    btts_hits = total_hits = 0
    matches = []

    for i, m in enumerate(past):
        w = RECENCY_DECAY ** i
        parts = (m.get("score") or "0:0").split(":")
        hs, as_ = _safe_int(parts[0]), _safe_int(parts[1]) if len(parts) > 1 else None
        if hs is None or as_ is None:
            continue
        is_home = m.get("home_team") == name
        gf, gc = (hs, as_) if is_home else (as_, hs)

        gf_w += gf * w
        gc_w += gc * w
        w_sum += w
        total_hits += 1
        if gf > 0 and gc > 0:
            btts_hits += 1

        ch = _safe_int(m.get("corners_home")) or 0
        ca = _safe_int(m.get("corners_away")) or 0
        if ch + ca > 0:
            corners_w += (ch + ca) * w
            corners_wsum += w

        p1 = str(m.get("score_period_1") or "").split(":")
        h1 = _safe_int(p1[0]) if p1 else None
        a1 = _safe_int(p1[1]) if len(p1) > 1 else None
        if h1 is not None and a1 is not None:
            fh_w += (h1 + a1) * w
            fh_wsum += w

        matches.append({
            "date": (m.get("start_date") or "")[:10],
            "line": f"{m.get('home_team')} {m.get('score')} {m.get('away_team')}",
            "gf": gf, "gc": gc, "corners": (ch + ca) or None,
        })

    if not w_sum:
        return {"available": False, "name": name}

    return {
        "available": True,
        "name": name,
        "team_id": tid,
        "n": total_hits,
        "gf": gf_w / w_sum,
        "gc": gc_w / w_sum,
        "btts_pct": btts_hits / total_hits * 100,
        "corners_avg": (corners_w / corners_wsum) if corners_wsum else None,
        "fh_avg": (fh_w / fh_wsum) if fh_wsum else None,
        "matches": matches,
        "last_match_date": matches[0]["date"] if matches else "?",
    }


# ── motor de mercados ────────────────────────────────────────────────────────

def analyze_match(home: str, away: str, ko: bool = True) -> dict:
    fh = weighted_form(home)
    fa = weighted_form(away)
    if not fh.get("available") or not fa.get("available"):
        return {"available": False,
                "error": f"Sin datos Altenar: {home}={fh.get('available')} {away}={fa.get('available')}"}

    adj = KO_ADJ if ko else 1.0
    lam_h = (fh["gf"] + fa["gc"]) / 2 * adj
    lam_a = (fa["gf"] + fh["gc"]) / 2 * adj
    lam_t = lam_h + lam_a

    # matriz de marcadores
    MAX_G = 8
    matrix = [[pois(lam_h, i) * pois(lam_a, j) for j in range(MAX_G)] for i in range(MAX_G)]

    p_home = sum(matrix[i][j] for i in range(MAX_G) for j in range(MAX_G) if i > j)
    p_draw = sum(matrix[i][i] for i in range(MAX_G))
    p_away = 1 - p_home - p_draw

    p_home_scores = 1 - pois(lam_h, 0)
    p_away_scores = 1 - pois(lam_a, 0)

    markets = {
        "1X2": {
            f"{home} gana": p_home,
            "Empate": p_draw,
            f"{away} gana": p_away,
        },
        "Doble oportunidad": {
            f"{home} o empate": p_home + p_draw,
            f"{away} o empate": p_away + p_draw,
            f"{home} o {away}": p_home + p_away,
        },
        "Goles O/U": {},
        "BTTS": {
            "Ambos anotan SI": p_home_scores * p_away_scores,
            "Ambos anotan NO": 1 - p_home_scores * p_away_scores,
        },
        "Equipo anota": {
            f"{home} marca SI": p_home_scores,
            f"{away} marca SI": p_away_scores,
        },
        "1a mitad": {
            "Gol en 1T (O0.5 1T)": 1 - pois(lam_t * FH_SHARE, 0),
            "Under 1.5 1T": pois_cdf(lam_t * FH_SHARE, 1),
        },
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        n = int(line)
        markets["Goles O/U"][f"Over {line}"] = 1 - pois_cdf(lam_t, n)
        markets["Goles O/U"][f"Under {line}"] = pois_cdf(lam_t, n)

    # corners
    ch, ca = fh.get("corners_avg"), fa.get("corners_avg")
    if ch and ca:
        lam_c = (ch + ca) / 2
        markets["Corners O/U"] = {}
        for line in (6.5, 7.5, 8.5, 9.5, 10.5, 11.5):
            n = int(line)
            markets["Corners O/U"][f"Over {line} corners"] = 1 - pois_cdf(lam_c, n)
            markets["Corners O/U"][f"Under {line} corners"] = pois_cdf(lam_c, n)
        corners_expected = lam_c
    else:
        corners_expected = None

    # marcadores exactos top
    scores = sorted(((matrix[i][j], i, j) for i in range(6) for j in range(6)), reverse=True)
    exact = {f"{i}-{j}": p for p, i, j in scores[:8]}

    h2h = _get_h2h(fh["team_id"], fa["team_id"])

    return {
        "available": True,
        "home_form": fh, "away_form": fa,
        "lam_h": lam_h, "lam_a": lam_a, "lam_total": lam_t,
        "corners_expected": corners_expected,
        "markets": markets,
        "exact_scores": exact,
        "h2h": h2h,
        "ko": ko,
    }


# ── reporte ──────────────────────────────────────────────────────────────────

def fair(p: float) -> float:
    return 1 / p if p > 0 else float("inf")


def print_report(home: str, away: str, res: dict, house_odds: dict | None = None):
    if not res.get("available"):
        print(f"[X] {res.get('error')}")
        print("REGLA: sin datos Altenar -> pick DESCARTADO. No se apuesta a ciegas.")
        return

    fh, fa = res["home_form"], res["away_form"]
    W = 74
    print("=" * W)
    print(f"  {home.upper()} vs {away.upper()}   |   Poisson sobre Altenar 24 (por nombre)")
    print("=" * W)
    print(f"  {fh['name']}: GF={fh['gf']:.2f} GC={fh['gc']:.2f} BTTS={fh['btts_pct']:.0f}% "
          f"corners={fh['corners_avg'] and round(fh['corners_avg'],1)} | ultimo: {fh['last_match_date']}")
    print(f"  {fa['name']}: GF={fa['gf']:.2f} GC={fa['gc']:.2f} BTTS={fa['btts_pct']:.0f}% "
          f"corners={fa['corners_avg'] and round(fa['corners_avg'],1)} | ultimo: {fa['last_match_date']}")
    print(f"  Lambdas: {home[:12]}={res['lam_h']:.2f}  {away[:12]}={res['lam_a']:.2f}  "
          f"TOTAL={res['lam_total']:.2f}  (KO_ADJ={'ON' if res['ko'] else 'OFF'})")
    if res["corners_expected"]:
        print(f"  Corners esperados: {res['corners_expected']:.1f}")
    print("-" * W)
    print(f"  {'MERCADO':<34}{'P real':>8}{'Justa':>8}{'Min+5%':>9}{'Valor+15%':>11}")
    print("-" * W)

    for cat, opts in res["markets"].items():
        print(f"  [{cat}]")
        for label, p in opts.items():
            f_ = fair(p)
            flag = " <-- CANDIDATA" if p >= 0.65 else ""
            print(f"    {label:<32}{p:>7.1%}{f_:>8.2f}{f_*1.05:>9.2f}{f_*1.15:>11.2f}{flag}")

    print("-" * W)
    print("  [Marcadores exactos top]")
    line = "   ".join(f"{s} {p:.1%}" for s, p in list(res["exact_scores"].items())[:6])
    print(f"    {line}")

    if res["h2h"]:
        print("  [H2H]")
        for m in res["h2h"][:3]:
            print(f"    {m['date']}: {m['home']} {m['score']} {m['away']}")

    # comparar contra cuotas de la casa
    if house_odds:
        print("-" * W)
        print(f"  {'PATA (casa)':<26}{'Cuota':>8}{'Implica':>9}{'P real':>8}{'Edge':>7}{'EV':>8}  VEREDICTO")
        flat = {}
        for opts in res["markets"].values():
            flat.update(opts)
        for key, am in house_odds.items():
            dec = american_to_decimal(am)
            implied = 1 / dec
            match = next((lbl for lbl in flat if key.lower().replace("_", " ") in lbl.lower()), None)
            p = flat.get(match) if match else None
            if p is None:
                print(f"  {key:<26}{am:>8}{implied:>8.1%}      ?       ?        ?  SIN MATCH en modelo")
                continue
            edge = p - implied
            ev = p * dec - 1
            verdict = "APOSTAR" if (edge >= 0.05 and p >= 0.35 and ev >= 0.05) else "NO VALE"
            print(f"  {match[:26]:<26}{am:>8}{implied:>8.1%}{p:>8.1%}{edge:>+7.1%}{ev:>+8.1%}  {verdict}")

    print("=" * W)


def main():
    ap = argparse.ArgumentParser(description="Analizador de valor Poisson + Altenar")
    ap.add_argument("home")
    ap.add_argument("away")
    ap.add_argument("--liga", action="store_true", help="sin ajuste knockout (usar en fase de grupos/ligas)")
    ap.add_argument("--odds", default=None,
                    help='cuotas americanas de la casa: "over 7.5 corners=-128,ambos anotan si=+120"')
    args = ap.parse_args()

    house = None
    if args.odds:
        house = {}
        for pair in args.odds.split(","):
            k, v = pair.split("=")
            house[k.strip()] = v.strip()

    res = analyze_match(args.home, args.away, ko=not args.liga)
    print_report(args.home, args.away, res, house)


if __name__ == "__main__":
    main()
