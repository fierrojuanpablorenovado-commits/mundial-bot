"""
mundial_odds.py — Provider de odds REALES de PlayDoit para el mundial-bot

Conecta dos sistemas:
  - football-data.org (fixtures del Mundial con sus IDs)
  - PlayDoit/Altenar (cuotas vivas via event_catalog)

Flujo:
  1. catalog = get_upcoming_catalog(48h)  → todos los eventos de fútbol en PlayDoit
  2. Para cada Match del mundial-bot, busca su evento Altenar por:
     - Fecha (mismo día UTC)
     - Nombres de equipos (fuzzy match con aliases)
  3. Extrae mercados O/U (1.5, 2.5, 3.5) y devuelve dict con la forma que
     analyze_mundial.analyze_day espera.

Si el evento no está en PlayDoit (mucho antes del partido, o ya empezó),
retorna None → analyze_day saltará ese partido.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import event_catalog
import mundial_data as md
from venue_calendar import _name_variants, _unaccent


# Cache local del catálogo (15 min — refrescar dentro de cada cron)
_CATALOG_CACHE: list[dict] = []
_CATALOG_TS: float = 0.0
_CACHE_TTL_SECONDS = 60 * 15


def _resolve_reference_dt() -> Optional[datetime]:
    """
    Lee TARGET_DATE del entorno (igual que mundial_runner.py) para que la ventana
    del catálogo WC se centre en la fecha objetivo, no en el día real de hoy.
    Crítico en simulaciones: TARGET_DATE=2026-06-11 mientras today=2026-05-29.
    """
    raw = os.environ.get("TARGET_DATE", "").strip()
    if not raw:
        return None
    try:
        d = datetime.strptime(raw, "%Y-%m-%d")
        return d.replace(hour=0, minute=0, second=0, tzinfo=timezone.utc)
    except ValueError:
        return None


def _get_catalog(hours_ahead: int = 48, force: bool = False) -> list[dict]:
    """
    Cache del catálogo PlayDoit para no quemar requests durante una corrida.

    Usa GetEventsByChamp(champId=3146) en lugar de GetUpcoming porque este último
    solo devuelve eventos en los próximos 4 días (ligas activas), y los partidos
    del Mundial (junio-julio) quedan fuera de esa ventana.
    """
    global _CATALOG_CACHE, _CATALOG_TS
    if not force and _CATALOG_CACHE and (time.time() - _CATALOG_TS) < _CACHE_TTL_SECONDS:
        return _CATALOG_CACHE
    try:
        ref_dt = _resolve_reference_dt()          # None en producción, TARGET_DATE en simulaciones
        days   = max(2, (hours_ahead // 24) + 1)  # hoy + mañana mínimo
        _CATALOG_CACHE = event_catalog.get_wc_catalog(days_ahead=days, reference_dt=ref_dt)
    except Exception as e:
        print(f"[mundial_odds] fallo wc_catalog: {e}")
    _CATALOG_TS = time.time()
    return _CATALOG_CACHE


def _names_match(a: str, b: str) -> bool:
    """
    Fuzzy match con alias groups + unaccent.
    Cubre football-data.org (inglés) ↔ Altenar/PlayDoit (español con tildes).
    Ejemplos: 'Germany' ↔ 'Alemania', 'South Africa' ↔ 'Sudáfrica', 'Mexico' ↔ 'México'.
    """
    if not a or not b:
        return False
    a_l, b_l = a.lower().strip(), b.lower().strip()
    if a_l == b_l:
        return True
    # Unaccent directo (México→mexico, Sudáfrica→sudafrica, etc.)
    a_ua, b_ua = _unaccent(a), _unaccent(b)
    if a_ua == b_ua:
        return True
    # Alias groups (alemania↔germany, brasil↔brazil, etc.) + variantes unaccented
    a_vars = _name_variants(a) | {a_l, a_ua}
    b_vars = _name_variants(b) | {b_l, b_ua}
    for av in a_vars:
        for bv in b_vars:
            if av == bv:
                return True
            # Substring solo para nombres ≥5 chars (evita falsos positivos con palabras cortas)
            if len(av) >= 5 and len(bv) >= 5 and (av in bv or bv in av):
                return True
    return False


def find_altenar_event(match: md.Match,
                        catalog: Optional[list[dict]] = None) -> Optional[dict]:
    """
    Mapea un Match (football-data) a su entry en el catálogo Altenar.
    Returns: dict del catálogo o None.
    """
    cat = catalog if catalog is not None else _get_catalog()
    if not cat:
        return None

    match_date = match.utc_kickoff[:10]
    for ev in cat:
        if ev.get("sport") != "futbol":
            continue
        ev_date = (ev.get("fecha_utc") or "")[:10]
        if ev_date != match_date:
            continue
        home_alt = ev.get("home_team", "")
        away_alt = ev.get("away_team", "")
        if _names_match(match.home_name, home_alt) and _names_match(match.away_name, away_alt):
            return ev
        # Reversed (algunos books listan al revés)
        if _names_match(match.home_name, away_alt) and _names_match(match.away_name, home_alt):
            return ev
    return None


def odds_provider_real(match: md.Match) -> Optional[dict]:
    """
    Provider real para analyze_mundial.analyze_day().
    Devuelve dict { "goles_o25": 1.85, "goles_u25": 1.95, ... } o None.
    """
    ev = find_altenar_event(match)
    if not ev:
        return None

    odds_dict: dict[str, float] = {}
    for m in ev.get("mercados", []):
        if m.get("market") != "goles":
            continue
        line = m.get("line", 0)
        side = m.get("type")
        cuota = m.get("cuota")
        if not (cuota and line and side):
            continue
        line_str = str(int(line * 10))  # 2.5 → "25"
        key = f"goles_{'o' if side == 'over' else 'u'}{line_str}"
        odds_dict[key] = float(cuota)

    if not odds_dict:
        return None

    # Adjuntamos el event_id y odd_ids para que el placement pueda usarlos después
    odds_dict["_altenar_event_id"] = ev["event_id"]
    odds_dict["_altenar_markets"] = {
        f"goles_{'o' if m['type']=='over' else 'u'}{int(m['line']*10)}": {
            "market_id": m["market_id"],
            "odd_id": m["odd_id"],
            "price": m["cuota"],
        }
        for m in ev.get("mercados", []) if m.get("market") == "goles"
    }
    return odds_dict


if __name__ == "__main__":
    print("[mundial_odds] Probando provider...")
    print("  NOTA: requiere conexión a PlayDoit/Altenar (no requiere login, solo lee catálogo público)")
    cat = _get_catalog(hours_ahead=72)
    print(f"  Catálogo: {len(cat)} eventos de fútbol")
    if cat:
        for ev in cat[:3]:
            print(f"    {ev['partido']} | {ev['league']} | {ev['fecha_utc'][:16]}")
