"""
altenar_stats.py — Estadísticas reales de Altenar Live Results
API pública: https://altenar.24liveresults.com
Sin autenticación. Forma, H2H, goles por mitad, corners, BTTS.

Lógica de resolución de IDs:
  1. Búsqueda por nombre via API (siempre primero — evita IDs desactualizados)
  2. KNOWN_IDS como fallback si la red falla

Uso:
    from altenar_stats import get_match_stats
    stats = get_match_stats("Colombia", "Ghana")
"""

import requests
import logging
from typing import Optional

log = logging.getLogger(__name__)

BASE = "https://altenar.24liveresults.com/api"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://altenar.24liveresults.com/",
    "Origin": "https://altenar.24liveresults.com",
}
TIMEOUT = 8

# IDs de respaldo — solo se usan si la búsqueda por nombre falla por red.
# Validados contra resultados reales del Mundial 2026 (03-jul-2026).
KNOWN_IDS: dict[str, int] = {
    "México": 79443, "Mexico": 79443,
    "Sudáfrica": 80183, "South Africa": 80183,
    "Alemania": 79846, "Germany": 79846,
    "Curazao": 40251, "Curacao": 40251,
    "España": 78498, "Spain": 78498,
    "Cabo Verde": 48963, "Cape Verde": 48963,
    "Portugal": 74651,
    "RD Congo": 41279, "DR Congo": 41279,
    "Austria": 78497,
    "Jordania": 16332, "Jordan": 16332,
    "Estados Unidos": 24573, "USA": 24573, "United States": 24573,
    "Paraguay": 74759,
    "Brasil": 16622, "Brazil": 16622,
    "Marruecos": 16272, "Morocco": 16272,
    "Irán": 16327, "Iran": 16327,
    "Nueva Zelanda": 73815, "New Zealand": 73815,
    "Francia": 74649, "France": 74649,           # corregido: era 79440 (equivocado)
    "Argentina": 16568,                           # corregido: era 79439 (otro deporte)
    "Inglaterra": 79848, "England": 79848,        # corregido: era 79441 (Honduras)
    "Croacia": 79447, "Croatia": 79447,
    "Países Bajos": 79442, "Netherlands": 79442,
    "Bélgica": 79448, "Belgium": 79448,
    "Arabia Saudita": 16328, "Saudi Arabia": 16328,
    "Uruguay": 74771,
    "Senegal": 16278,
    "Noruega": 79847, "Norway": 79847,            # corregido: era 78508 (juvenil Brasil)
    "Argelia": 16273, "Algeria": 16273,
    "Japón": 79449, "Japan": 79449,
    "Corea del Sur": 16277, "South Korea": 16277,
    "Canadá": 16539, "Canada": 16539,             # corregido: era 16325
    "Bosnia y Herzegovina": 16367, "Bosnia": 16367,  # corregido: era 16330 (Australia)
    "Catar": 16329, "Qatar": 16329,
    "Suiza": 78505, "Switzerland": 78505,
    "Haití": 16271, "Haiti": 16271,
    "Escocia": 79454, "Scotland": 79454,
    "Australia": 16330,                           # corregido: era 16326 (corrupto)
    "Turquía": 79446, "Turkey": 79446,
    "Costa de Marfil": 16280, "Ivory Coast": 16280,
    "Ecuador": 74762,
    "Suecia": 78501, "Sweden": 78501,
    "Túnez": 16279, "Tunisia": 16279,
    "Egipto": 16286, "Egypt": 16286,              # corregido: era 16274 (Senegal)
    "Irak": 16331, "Iraq": 16331,
    "Ghana": 80142,                               # corregido: era 16275 (Costa de Marfil)
    "Panamá": 74764, "Panama": 74764,
    "Colombia": 16570,                            # corregido: era 74758 (Chile)
    "Uzbekistán": 16335, "Uzbekistan": 16335,
}

NAME_ALIASES = {
    "Alemania": "Germany", "España": "Spain", "Portugal": "Portugal",
    "Austria": "Austria", "México": "Mexico", "Estados Unidos": "USA",
    "Brasil": "Brazil", "Irán": "Iran", "Sudáfrica": "South Africa",
    "Cabo Verde": "Cape Verde", "RD Congo": "DR Congo", "Jordania": "Jordan",
    "Paraguay": "Paraguay", "Marruecos": "Morocco", "Nueva Zelanda": "New Zealand",
    "Curazao": "Curacao", "Japón": "Japan", "Francia": "France",
    "Argentina": "Argentina", "Inglaterra": "England", "Croacia": "Croatia",
    "Países Bajos": "Netherlands", "Holanda": "Netherlands", "Bélgica": "Belgium",
    "Arabia Saudita": "Saudi Arabia", "Uruguay": "Uruguay", "Senegal": "Senegal",
    "Noruega": "Norway", "Argelia": "Algeria", "Ghana": "Ghana",
    "Panamá": "Panama", "Colombia": "Colombia", "Uzbekistán": "Uzbekistan",
    "Corea del Sur": "South Korea", "Chequia": "Czech Republic",
    "Canadá": "Canada", "Bosnia y Herzegovina": "Bosnia", "Catar": "Qatar",
    "Suiza": "Switzerland", "Haití": "Haiti", "Escocia": "Scotland",
    "Australia": "Australia", "Turquía": "Turkey", "Costa de Marfil": "Ivory Coast",
    "Ecuador": "Ecuador", "Suecia": "Sweden", "Túnez": "Tunisia",
    "Egipto": "Egypt", "Iraq": "Iraq", "Irak": "Iraq", "Eslovenia": "Slovenia",
}

_TEAM_ID_CACHE: dict[str, Optional[int]] = {}


def _search_team_id(name: str) -> Optional[int]:
    """Resuelve nombre → ID. Busca por nombre via API primero; KNOWN_IDS como fallback."""
    if name in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[name]

    search_name = NAME_ALIASES.get(name, name)

    # 1. Búsqueda via API — siempre primero para garantizar el ID correcto
    try:
        r = requests.get(
            f"{BASE}/search",
            params={"lang": "en", "q": search_name, "type": "participant"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        participants = r.json().get("participants", [])
        # Coincidencia exacta primero, luego parcial
        exact = next((p for p in participants if p.get("name") == search_name), None)
        if not exact:
            exact = next(
                (p for p in participants if search_name.lower() in (p.get("name") or "").lower()),
                None,
            )
        if exact:
            tid = exact["id"]
            _TEAM_ID_CACHE[name] = tid
            log.debug(f"altenar_stats: '{name}' → ID {tid} via API (name='{exact.get('name')}')")
            return tid
        log.warning(f"altenar_stats: API no encontró '{search_name}'")
    except Exception as e:
        log.warning(f"altenar_stats: búsqueda API falló para '{name}': {e}")

    # 2. KNOWN_IDS como fallback si la red falló
    for key in (name, search_name):
        if key in KNOWN_IDS:
            tid = KNOWN_IDS[key]
            log.warning(f"altenar_stats: usando KNOWN_IDS fallback para '{name}' → {tid}")
            _TEAM_ID_CACHE[name] = tid
            return tid

    _TEAM_ID_CACHE[name] = None
    return None


def _safe_int(v) -> Optional[int]:
    """Parsea int tolerando strings vacíos o None."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _get_form(team_id: int, limit: int = 8) -> dict:
    """Forma reciente con goles, corners, BTTS y goles por mitad."""
    try:
        r = requests.get(
            f"{BASE}/widgets/get-form/{team_id}",
            params={"limit": limit, "extraStats": 1},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        if not r.text.strip():
            return {"name": "?", "team_id": team_id, "available": False, "matches_analyzed": 0}
        data = r.json()
        if isinstance(data, list):
            return {"name": "?", "team_id": team_id, "available": False, "matches_analyzed": 0}

        name = data.get("name", "?")
        past = [m for m in (data.get("form") or []) if m.get("type") == "past"][:limit]

        goals_total, goals_scored, goals_conceded = [], [], []
        corners_total = []
        first_half_goals = []
        btts_count = 0
        results_str = ""

        for m in past:
            score = m.get("score", "0:0") or "0:0"
            parts = score.split(":")
            hs = _safe_int(parts[0]) if parts else None
            as_ = _safe_int(parts[1]) if len(parts) > 1 else None
            if hs is None or as_ is None:
                continue

            is_home = m.get("home_team") == name
            gf, gc = (hs, as_) if is_home else (as_, hs)
            goals_total.append(hs + as_)
            goals_scored.append(gf)
            goals_conceded.append(gc)

            # Corners (vienen como strings o int)
            ch = _safe_int(m.get("corners_home")) or 0
            ca = _safe_int(m.get("corners_away")) or 0
            if ch + ca > 0:
                corners_total.append(ch + ca)

            # Goles primera mitad
            p1 = m.get("score_period_1", "")
            if p1:
                p1_parts = str(p1).split(":")
                h1 = _safe_int(p1_parts[0])
                a1 = _safe_int(p1_parts[1]) if len(p1_parts) > 1 else None
                if h1 is not None and a1 is not None:
                    first_half_goals.append(h1 + a1)

            # BTTS
            if gf > 0 and gc > 0:
                btts_count += 1

            results_str += "W" if gf > gc else ("L" if gf < gc else "D")

        n = len(goals_total)
        if not n:
            return {"name": name, "team_id": team_id, "available": False, "matches_analyzed": 0}

        avg_goals = round(sum(goals_total) / n, 2)
        gf_avg = round(sum(goals_scored) / n, 2)
        gc_avg = round(sum(goals_conceded) / n, 2)
        u25_pct = round(len([g for g in goals_total if g <= 2]) / n * 100)
        u35_pct = round(len([g for g in goals_total if g <= 3]) / n * 100)
        o25_pct = 100 - u25_pct
        btts_pct = round(btts_count / n * 100)
        corners_avg = round(sum(corners_total) / len(corners_total), 1) if corners_total else None
        fh_avg = round(sum(first_half_goals) / len(first_half_goals), 2) if first_half_goals else None

        return {
            "available": True,
            "name": name,
            "team_id": team_id,
            "matches_analyzed": n,
            "avg_goals": avg_goals,
            "gf_avg": gf_avg,
            "gc_avg": gc_avg,
            "u25_pct": u25_pct,
            "u35_pct": u35_pct,
            "o25_pct": o25_pct,
            "btts_pct": btts_pct,
            "corners_avg": corners_avg,
            "first_half_avg": fh_avg,
            "form_str": results_str,
            "goals_list": goals_total,
        }

    except Exception as e:
        log.warning(f"altenar_stats: error forma team_id={team_id}: {e}")
        return {"name": "?", "team_id": team_id, "available": False, "matches_analyzed": 0}


def _get_h2h(home_id: int, away_id: int, limit: int = 6) -> list[dict]:
    """H2H histórico entre dos equipos."""
    try:
        r = requests.get(
            f"{BASE}/participant-compare/{home_id}/{away_id}",
            params={"lang": "en"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        h2h_raw = data.get("h2h") or data.get("first", {}).get("h2h") or []
        if isinstance(h2h_raw, dict):
            h2h_raw = h2h_raw.get("all") or h2h_raw.get("total", {}).get("all") or []

        # También buscar en data.data
        if not h2h_raw and isinstance(data.get("data"), dict):
            h2h_raw = data["data"].get("all") or []

        results = []
        for m in (h2h_raw or [])[:limit]:
            score = m.get("score", "?")
            parts = (score or "?").split(":")
            hs = _safe_int(parts[0]) if len(parts) > 0 else None
            as_ = _safe_int(parts[1]) if len(parts) > 1 else None
            results.append({
                "date": (m.get("date") or m.get("start_date") or "")[:10],
                "home": m.get("home_participant", {}).get("name") or m.get("home_name", "?"),
                "away": m.get("away_participant", {}).get("name") or m.get("away_name", "?"),
                "score": score,
                "total_goals": (hs + as_) if hs is not None and as_ is not None else None,
            })
        return results

    except Exception as e:
        log.warning(f"altenar_stats: error H2H {home_id} vs {away_id}: {e}")
        return []


def get_match_stats(home_name: str, away_name: str) -> dict:
    """
    Estadísticas completas para un partido.

    Returns dict con:
      home/away: {name, avg_goals, gf_avg, gc_avg, u25_pct, u35_pct, o25_pct,
                  btts_pct, corners_avg, first_half_avg, form_str, ...}
      h2h: [{date, home, away, score, total_goals}, ...]
      h2h_avg_goals, combined_avg_goals
      under25_signal, over25_signal, btts_signal  — "strong"|"moderate"|"weak"
      available: bool
    """
    home_id = _search_team_id(home_name)
    away_id = _search_team_id(away_name)

    if not home_id or not away_id:
        log.warning(f"altenar_stats: IDs no encontrados — {home_name}({home_id}) vs {away_name}({away_id})")
        return {"available": False, "home": {}, "away": {}, "h2h": []}

    home_form = _get_form(home_id)
    away_form = _get_form(away_id)
    h2h = _get_h2h(home_id, away_id)

    h_avg = home_form.get("avg_goals")
    a_avg = away_form.get("avg_goals")
    combined_avg = round((h_avg + a_avg) / 2, 2) if h_avg and a_avg else None

    h2h_goals = [m["total_goals"] for m in h2h if m["total_goals"] is not None]
    h2h_avg = round(sum(h2h_goals) / len(h2h_goals), 1) if h2h_goals else None

    def signal(val: Optional[float], high=65, mid=50) -> str:
        if val is None:
            return "unknown"
        if val >= high:
            return "strong"
        if val >= mid:
            return "moderate"
        return "weak"

    h_u25 = home_form.get("u25_pct") or 0
    a_u25 = away_form.get("u25_pct") or 0
    h_o25 = home_form.get("o25_pct") or 0
    a_o25 = away_form.get("o25_pct") or 0
    h_btts = home_form.get("btts_pct") or 0
    a_btts = away_form.get("btts_pct") or 0

    return {
        "available": True,
        "home": home_form,
        "away": away_form,
        "h2h": h2h,
        "h2h_avg_goals": h2h_avg,
        "combined_avg_goals": combined_avg,
        "under25_signal": signal((h_u25 + a_u25) / 2),
        "over25_signal": signal((h_o25 + a_o25) / 2),
        "btts_signal": signal((h_btts + a_btts) / 2),
    }


def format_stats_for_whatsapp(home_name: str, away_name: str, stats: dict) -> str:
    """Formatea estadísticas para el mensaje de WhatsApp."""
    if not stats.get("available"):
        return f"Stats no disponibles para {home_name} vs {away_name}"

    h = stats["home"]
    a = stats["away"]
    h2h = stats["h2h"]

    lines = [
        f"*{home_name} vs {away_name}*",
        "",
        f"*{home_name}:* avg={h.get('avg_goals','?')} | GF={h.get('gf_avg','?')} GC={h.get('gc_avg','?')} | U2.5={h.get('u25_pct','?')}% | BTTS={h.get('btts_pct','?')}% | corners={h.get('corners_avg','?')} | forma={h.get('form_str','?')}",
        f"*{away_name}:* avg={a.get('avg_goals','?')} | GF={a.get('gf_avg','?')} GC={a.get('gc_avg','?')} | U2.5={a.get('u25_pct','?')}% | BTTS={a.get('btts_pct','?')}% | corners={a.get('corners_avg','?')} | forma={a.get('form_str','?')}",
        "",
        f"*Combinado:* {stats.get('combined_avg_goals','?')} goles/partido",
        f"*Senales:* U2.5={stats['under25_signal'].upper()} | O2.5={stats['over25_signal'].upper()} | BTTS={stats['btts_signal'].upper()}",
    ]

    if h2h:
        lines.append("")
        lines.append("*H2H:*")
        for m in h2h[:3]:
            lines.append(f"  {m['date']}: {m['home']} {m['score']} {m['away']}")

    return "\n".join(lines)


def format_stats_table(picks: list[dict]) -> str:
    """
    Tabla de stats para múltiples picks.
    picks = [{"home": "México", "away": "Sudáfrica", "pick": "UNDER 2.5", "cuota": 1.70, "parlay": "B"}, ...]
    """
    lines = ["=" * 62, "ESTADISTICAS REALES — ALTENAR LIVE RESULTS", "=" * 62]

    for p in picks:
        stats = get_match_stats(p["home"], p["away"])
        h = stats.get("home", {})
        a = stats.get("away", {})

        pick_upper = p["pick"].upper()
        if "BTTS" in pick_upper:
            signal_key, signal_val = "btts_signal", stats.get("btts_signal", "?")
        elif "UNDER" in pick_upper:
            signal_key, signal_val = "under25_signal", stats.get("under25_signal", "?")
        else:
            signal_key, signal_val = "over25_signal", stats.get("over25_signal", "?")

        signal_val = (signal_val or "?").upper()
        emoji = "🟢" if signal_val == "STRONG" else ("🟡" if signal_val == "MODERATE" else "🔴")

        lines.append(f"\n[PARLAY {p['parlay']}] {p['home']} vs {p['away']}")
        lines.append(f"  Pick: {p['pick']} @ {p['cuota']} | Señal: {emoji} {signal_val}")
        lines.append(f"  {p['home']}: avg={h.get('avg_goals','?')} | U2.5={h.get('u25_pct','?')}% | BTTS={h.get('btts_pct','?')}% | corners={h.get('corners_avg','?')} | {h.get('form_str','?')}")
        lines.append(f"  {p['away']}: avg={a.get('avg_goals','?')} | U2.5={a.get('u25_pct','?')}% | BTTS={a.get('btts_pct','?')}% | corners={a.get('corners_avg','?')} | {a.get('form_str','?')}")
        if stats.get("combined_avg_goals"):
            lines.append(f"  Combinado: {stats['combined_avg_goals']} goles/partido | BTTS señal: {stats.get('btts_signal','?').upper()}")
        if stats.get("h2h"):
            lines.append(f"  H2H avg: {stats.get('h2h_avg_goals','?')} goles ({len(stats['h2h'])} partidos)")

    lines.append("=" * 62)
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("=== TEST: Colombia vs Ghana ===")
    stats = get_match_stats("Colombia", "Ghana")
    print(format_stats_for_whatsapp("Colombia", "Ghana", stats))
    print("\nHome corners_avg:", stats["home"].get("corners_avg"))
    print("Away corners_avg:", stats["away"].get("corners_avg"))
    print("Home first_half_avg:", stats["home"].get("first_half_avg"))
    print("BTTS signal:", stats.get("btts_signal"))

    print("\n=== TEST tabla ===")
    picks = [
        {"home": "Colombia", "away": "Ghana", "pick": "UNDER 2.5", "cuota": 1.67, "parlay": "A"},
        {"home": "Australia", "away": "Egypt", "pick": "UNDER 3.5", "cuota": 1.29, "parlay": "A"},
    ]
    print(format_stats_table(picks))
