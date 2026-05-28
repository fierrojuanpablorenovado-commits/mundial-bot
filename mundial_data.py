"""
mundial_data.py — Fetch específico de Mundial FIFA 2026

Fuentes:
  • football-data.org (free 10 req/min) — fixtures, standings, results
    competition code: WC · id: 2000 · season: 2398 (2026-06-11 → 2026-07-19)
  • api-sports.io (free 100 req/día) — lineups, top scorers
    league id: 1 (World Cup) · season: 2026

Cache local en mundial-bot/cache/ con TTL para no quemar free tier.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
API_SPORTS_KEY    = os.environ.get("API_SPORTS_KEY", "")

WC_COMPETITION_CODE = "WC"
WC_COMPETITION_ID   = 2000
WC_SEASON_ID        = 2398
API_SPORTS_LEAGUE_ID = 1
API_SPORTS_SEASON    = 2026

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# TTL por tipo de recurso (segundos)
TTL = {
    "fixtures":      3600 * 12,   # 12h: fixtures cambian poco
    "standings":     3600 * 2,    # 2h: post-jornada actualiza rápido
    "lineups":       60 * 15,     # 15min: cerca del partido cambia
    "top_scorers":   3600 * 24,   # 24h
    "competition":   3600 * 24,
    "team":          3600 * 24 * 7,
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace("?", "_").replace("=", "_").replace("&", "_")
    return CACHE_DIR / f"{safe}.json"


def _read_cache(key: str, ttl: int) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > ttl:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(key: str, data: dict) -> None:
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# football-data.org
# ─────────────────────────────────────────────────────────────────────────────

FD_BASE = "https://api.football-data.org/v4"


def fd_get(path: str, ttl_key: str = "fixtures", force: bool = False) -> dict:
    """GET con cache + rate limit awareness."""
    cache_key = f"fd_{path}"
    if not force:
        cached = _read_cache(cache_key, TTL[ttl_key])
        if cached is not None:
            return cached

    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    url = f"{FD_BASE}{path}"
    r = requests.get(url, headers=headers, timeout=20)

    if r.status_code == 429:
        time.sleep(7)  # free tier 10/min → espera y reintenta una vez
        r = requests.get(url, headers=headers, timeout=20)

    r.raise_for_status()
    data = r.json()
    _write_cache(cache_key, data)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# api-sports.io
# ─────────────────────────────────────────────────────────────────────────────

AS_BASE = "https://v3.football.api-sports.io"


def as_get(path: str, params: Optional[dict] = None, ttl_key: str = "fixtures",
           force: bool = False) -> dict:
    """GET api-sports.io con cache."""
    params = params or {}
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    cache_key = f"as_{path}_{qs}"

    if not force:
        cached = _read_cache(cache_key, TTL[ttl_key])
        if cached is not None:
            return cached

    headers = {"x-apisports-key": API_SPORTS_KEY}
    url = f"{AS_BASE}{path}"
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    _write_cache(cache_key, data)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de alto nivel — fixtures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Match:
    """Partido normalizado para uso interno del bot."""
    fd_id: int                       # football-data id
    utc_kickoff: str                 # ISO 8601
    matchday: int                    # 1=J1 grupos, ...
    stage: str                       # GROUP_STAGE, ROUND_OF_16, etc.
    group: Optional[str]             # "A", "B", ... o None en knockouts
    home_id: int
    home_name: str
    home_tla: str                    # ej "MEX", "FRA"
    away_id: int
    away_name: str
    away_tla: str
    status: str                      # SCHEDULED, IN_PLAY, FINISHED, ...
    home_goals: Optional[int]
    away_goals: Optional[int]
    venue: Optional[str]

    @property
    def is_finished(self) -> bool:
        return self.status == "FINISHED"

    @property
    def kickoff_dt(self) -> datetime:
        return datetime.fromisoformat(self.utc_kickoff.replace("Z", "+00:00"))


def _parse_fd_match(m: dict) -> Match:
    score = m.get("score", {}).get("fullTime", {}) or {}
    return Match(
        fd_id=m["id"],
        utc_kickoff=m["utcDate"],
        matchday=m.get("matchday") or 0,
        stage=m.get("stage", ""),
        group=m.get("group"),
        home_id=m["homeTeam"]["id"],
        home_name=m["homeTeam"]["name"] or "",
        home_tla=m["homeTeam"].get("tla") or "",
        away_id=m["awayTeam"]["id"],
        away_name=m["awayTeam"]["name"] or "",
        away_tla=m["awayTeam"].get("tla") or "",
        status=m["status"],
        home_goals=score.get("home"),
        away_goals=score.get("away"),
        venue=m.get("venue"),
    )


def get_all_matches(force: bool = False) -> list[Match]:
    """Trae los 104 partidos del Mundial 2026 (fixture completo)."""
    data = fd_get(f"/competitions/{WC_COMPETITION_CODE}/matches", "fixtures", force)
    return [_parse_fd_match(m) for m in data.get("matches", [])]


def get_matches_for_date(date_local: datetime, tz_offset_hours: int = -6) -> list[Match]:
    """
    Partidos cuya hora local (CDT México = UTC-6) cae en 'date_local'.
    """
    all_m = get_all_matches()
    target = date_local.date()
    result = []
    for m in all_m:
        local = m.kickoff_dt + timedelta(hours=tz_offset_hours)
        if local.date() == target:
            result.append(m)
    return sorted(result, key=lambda x: x.utc_kickoff)


def get_finished_matches_yesterday(tz_offset_hours: int = -6) -> list[Match]:
    """Para feedback al learning_tracker: partidos terminados ayer en CDT."""
    yesterday = datetime.now(timezone.utc) + timedelta(hours=tz_offset_hours) - timedelta(days=1)
    matches = get_matches_for_date(yesterday, tz_offset_hours)
    return [m for m in matches if m.is_finished]


# ─────────────────────────────────────────────────────────────────────────────
# Standings (después de J1)
# ─────────────────────────────────────────────────────────────────────────────

def get_standings(force: bool = False) -> dict:
    """
    Tabla de posiciones por grupo. Devuelve dict { "A": [team_row, ...], ... }.
    Vacío hasta que se juegue al menos un partido del grupo.
    """
    data = fd_get(f"/competitions/{WC_COMPETITION_CODE}/standings", "standings", force)
    result: dict[str, list[dict]] = {}
    for s in data.get("standings", []):
        group = s.get("group", "")  # "GROUP_A" → "A"
        key = group.replace("GROUP_", "") if group else "UNK"
        result[key] = s.get("table", [])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Lineups (api-sports.io — disponibles ~30-60 min antes del kickoff)
# ─────────────────────────────────────────────────────────────────────────────

def get_lineups_for_fixture(api_sports_fixture_id: int) -> list[dict]:
    """
    Returns [{ team, formation, startXI[], substitutes[] }, ...] (2 teams).
    Devuelve [] si aún no se publican.
    """
    data = as_get("/fixtures/lineups", {"fixture": api_sports_fixture_id}, "lineups")
    return data.get("response", [])


def get_api_sports_fixture_id(home_tla: str, away_tla: str, date_iso: str) -> Optional[int]:
    """
    Mapea fixture football-data → api-sports. Busca por fecha y nombres.
    date_iso = "YYYY-MM-DD".
    """
    data = as_get("/fixtures", {
        "league": API_SPORTS_LEAGUE_ID,
        "season": API_SPORTS_SEASON,
        "date":   date_iso,
    }, "fixtures")
    for fx in data.get("response", []):
        teams = fx.get("teams", {})
        h = (teams.get("home", {}).get("name") or "").upper()
        a = (teams.get("away", {}).get("name") or "").upper()
        # match por TLA o substring del nombre
        if home_tla.upper() in h and away_tla.upper() in a:
            return fx["fixture"]["id"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Top scorers (para star_player_form)
# ─────────────────────────────────────────────────────────────────────────────

def get_top_scorers() -> list[dict]:
    """Top goleadores del Mundial. Vacío antes del torneo, se llena con J1."""
    data = as_get("/players/topscorers", {
        "league": API_SPORTS_LEAGUE_ID,
        "season": API_SPORTS_SEASON,
    }, "top_scorers")
    return data.get("response", [])


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("─" * 70)
    print("mundial_data.py self-test")
    print("─" * 70)

    if not FOOTBALL_DATA_KEY:
        print("⚠  FOOTBALL_DATA_API_KEY no seteada — usa la del .env")
    else:
        comp = fd_get(f"/competitions/{WC_COMPETITION_CODE}", "competition")
        print(f"✅ Competition: {comp['name']} (id={comp['id']})")
        print(f"   Current season: {comp['currentSeason']['startDate']} → "
              f"{comp['currentSeason']['endDate']}")

        matches = get_all_matches()
        print(f"\n✅ Fixtures totales: {len(matches)}")

        # Primer partido del Mundial
        sched = [m for m in matches if m.status == "SCHEDULED"]
        if sched:
            first = sched[0]
            print(f"\n   Primer partido: {first.home_name} vs {first.away_name}")
            print(f"   Kickoff UTC: {first.utc_kickoff}")
            print(f"   Stage: {first.stage}, Grupo: {first.group}")
            print(f"   Sede: {first.venue}")

        # Partidos por jornada
        from collections import Counter
        by_md = Counter(m.matchday for m in matches if m.stage == "GROUP_STAGE")
        print(f"\n   Partidos fase de grupos por jornada: {dict(by_md)}")

    print("\n🎯 mundial_data.py OK")
