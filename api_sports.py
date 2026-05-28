"""
api_sports.py — Cliente unificado para api-sports.io (api-football, api-basketball, api-hockey)

Cubre los deportes que football-data.org NO cubre en free tier:
  - Liga MX (api-football.com)
  - NBA (api-basketball.com)
  - NHL + IIHF Hockey (api-hockey.com)

Setup:
  - Registro: https://www.api-football.com/  (la misma cuenta sirve para los 4 sports)
  - Free tier: 100 requests/día por endpoint
  - Variable de entorno: API_SPORTS_KEY

Endpoints unificados:
  Football  → https://v3.football.api-sports.io
  Basketball → https://v1.basketball.api-sports.io
  Hockey    → https://v1.hockey.api-sports.io
"""
from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, Literal
from urllib.request import Request, urlopen
from urllib.error  import HTTPError, URLError
from urllib.parse  import urlencode

from stats_engine import TeamRating, DEFAULT_TEAM

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

API_KEY = (os.environ.get("API_SPORTS_KEY") or "").lstrip("﻿").strip()

Sport = Literal["football", "basketball", "hockey"]

API_BASES: dict[Sport, str] = {
    "football":   "https://v3.football.api-sports.io",
    "basketball": "https://v1.basketball.api-sports.io",
    "hockey":     "https://v1.hockey.api-sports.io",
}

# IDs de ligas en api-sports.io (necesarios para fetchear partidos por liga)
# Football
LIGA_MX_ID    = 262     # Liga MX
MLS_ID        = 253     # Major League Soccer
# Basketball
NBA_ID        = 12      # NBA en api-basketball
# Hockey
NHL_ID        = 57      # NHL en api-hockey
IIHF_WC_ID    = 111     # World Championship (IIHF)

# Aliases nombre PlayDoit → nombre canónico api-sports
TEAM_ALIASES_HOCKEY: dict[str, str] = {
    # NHL teams (nombres como aparecen en PlayDoit)
    "MTL Canadiens":          "Montreal Canadiens",
    "CAR Hurricanes":          "Carolina Hurricanes",
    "Colorado Eagles":         "Colorado Eagles",
    "Chicago Wolves":          "Chicago Wolves",
    # IIHF (España)
    "Canadá":                  "Canada",
    "Estados Unidos":          "USA",
    "República Checa":         "Czech Republic",
    "Suiza":                   "Switzerland",
    "Suecia":                  "Sweden",
    "Finlandia":               "Finland",
    "Noruega":                 "Norway",
    "Letonia":                 "Latvia",
    "Alemania":                "Germany",
}

TEAM_ALIASES_BASKETBALL: dict[str, str] = {
    # NBA — los nombres ya son estándar
    "LA Lakers":              "Los Angeles Lakers",
    "Phila 76ers":            "Philadelphia 76ers",
    "OKC Thunder":            "Oklahoma City Thunder",
    "GS Warriors":            "Golden State Warriors",
    "NY Knicks":              "New York Knicks",
}

TEAM_ALIASES_LIGA_MX: dict[str, str] = {
    "Club America":           "Club América",
    "Cruz Azul":              "Cruz Azul",
    "Chivas":                 "Guadalajara",
    "Pumas":                  "U.N.A.M.- Pumas",
    "Tigres":                 "Tigres UANL",
    "Monterrey":              "Monterrey",
    "Toluca":                 "Toluca",
    "Pachuca":                "Pachuca",
}

CACHE_DIR = Path(os.environ.get("TEAM_FORM_CACHE_DIR", "/tmp"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "api_sports_cache.json"
CACHE_TTL_SECS = 24 * 3600


# ─────────────────────────────────────────────────────────────────────────────
# Cache + throttling
# ─────────────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"[ApiSports] Cache save fail: {e}")


def _cache_get(key: str) -> Optional[dict]:
    cache = _load_cache()
    entry = cache.get(key)
    if not entry:
        return None
    if time.time() - entry.get("_ts", 0) > CACHE_TTL_SECS:
        return None
    return entry.get("data")


def _cache_set(key: str, data: dict) -> None:
    cache = _load_cache()
    cache[key] = {"_ts": time.time(), "data": data}
    _save_cache(cache)


_recent_requests: list[float] = []
_THROTTLE_MAX = 10
_THROTTLE_WINDOW = 60.0


def _throttle() -> None:
    now = time.time()
    while _recent_requests and now - _recent_requests[0] > _THROTTLE_WINDOW:
        _recent_requests.pop(0)
    if len(_recent_requests) >= _THROTTLE_MAX:
        wait = _THROTTLE_WINDOW - (now - _recent_requests[0]) + 0.5
        if wait > 0:
            time.sleep(wait)
    _recent_requests.append(time.time())


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _api_get(sport: Sport, path: str, params: Optional[dict] = None) -> Optional[dict]:
    if not API_KEY:
        log.warning("[ApiSports] API_SPORTS_KEY no configurado")
        return None

    base = API_BASES[sport]
    url = f"{base}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    _throttle()
    try:
        req = Request(url, headers={
            "x-apisports-key": API_KEY,
            "User-Agent": "PlaydoitBot/5.0",
        })
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            errs = data.get("errors", {})
            if errs and (isinstance(errs, dict) and errs) or (isinstance(errs, list) and errs):
                log.warning(f"[ApiSports] errors={errs}")
            return data
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        log.warning(f"[ApiSports] HTTP {e.code}: {body}")
    except URLError as e:
        log.warning(f"[ApiSports] URL: {e}")
    except Exception as e:
        log.warning(f"[ApiSports] error: {e}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Resolver team_id
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_alias(name: str, sport: Sport) -> str:
    if sport == "hockey":
        return TEAM_ALIASES_HOCKEY.get(name, name)
    if sport == "basketball":
        return TEAM_ALIASES_BASKETBALL.get(name, name)
    return TEAM_ALIASES_LIGA_MX.get(name, name)


def _find_team_id(name: str, sport: Sport, league_id: int) -> Optional[int]:
    alias = _resolve_alias(name, sport)
    cache_key = f"team_id:{sport}:{league_id}:{alias.lower()}"
    cached = _cache_get(cache_key)
    if cached and "id" in cached:
        return cached["id"]

    # Free tier de api-sports.io solo permite seasons 2022-2024
    # Para ratings, usamos la más reciente accesible (2024)
    season = "2024"

    # Endpoint /teams para cada sport
    data = _api_get(sport, "/teams", {"league": league_id, "season": season})
    if not data or not data.get("response"):
        return None

    target = alias.lower().strip()
    for item in data["response"]:
        team = item.get("team") or item   # algunos endpoints wrappean en "team"
        team_name = (team.get("name") or "").lower()
        if team_name == target or target in team_name or team_name in target:
            tid = team.get("id")
            _cache_set(cache_key, {"id": tid, "name": team.get("name")})
            return tid
    log.warning(f"[ApiSports] No team_id para '{name}' (sport={sport}, liga={league_id})")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fetch team rating
# ─────────────────────────────────────────────────────────────────────────────

def _hockey_avg_goals(team_id: int, league_id: int, last_n: int = 12) -> Optional[tuple[float, float, int]]:
    """
    Devuelve (atq, def_, sample) calculado desde últimos N partidos finalizados.
    Free tier limit: no permite ?last= ni current season. Usa season 2024 + filtra por team.
    """
    season = "2024"
    data = _api_get("hockey", "/games", {
        "team": team_id, "season": season,
    })
    if not data:
        return None
    games = data.get("response", []) or []
    # Ordenar por fecha desc y tomar últimos N
    games_sorted = sorted(games,
                          key=lambda g: g.get("timestamp", 0),
                          reverse=True)
    goals_for_total = goals_against_total = count = 0
    for g in games_sorted[:last_n]:
        status_short = ((g.get("status") or {}).get("short") or "").upper()
        if status_short not in ("FT", "AOT", "AP"):
            continue
        scores = g.get("scores") or {}
        # Estructura free tier: scores.home y scores.away son ints directos
        home_score = scores.get("home")
        away_score = scores.get("away")
        if home_score is None or away_score is None:
            continue
        # Coerce to int (puede venir como dict en planes pagados)
        if isinstance(home_score, dict):
            home_score = home_score.get("total")
        if isinstance(away_score, dict):
            away_score = away_score.get("total")
        if home_score is None or away_score is None:
            continue
        is_home = (g.get("teams") or {}).get("home", {}).get("id") == team_id
        if is_home:
            goals_for_total     += home_score
            goals_against_total += away_score
        else:
            goals_for_total     += away_score
            goals_against_total += home_score
        count += 1
    if count == 0:
        return None
    return goals_for_total / count, goals_against_total / count, count


def fetch_hockey_rating(team_name: str, league: str, n_matches: int = 12) -> TeamRating:
    """
    Calcula rating Poisson de un equipo de hockey (NHL o IIHF) desde forma reciente.
    Devuelve DEFAULT_TEAM con sample=0 si no se encuentra data.
    """
    # Decidir league_id según el campeonato
    if "NHL" in league.upper() or "national hockey" in league.lower():
        league_id = NHL_ID
    elif "IIHF" in league.upper() or "mundial" in league.lower() or "world championship" in league.lower():
        league_id = IIHF_WC_ID
    else:
        log.info(f"[ApiSports] Liga hockey no soportada: {league}")
        return DEFAULT_TEAM

    cache_key = f"hockey_rating:{team_name.lower()}:{league_id}:{n_matches}"
    cached = _cache_get(cache_key)
    if cached:
        return TeamRating(
            atq=cached["atq"], def_=cached["def"],
            cpg=8.0,   # hockey no usa córners, valor neutro
            estilo="B",
            sample_size=cached["sample"],
        )

    team_id = _find_team_id(team_name, "hockey", league_id)
    if not team_id:
        return DEFAULT_TEAM

    res = _hockey_avg_goals(team_id, league_id, last_n=n_matches)
    if not res:
        return DEFAULT_TEAM
    atq, def_, sample = res

    _cache_set(cache_key, {"atq": atq, "def": def_, "sample": sample})
    return TeamRating(atq=atq, def_=def_, cpg=8.0, estilo="B", sample_size=sample)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not API_KEY:
        print("⚠ API_SPORTS_KEY no configurado.")
        print("  Registro: https://www.api-football.com/")
        raise SystemExit(1)
    print("=== Test hockey: Canadá vs USA (IIHF Mundial) ===")
    r = fetch_hockey_rating("Canadá", "IIHF Campeonato Mundial", n_matches=10)
    print(f"  Canadá:  atq={r.atq:.2f} def={r.def_:.2f} sample={r.sample_size}")
    r = fetch_hockey_rating("Estados Unidos", "IIHF Campeonato Mundial", n_matches=10)
    print(f"  USA:     atq={r.atq:.2f} def={r.def_:.2f} sample={r.sample_size}")
