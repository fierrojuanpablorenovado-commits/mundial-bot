"""
venue_calendar.py — Mapeo (fecha + equipos) → sede oficial Mundial 2026

football-data.org NO devuelve `venue` (siempre null) en /competitions/WC/matches.
Solución: thesportsdb.com tiene los fixtures con `strVenue` (gratis, sin key).

Estrategia:
  1. Bajar fixtures de thesportsdb (van publicando por jornadas — al inicio
     solo están los primeros 15 partidos, el resto se llenan progresivamente)
  2. Guardar venue_calendar.json con mapeo (date, home_tla|home_name, away_tla|away_name) → venue
  3. Fallback por país anfitrión cuando no hay match (USA → SoFi, MEX → Azteca, CAN → BMO)

Refresh: el cron diario debe llamar refresh_calendar() al inicio para
captar venues recién publicados.
"""
from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from typing import Optional

import requests

THE_SPORTS_DB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
WC_LEAGUE_ID = 4429
WC_SEASON = "2026"

CALENDAR_FILE = Path(__file__).parent / "venue_calendar.json"
CACHE_TTL = 3600 * 24  # 24h


# Fallback por país anfitrión cuando no hay venue específico
HOST_DEFAULTS = {
    "USA":    "MetLife Stadium",
    "MEX":    "Estadio Azteca",
    "CAN":    "BMO Field",
    "United States": "MetLife Stadium",
    "Mexico": "Estadio Azteca",
    "Canada": "BMO Field",
}

# Normalización de nombres antiguos/alternos
VENUE_NORMALIZATION = {
    "Reliant Stadium": "NRG Stadium",          # Houston nombre antiguo
    "Estadio Banorte": "Estadio Azteca",       # nombre patrocinado
}

# Grupos de equivalencia: cada set contiene todos los nombres que se refieren
# al mismo país/selección (inglés, español, abreviaturas, alternos).
# Altenar/PlayDoit México usa nombres en ESPAÑOL — incluyendo tildes.
# _unaccent() normaliza tildes antes de buscar en estos grupos.
ALIAS_GROUPS: list[set[str]] = [
    # ── Variantes inglesas ──────────────────────────────────────────────────
    {"czechia", "czech republic"},
    {"usa", "united states"},
    {"iran", "ir iran"},
    {"côte d'ivoire", "ivory coast"},
    {"cabo verde", "cape verde"},
    {"north macedonia", "republic of north macedonia", "macedonia"},
    {"bosnia-herzegovina", "bosnia and herzegovina"},
    # ── Español ↔ Inglés (Altenar PlayDoit México) ──────────────────────────
    {"germany", "alemania"},
    {"spain", "españa"},           # "españa" → unaccent → "espana" ≠ "spain"
    {"france", "francia"},
    {"netherlands", "países bajos", "paises bajos", "holanda"},
    {"belgium", "bélgica"},
    {"brazil", "brasil"},
    {"japan", "japón"},            # "japón" → unaccent → "japon" ≠ "japan"
    {"switzerland", "suiza"},
    {"south africa", "sudáfrica"},
    {"morocco", "marruecos"},
    {"new zealand", "nueva zelanda"},
    {"cameroon", "camerún"},
    {"romania", "rumania"},
    {"slovenia", "eslovenia"},
    {"iraq", "irak"},
    {"saudi arabia", "arabia saudita"},
    {"north korea", "corea del norte", "korea dpr"},
    {"south korea", "corea del sur", "korea republic"},
    {"czechia", "chequia", "czech republic"},
    {"united states", "estados unidos", "usa"},
    {"iran", "irán", "ir iran"},
    {"ivory coast", "costa de marfil", "côte d'ivoire"},
    {"england", "inglaterra"},
    {"croatia", "croacia"},
    {"turkey", "turquía"},
    {"scotland", "escocia"},
    {"hungary", "hungría"},
    {"slovakia", "eslovaquia"},
    {"egypt", "egipto"},
    {"tunisia", "túnez", "tunicia"},
    {"jordan", "jordania"},
    {"australia", "australia"},
    {"serbia", "serbia"},
    {"nigeria", "nigeria"},
    {"ghana", "ghana"},
    {"senegal", "senegal"},
    {"venezuela", "venezuela"},
    {"colombia", "colombia"},
    {"ecuador", "ecuador"},
    {"uruguay", "uruguay"},
    {"argentina", "argentina"},
    {"portugal", "portugal"},
    {"austria", "austria"},
    {"panama", "panamá"},
    {"honduras", "honduras"},
    {"costa rica", "costa rica"},
]
# Construir índice unaccented → grupo (para lookup O(1))
def _unaccent(s: str) -> str:
    """Quita tildes/diacríticos: México→mexico, España→espana, Países Bajos→paises bajos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower().strip())
        if unicodedata.category(c) != "Mn"
    )


def _name_variants(name: str) -> set[str]:
    """
    Devuelve todas las variantes conocidas de un nombre (con unaccent + alias groups).
    Cubre football-data.org (inglés) ↔ Altenar/PlayDoit (español con tildes).
    """
    n_lower   = name.lower().strip()
    n_unacc   = _unaccent(name)
    variants  = {n_lower, n_unacc}

    for group in ALIAS_GROUPS:
        group_unacc = {_unaccent(g) for g in group}
        # Match por nombre exacto (con tilde) o por versión sin tilde
        if n_lower in group or n_unacc in group_unacc:
            variants.update(g.lower() for g in group)
            variants.update(group_unacc)
            break

    return variants


def _normalize(venue: str) -> str:
    return VENUE_NORMALIZATION.get(venue, venue)


def refresh_calendar(force: bool = False) -> dict:
    """Baja fixtures de thesportsdb y actualiza venue_calendar.json."""
    if not force and CALENDAR_FILE.exists():
        age = time.time() - CALENDAR_FILE.stat().st_mtime
        if age < CACHE_TTL:
            return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))

    url = f"{THE_SPORTS_DB_BASE}/eventsseason.php?id={WC_LEAGUE_ID}&s={WC_SEASON}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[venue_calendar] fallo thesportsdb: {e}")
        return _load_existing()

    events = data.get("events") or []
    mapping = {}
    for e in events:
        venue = e.get("strVenue")
        if not venue:
            continue
        venue = _normalize(venue)
        date = e.get("dateEvent")  # YYYY-MM-DD
        home = (e.get("strHomeTeam") or "").strip()
        away = (e.get("strAwayTeam") or "").strip()
        if not date or not home or not away:
            continue
        key = f"{date}|{home}|{away}"
        mapping[key] = {
            "venue": venue,
            "date": date,
            "home": home,
            "away": away,
            "kickoff_utc": e.get("strTimestamp"),
        }

    payload = {
        "updated_at": time.time(),
        "total_events": len(events),
        "with_venue": len(mapping),
        "mapping": mapping,
    }
    CALENDAR_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _load_existing() -> dict:
    if CALENDAR_FILE.exists():
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    return {"mapping": {}, "updated_at": 0, "total_events": 0, "with_venue": 0}


def resolve_venue(date: str, home_name: str, away_name: str,
                   host_country: Optional[str] = None) -> Optional[str]:
    """
    Devuelve nombre de sede para un partido. Prioridad:
      1. Match exacto en venue_calendar
      2. Match fuzzy por substring del nombre (StatsBomb usa "United States" vs football-data "USA")
      3. Fallback por país anfitrión (HOST_DEFAULTS)
    """
    cal = _load_existing()
    mapping = cal.get("mapping", {})

    # Match exacto
    key = f"{date}|{home_name}|{away_name}"
    if key in mapping:
        return mapping[key]["venue"]

    # Match fuzzy por fecha + variantes de nombre conocidas
    home_variants = _name_variants(home_name)
    away_variants = _name_variants(away_name)
    for k, v in mapping.items():
        if not k.startswith(date):
            continue
        mapped_home = v["home"].lower()
        mapped_away = v["away"].lower()
        home_match = any(hv in mapped_home or mapped_home in hv for hv in home_variants)
        away_match = any(av in mapped_away or mapped_away in av for av in away_variants)
        if home_match and away_match:
            return v["venue"]

    # Fallback por país anfitrión
    if host_country:
        return HOST_DEFAULTS.get(host_country)
    return None


if __name__ == "__main__":
    print("[venue_calendar] refrescando…")
    payload = refresh_calendar(force=True)
    print(f"  total events: {payload['total_events']}")
    print(f"  con venue:    {payload['with_venue']}")
    print(f"\n  Primeros 5:")
    for k, v in list(payload["mapping"].items())[:5]:
        print(f"    {v['date']} {v['home']:20s} vs {v['away']:20s} → {v['venue']}")

    print("\n  Tests resolve_venue:")
    print(f"    resolve('2026-06-11', 'Mexico', 'South Africa') → {resolve_venue('2026-06-11', 'Mexico', 'South Africa')}")
    print(f"    resolve('2026-06-25', 'Brazil', 'Cameroon') → {resolve_venue('2026-06-25', 'Brazil', 'Cameroon', 'USA')}")
    print(f"    resolve('2026-07-19', 'France', 'Argentina') → {resolve_venue('2026-07-19', 'France', 'Argentina', 'USA')}")
