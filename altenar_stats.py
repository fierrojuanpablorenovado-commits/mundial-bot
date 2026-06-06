"""
altenar_stats.py — Estadísticas reales de Altenar Live Results
API pública: https://altenar.24liveresults.com
Sin autenticación. Datos reales de forma, H2H y promedios de goles.

Uso:
    from altenar_stats import get_match_stats
    stats = get_match_stats("México", "Sudáfrica")
    print(stats)
"""

import requests
import logging
from functools import lru_cache
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

# IDs conocidos extraídos via Chrome el 6-jun-2026 — evitan llamadas a /search
KNOWN_IDS: dict[str, int] = {
    # Equipos J1 Mundial 2026
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
    "Francia": 79440, "France": 79440,
    "Argentina": 79439,
    "Inglaterra": 79441, "England": 79441,
    "Croacia": 79447, "Croatia": 79447,
    "Países Bajos": 79442, "Netherlands": 79442,
    "Bélgica": 79448, "Belgium": 79448,
    "Arabia Saudita": 16328, "Saudi Arabia": 16328,
    "Uruguay": 74771,
    "Senegal": 16278,
    "Noruega": 78508, "Norway": 78508,
    "Argelia": 16273, "Algeria": 16273,
    "Japón": 79449, "Japan": 79449,
    "Corea del Sur": 16277, "South Korea": 16277,
    "Canadá": 16325, "Canada": 16325,
    "Bosnia y Herzegovina": 16330, "Bosnia": 16330,
    "Catar": 16329, "Qatar": 16329,
    "Suiza": 78505, "Switzerland": 78505,
    "Haití": 16271, "Haiti": 16271,
    "Escocia": 79454, "Scotland": 79454,
    "Australia": 16326,
    "Turquía": 79446, "Turkey": 79446,
    "Costa de Marfil": 16280, "Ivory Coast": 16280,
    "Ecuador": 74762,
    "Suecia": 78501, "Sweden": 78501,
    "Túnez": 16279, "Tunisia": 16279,
    "Egipto": 16274, "Egypt": 16274,
    "Irak": 16331, "Iraq": 16331,
    "Ghana": 16275,
    "Panamá": 74764, "Panama": 74764,
    "Colombia": 74758,
    "Uzbekistán": 16335, "Uzbekistan": 16335,
    "Nueva Zelanda": 73815,
}

# Cache de IDs por nombre de equipo
_TEAM_ID_CACHE: dict[str, Optional[int]] = {}

# Alias de nombres español → inglés para la búsqueda
NAME_ALIASES = {
    "Alemania": "Germany",
    "España": "Spain",
    "Portugal": "Portugal",
    "Austria": "Austria",
    "México": "Mexico",
    "Estados Unidos": "USA",
    "USA": "USA",
    "Brasil": "Brazil",
    "Irán": "Iran",
    "Sudáfrica": "South Africa",
    "Cabo Verde": "Cape Verde",
    "RD Congo": "DR Congo",
    "Jordania": "Jordan",
    "Paraguay": "Paraguay",
    "Marruecos": "Morocco",
    "Nueva Zelanda": "New Zealand",
    "Curazao": "Curacao",
    "Japón": "Japan",
    "Francia": "France",
    "Argentina": "Argentina",
    "Inglaterra": "England",
    "Croacia": "Croatia",
    "Países Bajos": "Netherlands",
    "Holanda": "Netherlands",
    "Bélgica": "Belgium",
    "Arabia Saudita": "Saudi Arabia",
    "Uruguay": "Uruguay",
    "Senegal": "Senegal",
    "Noruega": "Norway",
    "Argelia": "Algeria",
    "Ghana": "Ghana",
    "Panamá": "Panama",
    "Colombia": "Colombia",
    "Uzbekistán": "Uzbekistan",
    "Corea del Sur": "South Korea",
    "Chequia": "Czech Republic",
    "Canadá": "Canada",
    "Bosnia y Herzegovina": "Bosnia",
    "Catar": "Qatar",
    "Suiza": "Switzerland",
    "Haití": "Haiti",
    "Escocia": "Scotland",
    "Australia": "Australia",
    "Turquía": "Turkey",
    "Costa de Marfil": "Ivory Coast",
    "Ecuador": "Ecuador",
    "Suecia": "Sweden",
    "Túnez": "Tunisia",
    "Egipto": "Egypt",
    "Iraq": "Iraq",
    "Irak": "Iraq",
    "Eslovenia": "Slovenia",
    "Austria": "Austria",
}


def _search_team_id(name: str) -> Optional[int]:
    """Busca el ID de un equipo. Usa tabla local primero, luego API."""
    if name in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[name]

    # 1. Tabla local (rápida, sin red)
    if name in KNOWN_IDS:
        _TEAM_ID_CACHE[name] = KNOWN_IDS[name]
        return KNOWN_IDS[name]

    # 2. Traducir y reintentar con tabla local
    search_name = NAME_ALIASES.get(name, name)
    if search_name in KNOWN_IDS:
        _TEAM_ID_CACHE[name] = KNOWN_IDS[search_name]
        return KNOWN_IDS[search_name]

    # 3. Búsqueda via API como fallback
    try:
        r = requests.get(
            f"{BASE}/search",
            params={"lang": "en", "q": search_name, "type": "participant"},
            headers=HEADERS,
            timeout=TIMEOUT
        )
        r.raise_for_status()
        participants = r.json().get("participants", [])
        exact = next((p for p in participants if p.get("name") == search_name), None)
        if not exact:
            exact = next((p for p in participants if search_name.lower() in (p.get("name") or "").lower()), None)
        team_id = exact["id"] if exact else None
        _TEAM_ID_CACHE[name] = team_id
        return team_id
    except Exception as e:
        log.warning(f"altenar_stats: no se encontró ID para '{name}': {e}")
        _TEAM_ID_CACHE[name] = None
        return None


def _get_form(team_id: int, limit: int = 8) -> dict:
    """Obtiene forma reciente de un equipo."""
    try:
        r = requests.get(
            f"{BASE}/widgets/get-form/{team_id}",
            params={"limit": limit, "extraStats": 1},
            headers=HEADERS,
            timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        name = data.get("name", "?")
        past = [m for m in (data.get("form") or []) if m.get("type") == "past"][:limit]

        goals_list = []
        results_str = ""
        for m in past:
            score = m.get("score", "0:0") or "0:0"
            parts = score.split(":")
            hs = int(parts[0]) if parts[0].isdigit() else 0
            as_ = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            total = hs + as_
            goals_list.append(total)

            is_home = m.get("home_team") == name
            if is_home:
                results_str += "W" if hs > as_ else ("L" if hs < as_ else "D")
            else:
                results_str += "W" if as_ > hs else ("L" if as_ < hs else "D")

        avg_goals = round(sum(goals_list) / len(goals_list), 1) if goals_list else None
        u25_pct = round(len([g for g in goals_list if g <= 2]) / len(goals_list) * 100) if goals_list else None
        o25_pct = round(len([g for g in goals_list if g >= 3]) / len(goals_list) * 100) if goals_list else None

        return {
            "name": name,
            "team_id": team_id,
            "avg_goals": avg_goals,
            "u25_pct": u25_pct,
            "o25_pct": o25_pct,
            "form_str": results_str,
            "matches_analyzed": len(goals_list),
            "goals_list": goals_list,
        }

    except Exception as e:
        log.warning(f"altenar_stats: error forma team_id={team_id}: {e}")
        return {"name": "?", "team_id": team_id, "avg_goals": None, "form_str": "", "matches_analyzed": 0}


def _get_h2h(home_id: int, away_id: int, limit: int = 6) -> list[dict]:
    """H2H histórico entre dos equipos."""
    try:
        r = requests.get(
            f"{BASE}/participant-compare/{home_id}/{away_id}",
            params={"lang": "en"},
            headers=HEADERS,
            timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()

        # Extraer partidos H2H
        h2h_raw = (
            data.get("h2h") or
            data.get("first", {}).get("h2h") or
            []
        )
        if isinstance(h2h_raw, dict):
            h2h_raw = (
                h2h_raw.get("all") or
                h2h_raw.get("total", {}).get("all") or
                []
            )

        results = []
        for m in (h2h_raw or [])[:limit]:
            score = m.get("score", "?")
            parts = (score or "?").split(":")
            hs = int(parts[0]) if len(parts) > 0 and str(parts[0]).strip().isdigit() else None
            as_ = int(parts[1]) if len(parts) > 1 and str(parts[1]).strip().isdigit() else None
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
    Obtiene estadísticas completas para un partido.

    Returns:
        {
          "home": {name, avg_goals, u25_pct, o25_pct, form_str, ...},
          "away": {name, avg_goals, u25_pct, o25_pct, form_str, ...},
          "h2h": [{date, home, away, score, total_goals}, ...],
          "h2h_avg_goals": float,
          "combined_avg_goals": float,
          "under25_signal": "strong"|"moderate"|"weak",
          "over25_signal": "strong"|"moderate"|"weak",
          "available": bool
        }
    """
    home_id = _search_team_id(home_name)
    away_id = _search_team_id(away_name)

    if not home_id or not away_id:
        log.warning(f"altenar_stats: no se encontraron IDs para {home_name} ({home_id}) o {away_name} ({away_id})")
        return {"available": False, "home": {}, "away": {}, "h2h": []}

    home_form = _get_form(home_id)
    away_form = _get_form(away_id)
    h2h = _get_h2h(home_id, away_id)

    # Calcular promedio combinado
    h_avg = home_form.get("avg_goals")
    a_avg = away_form.get("avg_goals")
    combined_avg = round((h_avg + a_avg) / 2, 2) if h_avg and a_avg else None

    # H2H promedio de goles
    h2h_goals = [m["total_goals"] for m in h2h if m["total_goals"] is not None]
    h2h_avg = round(sum(h2h_goals) / len(h2h_goals), 1) if h2h_goals else None

    # Señal Under 2.5
    h_u25 = home_form.get("u25_pct") or 0
    a_u25 = away_form.get("u25_pct") or 0
    avg_u25 = (h_u25 + a_u25) / 2

    if avg_u25 >= 65:
        under_signal = "strong"
    elif avg_u25 >= 50:
        under_signal = "moderate"
    else:
        under_signal = "weak"

    # Señal Over 2.5
    h_o25 = home_form.get("o25_pct") or 0
    a_o25 = away_form.get("o25_pct") or 0
    avg_o25 = (h_o25 + a_o25) / 2

    if avg_o25 >= 65:
        over_signal = "strong"
    elif avg_o25 >= 50:
        over_signal = "moderate"
    else:
        over_signal = "weak"

    return {
        "available": True,
        "home": home_form,
        "away": away_form,
        "h2h": h2h,
        "h2h_avg_goals": h2h_avg,
        "combined_avg_goals": combined_avg,
        "under25_signal": under_signal,
        "over25_signal": over_signal,
    }


def format_stats_for_whatsapp(home_name: str, away_name: str, stats: dict) -> str:
    """Formatea las estadísticas para el mensaje de WhatsApp del bot."""
    if not stats.get("available"):
        return f"⚠️ Stats no disponibles para {home_name} vs {away_name}"

    h = stats["home"]
    a = stats["away"]
    h2h = stats["h2h"]

    lines = [
        f"📊 *{home_name} vs {away_name}*",
        f"",
        f"*{home_name}:* avg={h.get('avg_goals','?')} goles | U2.5={h.get('u25_pct','?')}% | forma={h.get('form_str','?')}",
        f"*{away_name}:* avg={a.get('avg_goals','?')} goles | U2.5={a.get('u25_pct','?')}% | forma={a.get('form_str','?')}",
        f"",
        f"*Combinado:* {stats.get('combined_avg_goals','?')} goles/partido",
        f"*Señal U2.5:* {stats['under25_signal'].upper()} | *Señal O2.5:* {stats['over25_signal'].upper()}",
    ]

    if h2h:
        lines.append("")
        lines.append("*H2H (últimos encuentros):*")
        for m in h2h[:3]:
            lines.append(f"  {m['date']}: {m['home']} {m['score']} {m['away']}")

    return "\n".join(lines)


def format_stats_table(picks: list[dict]) -> str:
    """
    Genera tabla de stats para múltiples picks.
    picks = [{"home": "México", "away": "Sudáfrica", "pick": "UNDER 2.5", "cuota": 1.70, "parlay": "B"}, ...]
    """
    lines = ["=" * 60, "ESTADÍSTICAS REALES — ALTENAR LIVE RESULTS", "=" * 60]

    for p in picks:
        stats = get_match_stats(p["home"], p["away"])
        h = stats.get("home", {})
        a = stats.get("away", {})

        signal_key = "under25_signal" if "UNDER" in p["pick"] else "over25_signal"
        signal = stats.get(signal_key, "?").upper()
        emoji = "🟢" if signal == "STRONG" else ("🟡" if signal == "MODERATE" else "🔴")

        lines.append(f"\n[PARLAY {p['parlay']}] {p['home']} vs {p['away']}")
        lines.append(f"  Pick: {p['pick']} @ {p['cuota']} | Señal: {emoji} {signal}")
        lines.append(f"  {p['home']}: avg={h.get('avg_goals','?')} | U2.5={h.get('u25_pct','?')}% | {h.get('form_str','?')}")
        lines.append(f"  {p['away']}: avg={a.get('avg_goals','?')} | U2.5={a.get('u25_pct','?')}% | {a.get('form_str','?')}")
        if stats.get("combined_avg_goals"):
            lines.append(f"  Combinado: {stats['combined_avg_goals']} goles/partido")

    lines.append("=" * 60)
    return "\n".join(lines)


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    picks_j1 = [
        {"home": "Alemania",        "away": "Curazao",        "pick": "OVER 4.5",  "cuota": 2.05, "parlay": "A"},
        {"home": "España",          "away": "Cabo Verde",     "pick": "OVER 3.5",  "cuota": 1.92, "parlay": "A"},
        {"home": "Portugal",        "away": "RD Congo",       "pick": "OVER 2.5",  "cuota": 1.68, "parlay": "A"},
        {"home": "Austria",         "away": "Jordania",       "pick": "OVER 2.5",  "cuota": 1.62, "parlay": "A"},
        {"home": "México",          "away": "Sudáfrica",      "pick": "UNDER 2.5", "cuota": 1.70, "parlay": "B"},
        {"home": "Estados Unidos",  "away": "Paraguay",       "pick": "UNDER 2.5", "cuota": 1.62, "parlay": "B"},
        {"home": "Brasil",          "away": "Marruecos",      "pick": "UNDER 2.5", "cuota": 1.80, "parlay": "B"},
        {"home": "Irán",            "away": "Nueva Zelanda",  "pick": "UNDER 2.5", "cuota": 1.62, "parlay": "B"},
    ]

    print(format_stats_table(picks_j1))
