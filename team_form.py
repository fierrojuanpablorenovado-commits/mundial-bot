"""
team_form.py — Calcula TeamRating dinámico desde football-data.org

Flujo:
  1. Resolver nombre de equipo → ID (búsqueda fuzzy en /v4/teams)
  2. Fetch últimos N partidos finalizados del equipo (/v4/teams/{id}/matches)
  3. Calcular avg goles a favor (atq), avg goles en contra (def)
  4. Ajustar por home/away separadamente
  5. Devolver TeamRating con sample_size para confianza

Fuente: football-data.org API gratis (10 req/min, suficiente para nuestro caso).
Cache local 24h en /tmp/team_form_cache.json para minimizar requests.

API Key:
  export FOOTBALL_DATA_API_KEY=xxxxx
  (gratis en https://www.football-data.org/client/register)
"""
from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error  import HTTPError, URLError
from urllib.parse  import quote

from stats_engine import TeamRating, DEFAULT_TEAM, Estilo

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

API_BASE = "https://api.football-data.org/v4"
API_KEY = (os.environ.get("FOOTBALL_DATA_API_KEY") or "").lstrip("﻿").strip()

CACHE_DIR = Path(os.environ.get("TEAM_FORM_CACHE_DIR", "/tmp"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "team_form_cache.json"
CACHE_TTL_SECS = 24 * 3600  # 24h

# Liga code en football-data.org (free tier)
# Clave = nombre EXACTO que PlayDoit/Altenar usa en champ.name
LEAGUE_CODES: dict[str, str] = {
    "Bundesliga":           "BL1",
    "Premier League":       "PL",
    "Primera División":     "PD",   # La Liga, nombre Altenar
    "La Liga":              "PD",
    "Serie A":              "SA",
    "Ligue 1":              "FL1",
    "Eredivisie":           "DED",
    "UEFA Champions League": "CL",
    "Champions League":     "CL",
    "Primeira Liga":        "PPL",
    "Championship":         "ELC",
    "Brasileiro Serie A":   "BSA",   # ACTIVO en mayo-noviembre
    "Campeonato Brasileiro Série A": "BSA",
    "Brasileirão Serie A":  "BSA",
    "Copa do Brasil":       None,    # NO en free tier
    # Mundial 2026 (junio-julio):
    "Copa Mundial":         "WC",
    "FIFA World Cup":       "WC",
    "Eurocopa":             "EC",
}
# Filtra ligas sin código (None) en el filtro de candidatos
LEAGUE_CODES = {k: v for k, v in LEAGUE_CODES.items() if v}

# Aliases: nombre como aparece en PlayDoit → nombre canónico en football-data.org
# Clave = string EXACTO que viene en `home_team` o `away_team` desde event_catalog.py
TEAM_ALIASES: dict[str, str] = {
    # Bundesliga (off-season ahora, pero ya queda listo para agosto)
    "Bayern Munich":     "Bayern München",
    "Bayern Munchen":    "Bayern München",

    # Premier League
    "Manchester City":   "Manchester City FC",
    "Manchester United": "Manchester United FC",
    "Liverpool":         "Liverpool FC",
    "Arsenal":           "Arsenal FC",
    "Chelsea":           "Chelsea FC",

    # La Liga
    "Atletico Madrid":   "Club Atlético de Madrid",
    "Atlético de Madrid": "Club Atlético de Madrid",
    "FC Barcelona":      "FC Barcelona",
    "Barcelona":         "FC Barcelona",

    # Serie A
    "Juventus":          "Juventus FC",
    "AC Milan":          "AC Milan",
    "Inter":             "FC Internazionale Milano",
    "Inter Milan":       "FC Internazionale Milano",
    "PSG":               "Paris Saint-Germain FC",
    "Paris Saint-Germain": "Paris Saint-Germain FC",

    # Brasileirão Serie A — ACTIVO mayo-noviembre 2026
    "Flamengo":          "CR Flamengo",
    "Coritiba":          "Coritiba FBC",
    "Grêmio":            "Grêmio FBPA",
    "Gremio":            "Grêmio FBPA",
    "Corinthians":       "SC Corinthians Paulista",
    "Bahia":             "EC Bahia",
    "Botafogo":          "Botafogo FR",
    "Santos":            "Santos FC",
    "EC Vitoria BA":     "EC Vitória",
    "Bragantino":        "RB Bragantino",
    "Internacional":     "SC Internacional",
    "Vasco da Gama":     "CR Vasco da Gama",
    "Atlético-MG":       "CA Mineiro",
    "Atletico-MG":       "CA Mineiro",
    "Palmeiras":         "SE Palmeiras",
    "Chapecoense":       "Chapecoense AF",
    "Cruzeiro":          "Cruzeiro EC",
    "Fluminense":        "Fluminense FC",
    "Remo":              "Clube do Remo",
    "Sao Paulo FC":      "São Paulo FC",
    "Mirassol":          "Mirassol FC",
    "CA Paranaense":     "CA Paranaense",
}

DEFAULT_ESTILO: dict[str, Estilo] = {
    # Equipos conocidos por estilo (rough mapping, mejorable)
    "Manchester City FC":     "P",
    "FC Barcelona":           "P",
    "Real Madrid":            "B",
    "Bayern München":         "P",
    "Liverpool FC":           "V",
    "Atlético de Madrid":     "D",
    "Juventus FC":            "B",
    "FC Internazionale Milano": "B",
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache
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
        log.warning(f"[Cache] No se pudo guardar: {e}")


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


# ─────────────────────────────────────────────────────────────────────────────
# HTTP a football-data.org
# ─────────────────────────────────────────────────────────────────────────────

# Estado para throttling: timestamps de las últimas requests
_recent_requests: list[float] = []
_THROTTLE_MAX_PER_MIN = 10  # límite del free tier de football-data.org
_THROTTLE_WINDOW = 60.0


def _throttle() -> None:
    """Espera si estamos cerca del rate limit (10 req/min en free tier)."""
    now = time.time()
    # Quitar requests viejas (>60s)
    while _recent_requests and now - _recent_requests[0] > _THROTTLE_WINDOW:
        _recent_requests.pop(0)
    # Si llegamos al límite, esperar hasta que la más vieja salga de la ventana
    if len(_recent_requests) >= _THROTTLE_MAX_PER_MIN:
        wait = _THROTTLE_WINDOW - (now - _recent_requests[0]) + 0.5
        if wait > 0:
            log.info(f"[API] Throttle: esperando {wait:.1f}s (límite 10/min)")
            time.sleep(wait)
    _recent_requests.append(time.time())


def _api_get(path: str, params: Optional[dict] = None, retries: int = 2) -> Optional[dict]:
    """GET con header X-Auth-Token. Maneja throttling y 429 retry."""
    if not API_KEY:
        log.warning("[API] FOOTBALL_DATA_API_KEY no configurado")
        return None
    url = f"{API_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url = f"{url}?{qs}"

    for attempt in range(retries + 1):
        _throttle()
        try:
            req = Request(url, headers={
                "X-Auth-Token": API_KEY,
                "User-Agent": "PlaydoitBot/5.0",
            })
            with urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            if e.code == 429 and attempt < retries:
                # Buscar segundos a esperar en el mensaje
                import re as _re
                m = _re.search(r"Wait (\d+) seconds", body)
                wait = int(m.group(1)) + 1 if m else 30
                log.warning(f"[API] 429 — esperando {wait}s (intento {attempt+1}/{retries+1})")
                time.sleep(wait)
                continue
            log.warning(f"[API] HTTP {e.code}: {body}")
            return None
        except URLError as e:
            log.warning(f"[API] URL error: {e}")
            return None
        except Exception as e:
            log.warning(f"[API] error: {e}")
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Resolver nombre de equipo → ID
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Limpia nombre para matching: minúsculas, sin diacríticos básicos."""
    n = name.lower().strip()
    n = n.replace(".", "").replace("'", "").replace("-", " ")
    # Quitar prefijos comunes
    for prefix in ("fc ", "ac ", "as ", "ss ", "vf l ", "vfl ", "ssc ", "1 ", "1. "):
        if n.startswith(prefix):
            n = n[len(prefix):]
    # Quitar sufijos comunes
    for suffix in (" fc", " cf", " sc", " ac", " bc"):
        if n.endswith(suffix):
            n = n[:-len(suffix)]
    return n.strip()


def find_team_id(team_name: str, league: str = "") -> Optional[int]:
    """
    Busca el ID del equipo en football-data.org.
    1. Si el nombre exacto está en alias → usa ese
    2. Si liga está soportada → busca en /competitions/{code}/teams
    3. Fallback: búsqueda fuzzy general
    """
    name = TEAM_ALIASES.get(team_name, team_name)
    cache_key = f"team_id:{_normalize_name(name)}"

    cached = _cache_get(cache_key)
    if cached and "id" in cached:
        return cached["id"]

    target = _normalize_name(name)

    # Búsqueda dentro de la liga (más preciso)
    league_code = LEAGUE_CODES.get(league)
    if league_code:
        data = _api_get(f"/competitions/{league_code}/teams")
        if data:
            for team in data.get("teams", []):
                candidates = [team.get("name", ""), team.get("shortName", "")]
                candidates.extend(team.get("aliases", []) or [])
                for cand in candidates:
                    if _normalize_name(cand) == target:
                        tid = team.get("id")
                        _cache_set(cache_key, {"id": tid, "name": team.get("name")})
                        return tid
            # Match parcial (más permisivo)
            for team in data.get("teams", []):
                cand = _normalize_name(team.get("name", ""))
                if target in cand or cand in target:
                    tid = team.get("id")
                    _cache_set(cache_key, {"id": tid, "name": team.get("name")})
                    return tid

    log.warning(f"[Form] No se encontró ID para '{team_name}' (liga={league})")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Fetch forma reciente y calcular TeamRating
# ─────────────────────────────────────────────────────────────────────────────

def fetch_team_rating(
    team_name: str,
    league: str = "",
    n_matches: int = 15,
) -> TeamRating:
    """
    Calcula TeamRating dinámicamente desde forma reciente.

    Devuelve DEFAULT_TEAM con sample_size=0 si no hay datos disponibles.
    El sample_size indica cuántos partidos se usaron — la confianza del rating.
    """
    cache_key = f"rating:{_normalize_name(team_name)}:{n_matches}"
    cached = _cache_get(cache_key)
    if cached:
        return TeamRating(
            atq=cached["atq"],
            def_=cached["def"],
            cpg=cached.get("cpg", 9.0),
            estilo=cached.get("estilo", "B"),
            sample_size=cached.get("sample_size", 0),
        )

    team_id = find_team_id(team_name, league)
    if not team_id:
        return DEFAULT_TEAM

    data = _api_get(f"/teams/{team_id}/matches", params={
        "status": "FINISHED",
        "limit": n_matches,
    })
    if not data:
        return DEFAULT_TEAM

    matches = data.get("matches", [])[:n_matches]
    if not matches:
        return DEFAULT_TEAM

    goals_for_total = 0
    goals_against_total = 0
    count = 0
    for m in matches:
        score = (m.get("score") or {}).get("fullTime") or {}
        home_g = score.get("home")
        away_g = score.get("away")
        if home_g is None or away_g is None:
            continue
        is_home = (m.get("homeTeam") or {}).get("id") == team_id
        if is_home:
            goals_for_total     += home_g
            goals_against_total += away_g
        else:
            goals_for_total     += away_g
            goals_against_total += home_g
        count += 1

    if count == 0:
        return DEFAULT_TEAM

    atq = goals_for_total / count
    def_ = goals_against_total / count

    # Estilo conocido o B por default
    estilo = DEFAULT_ESTILO.get(team_name, DEFAULT_ESTILO.get(
        TEAM_ALIASES.get(team_name, team_name), "B"))

    # CPG no viene de football-data.org tier gratis — usar default ajustado
    cpg = 9.5 if atq > 1.8 else 9.0 if atq > 1.3 else 8.5

    rating_data = {
        "atq": atq, "def": def_, "cpg": cpg, "estilo": estilo,
        "sample_size": count,
    }
    _cache_set(cache_key, rating_data)

    return TeamRating(atq=atq, def_=def_, cpg=cpg, estilo=estilo,
                      sample_size=count)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"API key configurada: {'sí' if API_KEY else 'NO — exporta FOOTBALL_DATA_API_KEY'}")
    if not API_KEY:
        print("\nObtén tu key gratis en: https://www.football-data.org/client/register")
        raise SystemExit(1)

    print("\n--- Test: Bayern München ---")
    r = fetch_team_rating("Bayern München", "Bundesliga", n_matches=10)
    print(f"atq={r.atq:.2f} | def={r.def_:.2f} | sample={r.sample_size}")

    print("\n--- Test: Real Madrid ---")
    r = fetch_team_rating("Real Madrid", "La Liga", n_matches=10)
    print(f"atq={r.atq:.2f} | def={r.def_:.2f} | sample={r.sample_size}")
