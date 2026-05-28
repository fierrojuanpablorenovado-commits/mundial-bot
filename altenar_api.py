"""
altenar_api.py — Login + bet placement via Altenar REST API (sin Playwright)

FLUJO COMPLETO VERIFICADO 13-May-2026:
  1. POST https://www.playdoit.mx/api/login
     - Content-Type: application/x-www-form-urlencoded; charset=UTF-8
     - Campo: "login" (no "email" ni "username")
     - Respuesta: {success:true} + cookie JSESSIONID
     → Guarda la sesión (requests.Session con cookies)

  2. GET https://www.playdoit.mx/api/game/real/250002?siteHost=www.playdoit.mx
     - Requiere JSESSIONID cookie en la sesión
     - Respuesta: {"token": "G0QX4NDL...", "url": "https://sb2integration-altenar2.biahosted.com..."}
     → token es el token específico de PlayDoit para Altenar (NO el JSESSIONID)

  3. POST https://sb2auth-altenar2.biahosted.com/api/WidgetAuth/SignIn
     - Payload: {token: "G0QX4NDL...", culture, integration, ...}
     - Respuesta: {"accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}
     → accessToken JWT es el token final para hacer apuestas

  4. POST https://sb2frontend-altenar2.biahosted.com/api/v1/bets
     - Payload: {customerToken: accessToken, bets: [...]}
     → Coloca la apuesta

FIXES CRÍTICOS:
  - 12-May: PlayDoit usa form-urlencoded (NO JSON), campo "login" (no "email")
  - 13-May: Altenar usa "accessToken" (NO "customerToken") en SignIn response
  - 13-May: Token para Altenar viene de /api/game/real/250002 (NO del JSESSIONID)
  - El gameId 250002 = Altenar Sportsbook en PlayDoit. walletCode=417362, skinid=playdoit2
"""
import os
import requests
import logging
from typing import Optional, Dict, List, Tuple
from urllib.parse import urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)


# ── Proxy residencial (env var PROXY_URL) ────────────────────────────────────
def _get_proxies_from_env() -> Optional[dict]:
    """
    Lee PROXY_URL del env y devuelve dict para requests/curl-cffi.

    Formato esperado de PROXY_URL: http://user:pass@host:port  (o https://...)
    Webshare/IPRoyal/Smartproxy entregan así.
    Retorna None si no está configurado o es vacío.
    """
    url = (os.environ.get("PROXY_URL") or "").lstrip("﻿").strip()
    if not url:
        return None
    return {"http": url, "https": url}


def _alt_proxy_port(proxy_url: str, new_port: int) -> Optional[str]:
    """Devuelve la misma URL de proxy pero con un puerto diferente. None si falla."""
    try:
        p = urlparse(proxy_url)
        if p.port == new_port:
            return None  # Ya tiene ese puerto
        if p.username:
            netloc = f"{p.username}:{p.password}@{p.hostname}:{new_port}"
        else:
            netloc = f"{p.hostname}:{new_port}"
        return urlunparse(p._replace(netloc=netloc))
    except Exception:
        return None


def _alt_proxy_host(proxy_url: str, new_host: str) -> Optional[str]:
    """Devuelve la misma URL de proxy pero con un hostname diferente. None si falla."""
    try:
        p = urlparse(proxy_url)
        if p.hostname == new_host:
            return None
        port = p.port or 80
        if p.username:
            netloc = f"{p.username}:{p.password}@{new_host}:{port}"
        else:
            netloc = f"{new_host}:{port}"
        return urlunparse(p._replace(netloc=netloc))
    except Exception:
        return None


def _build_proxy_attempts(proxies: Optional[dict]) -> list:
    """
    Devuelve lista de proxy configs a intentar, en orden de preferencia.
    Webshare residential solo soporta :80 — otros puertos simplemente timeout.

    Cadena: proxy:80 → PROXY_URL_BACKUP (si existe) → sin proxy (curl-cffi TLS)
    """
    if not proxies:
        return [None]
    attempts = [proxies]

    # Proxy de backup (segunda cuenta / segundo proveedor)
    backup_url = (os.environ.get("PROXY_URL_BACKUP") or "").lstrip("﻿").strip()
    if backup_url:
        attempts.append({"http": backup_url, "https": backup_url})

    # Fallback sin proxy — curl-cffi con TLS fingerprint Chrome puede bypasear
    # Cloudflare directamente desde GitHub Actions en algunos casos
    attempts.append(None)
    return attempts

PLAYDOIT_BASE    = "https://www.playdoit.mx"
ALTENAR_AUTH       = "https://sb2auth-altenar2.biahosted.com/api"
ALTENAR_FRONTEND   = "https://sb2frontend-altenar2.biahosted.com/api"
ALTENAR_BETGATEWAY = "https://sb2betgateway-altenar2.biahosted.com/api"
# Endpoint correcto de apuestas — descubierto via Playwright 14-May-2026
ALTENAR_PLACE_BET_URL = f"{ALTENAR_BETGATEWAY}/widget/placeWidget"

# gameId del sportsbook Altenar en PlayDoit (NO cambiar — verificado 13-May-2026)
PLAYDOIT_SPORTSBOOK_GAME_ID = 250002
PLAYDOIT_SITE_HOST          = "www.playdoit.mx"

BASE_PARAMS = {
    "culture": "es-ES",
    "timezoneOffset": 360,
    "integration": "playdoit2",
    "deviceType": 1,
    "numFormat": "en-GB",
    "countryCode": "MX",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Headers para login (form-urlencoded)
_LOGIN_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json",
    "Origin": PLAYDOIT_BASE,
    "Referer": f"{PLAYDOIT_BASE}/es/login",
    "User-Agent": _UA,
}

# Headers para APIs JSON
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": PLAYDOIT_BASE,
    "Referer": f"{PLAYDOIT_BASE}/",
    "User-Agent": _UA,
}


# ── Paso 1: Login en PlayDoit ─────────────────────────────────────────────────

def _playdoit_login(username: str, password: str,
                    proxies: Optional[dict] = None
                    ) -> Optional[requests.Session]:
    """
    POST /api/login en playdoit.mx con form-urlencoded.
    Campo correcto: "login" (descubierto en BillfoldClass.convertArguments).

    ESTRATEGIA ANTI-BLACKLIST (lección 25-May-2026):
    - Proxy SIEMPRE (GitHub Actions IP directo → siempre blacklist 108 en PlayDoit).
    - 403 Cloudflare: reintentar hasta 4 veces con pausa de 3s para rotar IP proxy.
    - 108 PlayDoit: abortar inmediatamente (IP válida pero bloqueada → no hacer más).
    - Sin proxy como fallback SOLO si proxy da error de red (502/tunnel).
    """
    import time as _time
    url = f"{PLAYDOIT_BASE}/api/login"
    payload_str = urlencode({"login": username, "password": password})

    if proxies is None:
        proxies = _get_proxies_from_env()

    def _build_sess(proxy_dict) -> requests.Session:
        sess = requests.Session()
        if proxy_dict:
            sess.proxies.update(proxy_dict)
        sess.headers.update({"User-Agent": _UA})
        return sess

    def _check_response(resp, sess: requests.Session) -> Optional[requests.Session]:
        try:
            data = resp.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            return None
        err = data.get("errorCode")
        if err:
            logger.warning(f"[API] Login errorCode={err} full={str(data)[:200]}")
            return None
        if data.get("success") or "JSESSIONID" in sess.cookies or not err:
            jsid = sess.cookies.get("JSESSIONID", "")
            logger.info(f"[API] ✅ Login OK. JSESSIONID len={len(jsid)}")
            return sess
        return None

    # ESTRATEGIA REVISADA (27-May-2026):
    # Webshare IPs están blacklisteadas (errorCode=108). Estrategia:
    #   - Fase 1: 2 intentos CON proxy (rotación de IP, pausa 5s)
    #   - Fase 2: 2 intentos SIN proxy (curl-cffi TLS fingerprint desde GitHub Actions)
    #             curl-cffi con chrome110/120 puede bypasear Cloudflare incluso desde datacenter.
    # Total: 4 intentos max. Mucho menos agresivo que 6 → reduce riesgo de blacklist.

    try:
        from curl_cffi import requests as cf_requests
    except ImportError:
        logger.error("[API] curl-cffi no disponible")
        return None

    # Construir secuencia de intentos: [(proxies_dict, impersonate, pausa_antes)]
    attempts_plan = []

    if proxies:
        # Fase 1: con proxy (2 intentos, pausa 5s entre ellos)
        attempts_plan.append((proxies, "chrome110", 0))
        attempts_plan.append((proxies, "chrome120", 5))

    # Fase 2: sin proxy — directo desde GitHub Actions / IP local (2 intentos)
    attempts_plan.append((None, "chrome110", 5 if proxies else 0))
    attempts_plan.append((None, "chrome120", 3))

    total = len(attempts_plan)
    for i, (attempt_proxies, imp, pause_before) in enumerate(attempts_plan, 1):
        label = (list(attempt_proxies.values())[0].split("@")[-1]
                 if attempt_proxies else "directo")
        if pause_before > 0 and i > 1:
            _time.sleep(pause_before)
        try:
            resp = cf_requests.post(
                url,
                data=payload_str,
                headers=_LOGIN_HEADERS,
                impersonate=imp,
                timeout=30,
                proxies=attempt_proxies,
            )
            logger.info(f"[API] Login [{i}/{total}] {imp} [{label}]: HTTP {resp.status_code}")

            if resp.status_code == 200:
                sess = _build_sess(attempt_proxies)
                for k, v in resp.cookies.items():
                    sess.cookies.set(k, v, domain="playdoit.mx")
                result = _check_response(resp, sess)
                if result:
                    return result
                try:
                    if resp.json().get("errorCode") == 108:
                        logger.warning(f"[API] 108 [{i}/{total}] — IP blacklisteada, siguiente intento...")
                        continue
                except Exception:
                    pass
            elif resp.status_code in (403, 429):
                logger.warning(f"[API] Cloudflare {resp.status_code} [{i}/{total}] — siguiente intento...")

        except Exception as e:
            err_str = str(e)
            if "502" in err_str or "proxy" in err_str.lower() or "tunnel" in err_str.lower():
                logger.warning(f"[API] Proxy error [{i}/{total}]: {err_str[:80]}")
            else:
                logger.error(f"[API] Login error [{i}/{total}]: {e}")

    logger.error(f"[API] ❌ PlayDoit login falló ({total} intentos agotados)")
    return None


# ── Paso 2: Obtener token de Altenar desde PlayDoit ────────────────────────────

def _get_altenar_game_token(session: requests.Session,
                             proxies: Optional[dict] = None) -> Optional[str]:
    """
    GET /api/game/real/250002?siteHost=www.playdoit.mx

    FLUJO CONFIRMADO 13-May-2026:
      - Este endpoint requiere sesión activa (JSESSIONID en cookies)
      - Retorna {"token": "G0QX4NDL...", "url": "https://sb2integration-altenar2..."}
      - El "token" es el que se pasa a WidgetAuth/SignIn

    FIX 25-May-2026: usar curl-cffi con TLS fingerprint Chrome para evitar
    403 de Cloudflare en este endpoint (igual que el login).
    """
    if proxies is None:
        proxies = _get_proxies_from_env()
    if proxies:
        session.proxies.update(proxies)

    url = f"{PLAYDOIT_BASE}/api/game/real/{PLAYDOIT_SPORTSBOOK_GAME_ID}"
    params = {"siteHost": PLAYDOIT_SITE_HOST}
    game_headers = {
        "Accept": "application/json",
        "Referer": f"{PLAYDOIT_BASE}/es/deportes",
        "User-Agent": _UA,
    }

    # Extraer JSESSIONID para pasarlo a curl-cffi
    jsid = session.cookies.get("JSESSIONID", "")
    cookies_dict = dict(session.cookies) if jsid else {}

    # Intento A: curl-cffi con TLS fingerprint (bypasea Cloudflare)
    try:
        from curl_cffi import requests as cf_requests
        for imp in ["chrome110", "chrome120"]:
            try:
                cf_resp = cf_requests.get(
                    url,
                    params=params,
                    headers=game_headers,
                    cookies=cookies_dict,
                    impersonate=imp,
                    proxies=proxies,
                    timeout=15,
                )
                logger.info(f"[API] game/real/{PLAYDOIT_SPORTSBOOK_GAME_ID} (curl-cffi/{imp}): HTTP {cf_resp.status_code}")
                if cf_resp.status_code == 200:
                    data = cf_resp.json() if cf_resp.content else {}
                    token = data.get("token")
                    if token and isinstance(token, str) and len(token) > 5:
                        logger.info(f"[API] ✅ Altenar game token OK (len={len(token)})")
                        return token
                    logger.warning(f"[API] game/real sin token: {str(data)[:200]}")
                    return None  # 200 pero sin token → no reintentar
                elif cf_resp.status_code in (403, 429):
                    continue  # probar otro impersonate
            except Exception as e:
                err_str = str(e)
                if "502" in err_str or "proxy" in err_str.lower():
                    logger.warning(f"[API] game/real proxy error ({imp}): {err_str[:80]}")
                    break
                logger.debug(f"[API] game/real curl-cffi/{imp}: {e}")
    except ImportError:
        pass

    # Intento B: requests.Session estándar (fallback)
    try:
        resp = session.get(url, params=params, headers=game_headers, timeout=12)
        logger.info(f"[API] game/real/{PLAYDOIT_SPORTSBOOK_GAME_ID} (requests): HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            token = data.get("token")
            if token and isinstance(token, str) and len(token) > 5:
                logger.info(f"[API] ✅ Altenar game token OK (len={len(token)})")
                return token
            logger.warning(f"[API] game/real sin token: {str(data)[:200]}")
        else:
            logger.warning(f"[API] game/real HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.error(f"[API] Error en game/real: {e}")
    return None


# ── Paso 3: Intercambiar token → accessToken Altenar ─────────────────────────

def _altenar_signin(game_token: str,
                    proxies: Optional[dict] = None) -> Optional[str]:
    if proxies is None:
        proxies = _get_proxies_from_env()
    """
    POST /api/WidgetAuth/SignIn en sb2auth-altenar2.
    Recibe el token de /api/game/real/250002 y devuelve el accessToken JWT.

    VERIFICADO 13-May-2026:
      - El campo de respuesta es "accessToken" (NO "customerToken")
      - accessToken es JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
      - Contiene: PersonId, UserName, CurrencyCode, Integration, exp, iss
      - Si currencyId=0 en respuesta → token incorrecto (no autorizado)
    """
    try:
        payload = {**BASE_PARAMS, "token": game_token}
        resp = requests.post(
            f"{ALTENAR_AUTH}/WidgetAuth/SignIn",
            json=payload,
            headers=_HEADERS,
            proxies=proxies,
            timeout=15,
        )
        logger.info(f"[API] Altenar SignIn: HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            # accessToken es el campo correcto (verificado en index-BeI_Gi8Q.js)
            access_token = (
                data.get("accessToken")
                or data.get("customerToken")   # fallback legacy
                or data.get("token")
                or (data.get("data") or {}).get("accessToken")
            )
            if access_token and len(str(access_token)) > 20:
                logger.info(f"[API] ✅ accessToken JWT OK (len={len(str(access_token))})")
                return str(access_token)
            else:
                cid = data.get("currencyId", -1) if isinstance(data, dict) else -1
                logger.warning(f"[API] Altenar SignIn sin accessToken. currencyId={cid} keys={list(data.keys()) if isinstance(data,dict) else '?'}")
                if cid == 0:
                    logger.warning("[API] currencyId=0 → game token no autenticó en Altenar")
        else:
            logger.warning(f"[API] Altenar SignIn HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"[API] Altenar SignIn error: {e}")
    return None


# ── Extraer balance de respuesta JSON ─────────────────────────────────────────

def _extract_balance(data: dict) -> Optional[float]:
    """Extrae el saldo de distintas estructuras JSON que devuelven los APIs."""
    if not isinstance(data, dict):
        return None
    for key_path in [
        ["balance"], ["Balance"], ["realBalance"], ["availableBalance"],
        ["user", "balance"], ["data", "balance"], ["wallet", "balance"],
        ["account", "balance"], ["player", "balance"],
        ["userDetails", "balance"], ["info", "balance"],
    ]:
        val = data
        for key in key_path:
            val = val.get(key) if isinstance(val, dict) else None
        if val is not None and isinstance(val, (int, float)) and float(val) >= 0:
            return float(val)
    return None


def get_balance(session: Optional[requests.Session] = None,
                token: Optional[str] = None,
                proxies: Optional[dict] = None) -> Optional[float]:
    """
    Obtiene el saldo REAL de la cuenta PlayDoit.
    Intenta primero la sesión PlayDoit (JSESSIONID), luego Altenar API (JWT).
    Retorna float en MXN, o None si no se puede obtener.
    """
    if proxies is None:
        proxies = _get_proxies_from_env()

    # Método 1: PlayDoit API con JSESSIONID (varios endpoints posibles)
    if session:
        if proxies:
            session.proxies.update(proxies)
        pd_headers = {
            "Accept": "application/json",
            "Referer": f"{PLAYDOIT_BASE}/es/deportes",
            "User-Agent": _UA,
        }
        for url in [
            f"{PLAYDOIT_BASE}/api/user",
            f"{PLAYDOIT_BASE}/api/account",
            f"{PLAYDOIT_BASE}/api/user/balance",
            f"{PLAYDOIT_BASE}/api/account/balance",
            f"{PLAYDOIT_BASE}/api/player/balance",
        ]:
            try:
                resp = session.get(url, headers=pd_headers, timeout=10)
                logger.debug(f"[Balance] {url}: HTTP {resp.status_code}")
                if resp.status_code == 200 and resp.content:
                    data = resp.json()
                    bal = _extract_balance(data)
                    if bal is not None:
                        logger.info(f"[Balance] ✅ PlayDoit balance: ${bal:.2f} MXN ({url})")
                        return bal
            except Exception as e:
                logger.debug(f"[Balance] {url} error: {e}")

    # Método 2: Altenar API con JWT
    if token:
        try:
            headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
            resp = requests.get(
                f"{ALTENAR_FRONTEND}/widget/GetBalance",
                params=BASE_PARAMS,
                headers=headers,
                proxies=proxies,
                timeout=10,
            )
            logger.debug(f"[Balance] Altenar GetBalance: HTTP {resp.status_code}")
            if resp.status_code == 200 and resp.content:
                data = resp.json()
                bal = _extract_balance(data)
                if bal is not None:
                    logger.info(f"[Balance] ✅ Altenar balance: ${bal:.2f} MXN")
                    return bal
                # A veces el saldo es el top-level number
                if isinstance(data, (int, float)):
                    logger.info(f"[Balance] ✅ Altenar balance (raw): ${data:.2f} MXN")
                    return float(data)
        except Exception as e:
            logger.debug(f"[Balance] Altenar GetBalance error: {e}")

    logger.warning("[Balance] ⚠️ No se pudo obtener saldo real de ningún endpoint")
    return None


# ── Login completo (3 pasos) ──────────────────────────────────────────────────

def api_login(username: str, password: str, proxies: Optional[dict] = None) -> Optional[str]:
    """
    Login completo vía API (sin browser, sin Playwright).

    FLUJO VERIFICADO (13-May-2026):
      1. POST /api/login (form-urlencoded, campo "login") → JSESSIONID
      2. GET  /api/game/real/250002?siteHost=www.playdoit.mx → game token
      3. POST /api/WidgetAuth/SignIn con game token → accessToken JWT

    Retorna el accessToken JWT listo para usar en place_bet_api().
    None si falla cualquier paso.
    """
    # Si no se pasó proxies explícito, leer de env (PROXY_URL)
    if proxies is None:
        proxies = _get_proxies_from_env()

    # Paso 1: Login
    session = _playdoit_login(username, password, proxies=proxies)
    if not session:
        logger.error("[API] ❌ Paso 1 falló: no se pudo hacer login en PlayDoit")
        return None

    # Paso 2: Game token
    game_token = _get_altenar_game_token(session, proxies=proxies)
    if not game_token:
        logger.error("[API] ❌ Paso 2 falló: no se obtuvo game token de /api/game/real/250002")
        return None

    # Paso 3: accessToken Altenar
    access_token = _altenar_signin(game_token, proxies=proxies)
    if not access_token:
        logger.error("[API] ❌ Paso 3 falló: WidgetAuth/SignIn no devolvió accessToken")
        return None

    logger.info(f"[API] ✅ Login completo OK. accessToken len={len(access_token)}")
    return access_token


def api_login_full(username: str, password: str,
                   proxies: Optional[dict] = None
                   ) -> tuple:
    """
    Login completo + devuelve (access_token, playdoit_session).
    La session sirve para llamar get_balance() y obtener el saldo real.
    Retorna (None, None) si falla cualquier paso.
    """
    if proxies is None:
        proxies = _get_proxies_from_env()

    session = _playdoit_login(username, password, proxies=proxies)
    if not session:
        logger.error("[API] ❌ api_login_full: paso 1 falló (PlayDoit login)")
        return None, None

    game_token = _get_altenar_game_token(session, proxies=proxies)
    if not game_token:
        logger.error("[API] ❌ api_login_full: paso 2 falló (game token)")
        return None, None

    access_token = _altenar_signin(game_token, proxies=proxies)
    if not access_token:
        logger.error("[API] ❌ api_login_full: paso 3 falló (SignIn)")
        return None, None

    logger.info(f"[API] ✅ api_login_full OK. accessToken len={len(access_token)}")
    return access_token, session


# ── Buscar selección Over/Under ────────────────────────────────────────────────

# Mapeo de market_type → typeIds de mercado en Altenar
# typeId=18: Total Goals (fútbol goles, NBA puntos)
# Corners y tarjetas usan otros typeIds — si no los conocemos, buscamos en TODOS los mercados.
_KNOWN_MARKET_TYPE_IDS = {
    "goles":    [18],
    "puntos":   [18],      # NBA total points: mismo typeId en muchos books
    "corners":  None,      # Desconocido — buscar en todos los mercados por nombre
    "tarjetas": None,      # Desconocido — buscar en todos los mercados por nombre
    "games":    None,      # Tenis de mesa — buscar en todos los mercados
}
# Palabras clave en nombre de mercado para corners/tarjetas/games
_MARKET_NAME_KEYWORDS = {
    "corners":  ["corner", "esquina", "tiro de esquina"],
    "tarjetas": ["tarjeta", "card", "booking", "amarilla"],
    "games":    ["game", "set", "juego"],
}


def find_total_selection_full(event_id: int, bet_type: str, line: float,
                              sport_id: int = 66,
                              market_type: str = "goles",
                              proxies: Optional[dict] = None) -> Optional[dict]:
    """
    Devuelve el dict completo con todos los campos para construir el payload de placeWidget.
    Retorna {market_id, odd_id, price, event_name, cat_name, champ_name, ...} o None.

    market_type: "goles" | "corners" | "tarjetas" | "puntos" | "games"
      - "goles"/"puntos": busca en mercados typeId=18 (Total)
      - "corners"/"tarjetas"/"games": busca en TODOS los mercados (typeId desconocido en Altenar)

    NOTE: GetEventDetails es API pública — no requiere geo-restricción.
    Si el proxy falla (502/tunnel), reintenta automáticamente sin proxy.
    """
    if proxies is None:
        proxies = _get_proxies_from_env()

    target_type = 12 if bet_type == "over" else 13
    line_str    = str(line)
    name_suffix = f" {line_str}"

    # Determinar si filtramos por typeId o buscamos en todos
    allowed_type_ids = _KNOWN_MARKET_TYPE_IDS.get(market_type, [18])  # default = goles
    market_keywords  = _MARKET_NAME_KEYWORDS.get(market_type, [])
    search_all_markets = allowed_type_ids is None

    # Intentar con proxy principal → port 443 → sin proxy
    proxy_attempts = _build_proxy_attempts(proxies)

    for attempt_proxies in proxy_attempts:
        try:
            resp = requests.get(
                f"{ALTENAR_FRONTEND}/widget/GetEventDetails",
                params={**BASE_PARAMS, "eventId": event_id},
                timeout=12, proxies=attempt_proxies,
            )
            resp.raise_for_status()
            data = resp.json()

            # Metadatos del evento
            event_name    = data.get("name", "")
            cat_name      = data.get("categoryName", "")
            champ_name    = data.get("champName", "")
            sport_name    = data.get("sportName", "Fútbol")
            sport_type_id = data.get("sportTypeId", 1)
            markets       = data.get("markets", [])
            odds_list     = data.get("odds", [])
            odds_by_id    = {o["id"]: o for o in odds_list}

            def _market_name_matches(mkt: dict) -> bool:
                """Para markets con typeId desconocido: verificar por nombre de mercado."""
                if not search_all_markets:
                    return mkt.get("typeId") in allowed_type_ids
                if not market_keywords:
                    return True  # Buscar en absolutamente todos
                mkt_name = str(mkt.get("name", "") or mkt.get("typeName", "")).lower()
                return any(kw in mkt_name for kw in market_keywords)

            # Búsqueda principal: recorrer markets según market_type
            for market in markets:
                if not _market_name_matches(market):
                    continue
                market_id       = market.get("id")
                market_name_str = str(market.get("name", "") or market.get("typeName", "Total"))
                market_type_id  = market.get("typeId", 18)
                desktop_odd_ids = market.get("desktopOddIds") or []
                all_odd_ids = []
                for row in desktop_odd_ids:
                    if isinstance(row, list):
                        all_odd_ids.extend(row)
                    elif isinstance(row, int):
                        all_odd_ids.append(row)
                for odd_id in all_odd_ids:
                    odd = odds_by_id.get(odd_id, {})
                    if odd.get("typeId") != target_type:
                        continue
                    odd_name = str(odd.get("name", ""))
                    if odd_name.endswith(name_suffix):
                        price = odd.get("price", 1.0)
                        logger.info(f"[API] Found full ({market_type}): {bet_type} {line} | mktId={market_id} oddId={odd_id} price={price}")
                        return {
                            "market_id": market_id, "odd_id": odd_id, "price": price,
                            "event_name": event_name, "cat_name": cat_name,
                            "champ_name": champ_name, "sport_name": sport_name,
                            "sport_type_id": sport_type_id, "db_id": 10,
                            "s_pov": line_str, "market_name": market_name_str,
                            "market_type_id": market_type_id,
                            "selection_type_id": target_type, "selection_name": odd_name,
                        }

            # Fallback: buscar en todos los odds directamente (cualquier market)
            for odd in odds_list:
                if odd.get("typeId") != target_type:
                    continue
                if str(odd.get("name", "")).endswith(name_suffix):
                    odd_id = odd.get("id")
                    price  = odd.get("price", 1.0)
                    # Encontrar el market que contiene este odd
                    for market in markets:
                        desktop = market.get("desktopOddIds") or []
                        flat = []
                        for row in desktop:
                            if isinstance(row, list): flat.extend(row)
                            elif isinstance(row, int): flat.append(row)
                        if odd_id in flat:
                            mkt_name_str = str(market.get("name", "") or market.get("typeName", "Total"))
                            logger.info(f"[API] (fallback full/{market_type}) {bet_type} {line}: oddId={odd_id}")
                            return {
                                "market_id": market.get("id"), "odd_id": odd_id, "price": price,
                                "event_name": event_name, "cat_name": cat_name,
                                "champ_name": champ_name, "sport_name": sport_name,
                                "sport_type_id": sport_type_id, "db_id": 10,
                                "s_pov": line_str, "market_name": mkt_name_str,
                                "market_type_id": market.get("typeId", 18),
                            }

            logger.warning(f"[API] No se encontró {market_type} {bet_type} {line} para eventId={event_id}")
            if search_all_markets:
                # Log todos los markets disponibles para diagnóstico
                avail_mkts = [(m.get("typeId"), m.get("name") or m.get("typeName")) for m in markets]
                logger.debug(f"[API] Markets disponibles: {avail_mkts[:15]}")
            return None  # Datos obtenidos pero sin odds — no tiene sentido reintentar

        except requests.exceptions.ProxyError as e:
            if attempt_proxies is None:
                logger.error(f"[API] GetEventDetails falló sin proxy: {e}")
                break
            logger.warning(f"[API] Proxy 502 en GetEventDetails eventId={event_id} — reintentando sin proxy...")
            continue
        except Exception as e:
            logger.error(f"[API] Error en find_total_selection_full: {e}")
            break

    return None


def find_total_selection(event_id: int, bet_type: str, line: float,
                          proxies: Optional[dict] = None) -> Optional[Tuple[int, int, float]]:
    """
    Busca el oddId y marketId para Over/Under goles en un evento de Altenar.
    Retorna (marketId, oddId, price) o None si no encontrado.

    bet_type: 'over' | 'under'
    line: 2.5, 3.5, etc.

    ESTRUCTURA de GetEventDetails:
      - markets[typeId=18]: mercado "Total" de goles
        - desktopOddIds: array 2D [[over_odds...], [under_odds...]]
        - oddIds: vacío (no usar)
      - odds[typeId=12]: Over ("Más de X.X")
        - odds[typeId=13]: Under ("Menos de X.X")
        - marketId en odds es None — usar desktopOddIds del market

    Busca el odd correcto por:
      1. typeId=12 (over) o typeId=13 (under)
      2. name termina en " {line}" (ej: "Más de 2.5")
    """
    target_type = 12 if bet_type == "over" else 13
    line_str    = str(line)
    # El nombre del odd termina con la línea: "Más de 2.5" o "Menos de 2.5"
    name_suffix = f" {line_str}"

    try:
        resp = requests.get(
            f"{ALTENAR_FRONTEND}/widget/GetEventDetails",
            params={**BASE_PARAMS, "eventId": event_id},
            timeout=12, proxies=proxies,
        )
        resp.raise_for_status()
        data      = resp.json()
        markets   = data.get("markets", [])
        odds_list = data.get("odds", [])
        odds_by_id = {o["id"]: o for o in odds_list}

        # Paso 1: Encontrar el market typeId=18 que contiene el odd buscado
        for market in markets:
            if market.get("typeId") != 18:
                continue
            market_id = market.get("id")

            # desktopOddIds es 2D: [[over_odd_ids...], [under_odd_ids...]]
            desktop_odd_ids = market.get("desktopOddIds") or []
            # Aplanar todos los IDs del mercado
            all_odd_ids = []
            for row in desktop_odd_ids:
                if isinstance(row, list):
                    all_odd_ids.extend(row)
                elif isinstance(row, int):
                    all_odd_ids.append(row)

            for odd_id in all_odd_ids:
                odd = odds_by_id.get(odd_id, {})
                if odd.get("typeId") != target_type:
                    continue
                odd_name = str(odd.get("name", ""))
                # Verificar que el nombre termina con la línea buscada
                # Ej: "Más de 2.5" → endswith(" 2.5")
                if odd_name.endswith(name_suffix):
                    price = odd.get("price", 1.0)
                    logger.info(f"[API] Found {bet_type} {line}: marketId={market_id} oddId={odd_id} price={price} name={odd_name!r}")
                    return market_id, odd_id, price

        # Paso 2: Si no encontrado via desktopOddIds, buscar directamente en todos los odds
        # (fallback más amplio — puede retornar odd de un market diferente)
        logger.debug(f"[API] Buscando via fallback directo en todos los odds...")
        for odd in odds_list:
            if odd.get("typeId") != target_type:
                continue
            odd_name = str(odd.get("name", ""))
            if odd_name.endswith(name_suffix):
                # Encontrar el market que contiene este odd
                odd_id = odd.get("id")
                for market in markets:
                    if market.get("typeId") != 18:
                        continue
                    desktop_odd_ids = market.get("desktopOddIds") or []
                    all_market_odds = []
                    for row in desktop_odd_ids:
                        if isinstance(row, list):
                            all_market_odds.extend(row)
                        elif isinstance(row, int):
                            all_market_odds.append(row)
                    if odd_id in all_market_odds:
                        price = odd.get("price", 1.0)
                        market_id = market.get("id")
                        logger.info(f"[API] (fallback) Found {bet_type} {line}: marketId={market_id} oddId={odd_id} price={price}")
                        return market_id, odd_id, price

        logger.warning(f"[API] No se encontró {bet_type} {line} para eventId={event_id}")
        # Mostrar líneas disponibles
        avail = [(o.get("name"), o.get("price")) for o in odds_list
                 if o.get("typeId") in (12, 13)][:20]
        logger.debug(f"[API] Odds Over/Under disponibles: {avail}")
    except Exception as e:
        logger.error(f"[API] Error buscando selección: {e}")
    return None


def list_available_lines(event_id: int, proxies: Optional[dict] = None) -> List[dict]:
    """
    Lista todas las líneas de Total (Over/Under) disponibles para un evento.
    Útil para diagnóstico: ver qué líneas (2.5, 3.5, 4.5, etc.) están disponibles.
    """
    try:
        resp = requests.get(
            f"{ALTENAR_FRONTEND}/widget/GetEventDetails",
            params={**BASE_PARAMS, "eventId": event_id},
            timeout=12, proxies=proxies,
        )
        resp.raise_for_status()
        data      = resp.json()
        markets   = data.get("markets", [])
        odds_list = data.get("odds", [])
        odds_by_id = {o["id"]: o for o in odds_list}

        # Construir índice odd_id → market_id via desktopOddIds
        odd_to_market = {}
        for market in markets:
            if market.get("typeId") != 18:
                continue
            mid = market.get("id")
            for row in (market.get("desktopOddIds") or []):
                if isinstance(row, list):
                    for oid in row:
                        odd_to_market[oid] = mid
                elif isinstance(row, int):
                    odd_to_market[row] = mid

        lines = []
        for odd in odds_list:
            t = odd.get("typeId")
            if t not in (12, 13):
                continue
            name  = str(odd.get("name", ""))
            side  = "over" if t == 12 else "under"
            oid   = odd.get("id")
            mid   = odd_to_market.get(oid)
            price = odd.get("price")
            lines.append({
                "side": side, "name": name, "price": price,
                "oddId": oid, "marketId": mid,
            })
        return sorted(lines, key=lambda x: (x.get("name",""), x.get("side","")))
    except Exception as e:
        logger.error(f"[API] Error listando líneas: {e}")
        return []


# ── Helper: auto-fetch metadata del evento ────────────────────────────────────

def _auto_fetch_sel_meta(event_id: int, odd_id: int, proxies: Optional[dict] = None) -> dict:
    """
    Obtiene metadata del evento y selección desde GetEventDetails.
    Usado por place_bet_api y place_parlay_api cuando no se provee sel_meta.

    Campos rellenados:
      event_name, cat_name, champ_name, sport_name, sport_type_id,
      market_name, market_type_id, selection_name, selection_type_id, s_pov, db_id
    """
    try:
        if proxies is None:
            proxies = _get_proxies_from_env()
        # Intentar con proxy y sin proxy como fallback
        proxy_attempts = _build_proxy_attempts(proxies)
        for attempt_proxies in proxy_attempts:
            try:
                resp = requests.get(
                    f"{ALTENAR_FRONTEND}/widget/GetEventDetails",
                    params={**BASE_PARAMS, "eventId": event_id},
                    timeout=12, proxies=attempt_proxies,
                )
                if not resp.ok:
                    continue
                data = resp.json()
                break
            except Exception:
                continue
        else:
            logger.warning(f"[API] _auto_fetch_sel_meta: no se pudo obtener GetEventDetails para eventId={event_id}")
            return {}

        meta = {
            "event_name":       data.get("name", ""),
            "cat_name":         data.get("categoryName", ""),
            "champ_name":       data.get("champName", ""),
            "sport_name":       data.get("sportName", "Fútbol"),
            "sport_type_id":    data.get("sportTypeId", 1),
            "db_id":            10,
            "s_pov":            "2.5",
            "market_name":      "Total",
            "market_type_id":   18,
            "selection_type_id": 12,
            "selection_name":   "Más de 2.5",
        }
        # Buscar el odd específico para selectionName y selectionTypeId correctos
        odds_list  = data.get("odds", [])
        markets    = data.get("markets", [])
        for odd in odds_list:
            if odd.get("id") == odd_id:
                meta["selection_name"]     = odd.get("name", meta["selection_name"])
                meta["selection_type_id"]  = odd.get("typeId", meta["selection_type_id"])
                # Buscar el market que contiene este odd
                for market in markets:
                    desktop = market.get("desktopOddIds") or []
                    flat = []
                    for row in desktop:
                        if isinstance(row, list):  flat.extend(row)
                        elif isinstance(row, int): flat.append(row)
                    if odd_id in flat:
                        meta["market_name"]    = str(market.get("name", "") or market.get("typeName", "Total"))
                        meta["market_type_id"] = market.get("typeId", 18)
                        break
                break
        logger.debug(
            f"[API] auto-meta eventId={event_id} oddId={odd_id}: "
            f"event={meta['event_name']!r} cat={meta['cat_name']!r} "
            f"champ={meta['champ_name']!r} sel={meta['selection_name']!r}"
        )
        return meta
    except Exception as e:
        logger.warning(f"[API] _auto_fetch_sel_meta error eventId={event_id}: {e}")
        return {}


# ── Colocar apuesta ───────────────────────────────────────────────────────────

def place_bet_api(token: str, event_id: int, market_id: int, odd_id: int,
                   price: float, stake: float,
                   sel_meta: Optional[dict] = None,
                   proxies: Optional[dict] = None) -> Dict:
    """
    Coloca una apuesta SINGLE via API de Altenar.

    PAYLOAD CORRECTO — descubierto via interceptor Playwright 14-May-2026:
      - betType: 0 (single) — NO "type":1 como antes
      - stakes: [stake] (array) — NO "stake":scalar
      - isAutoCharge: false, oddsChangeAction: 0
      - betMarkets: [{id, isBanker, dbId, sportName, rC, eventName, catName,
                      champName, sportTypeId, odds:[{id, sPOV, marketId, price,
                      marketName, marketTypeId}]}]
      - Authorization: Bearer {token} en HEADER (no solo en payload)

    sel_meta: dict de find_total_selection_full() con event/market metadata.
    Si no se provee, usa valores por defecto para soccer.
    """
    import secrets, string
    if proxies is None:
        proxies = _get_proxies_from_env()
    # Auto-fetch event metadata si no se proveyó sel_meta.
    # Altenar betgateway rechaza (errorType=11) cuando catName/champName/eventName son vacíos.
    if not sel_meta:
        sel_meta = _auto_fetch_sel_meta(event_id, odd_id, proxies)
    meta = sel_meta or {}
    bet_market = {
        "id":          event_id,
        "isBanker":    False,
        "dbId":        meta.get("db_id", 10),
        "sportName":   meta.get("sport_name", "Fútbol"),
        "rC":          False,
        "eventName":   meta.get("event_name", ""),
        "catName":     meta.get("cat_name", ""),
        "champName":   meta.get("champ_name", ""),
        "sportTypeId": meta.get("sport_type_id", 1),
        "odds": [{
            "id":              odd_id,
            "sPOV":            meta.get("s_pov", "2.5"),
            "marketId":        market_id,
            "price":           price,
            "marketName":      meta.get("market_name", "Total"),
            "marketTypeId":    meta.get("market_type_id", 18),
            "mostBalanced":    False,
            "selectionTypeId": meta.get("selection_type_id", 12),
            "selectionName":   meta.get("selection_name", "Más de 2.5"),
            "widgetInfo": {
                "widget":         12,
                "page":            3,
                "tabIndex":        3,
                "tipsterId":       None,
                "suggestionType":  None,
            },
        }],
    }
    # Generar requestId aleatorio (formato AltenarPB observado: 21 chars base64-like)
    req_id = "".join(secrets.choice(string.ascii_letters + string.digits + "-_") for _ in range(21))
    payload = {
        **BASE_PARAMS,
        "betType":           0,          # 0 = single
        "isAutoCharge":      False,
        "stakes":            [stake],
        "oddsChangeAction":  2,          # 2 = aceptar cualquier cambio de cuota (fix errorType=6)
        "betMarkets":        [bet_market],
        "eachWays":          [False],
        "requestId":         req_id,
        "confirmedByClient": True,       # confirmar al servidor que aceptamos cambios
        "device":            0,
    }

    # Header correcto: Authorization Bearer (NO customerToken en payload)
    headers = {**_HEADERS, "Authorization": f"Bearer {token}"}

    logger.info(f"[API] Single bet: eventId={event_id} mktId={market_id} oddId={odd_id} price={price} stake={stake}")
    try:
        resp = requests.post(
            ALTENAR_PLACE_BET_URL,
            json=payload,
            headers=headers,
            proxies=proxies,
            timeout=20,
        )
        logger.info(f"[API] Bet HTTP {resp.status_code}")
        data = resp.json() if resp.content else {}
        logger.info(f"[API] Bet response: {str(data)[:300]}")
        # Altenar devuelve HTTP 200 incluso para errores de negocio
        # (saldo insuficiente, cuota cambió, etc.) — detectarlos aquí
        has_error = isinstance(data, dict) and "error" in data
        success = (resp.status_code in (200, 201)) and not has_error
        return {
            "http_status": resp.status_code,
            "response": data,
            "success": success,
        }
    except Exception as e:
        logger.error(f"[API] Error placing bet: {e}")
        return {"http_status": 0, "response": {}, "success": False, "error": str(e)}


# ── Colocar parlay (acumulada) vía API ────────────────────────────────────────

def place_parlay_api(token: str, picks: List[dict], stake: float,
                     proxies: Optional[dict] = None) -> Dict:
    """
    Coloca un parlay / acumulada vía API de Altenar.

    picks: lista de {event_id, market_id, odd_id, price, [meta: dict]}
           meta = dict de find_total_selection_full() con event/market metadata.
           Si no se provee meta, usa defaults para soccer.
    Retorna dict con {http_status, response, success}.

    PAYLOAD CORRECTO — mismo formato que singles (descubierto 14-May-2026):
      - betType: 1 (accumulator/parlay)
      - stakes: [stake] (array, no scalar)
      - betMarkets: [...] un entry por pierna, con metadata completa del evento
      - Authorization: Bearer {token} en HEADER
      - Endpoint: placeWidget (no /v1/bets)
    """
    import secrets, string
    if proxies is None:
        proxies = _get_proxies_from_env()
    try:
        bet_markets = []
        total_price = 1.0

        for p in picks:
            total_price *= float(p.get("price", 1.0))
            # Auto-fetch metadata si falta: catName/champName/eventName vacíos → errorType=11
            meta = p.get("meta") or p.get("sel_meta") or {}
            event_id  = p["event_id"]
            market_id = p["market_id"]
            odd_id    = p["odd_id"]
            price     = p["price"]
            if not meta:
                meta = _auto_fetch_sel_meta(event_id, odd_id, proxies)

            bet_market = {
                "id":          event_id,
                "isBanker":    False,
                "dbId":        meta.get("db_id", 10),
                "sportName":   meta.get("sport_name", "Fútbol"),
                "rC":          False,
                "eventName":   meta.get("event_name", ""),
                "catName":     meta.get("cat_name", ""),
                "champName":   meta.get("champ_name", ""),
                "sportTypeId": meta.get("sport_type_id", 1),
                "odds": [{
                    "id":              odd_id,
                    "sPOV":            meta.get("s_pov", "2.5"),
                    "marketId":        market_id,
                    "price":           price,
                    "marketName":      meta.get("market_name", "Total"),
                    "marketTypeId":    meta.get("market_type_id", 18),
                    "mostBalanced":    False,
                    "selectionTypeId": meta.get("selection_type_id", 12),
                    "selectionName":   meta.get("selection_name", "Más de 2.5"),
                    "widgetInfo": {
                        "widget":         12,
                        "page":            3,
                        "tabIndex":        3,
                        "tipsterId":       None,
                        "suggestionType":  None,
                    },
                }],
            }
            bet_markets.append(bet_market)

        req_id = "".join(secrets.choice(string.ascii_letters + string.digits + "-_") for _ in range(21))
        payload = {
            **BASE_PARAMS,
            "betType":           1,         # 1 = accumulator/parlay
            "isAutoCharge":      False,
            "stakes":            [stake],
            "oddsChangeAction":  2,          # 2 = aceptar cualquier cambio de cuota (fix errorType=6)
            "betMarkets":        bet_markets,
            "eachWays":          [False],
            "requestId":         req_id,
            "confirmedByClient": True,       # confirmar al servidor que aceptamos cambios
            "device":            0,
        }

        # Header correcto: Authorization Bearer (igual que singles)
        headers = {**_HEADERS, "Authorization": f"Bearer {token}"}

        odd_ids = [p["odd_id"] for p in picks]
        logger.info(f"[API] Parlay {len(picks)} piernas: oddIds={odd_ids} acum={total_price:.3f} stake={stake}")

        resp = requests.post(
            ALTENAR_PLACE_BET_URL,
            json=payload,
            headers=headers,
            proxies=proxies,
            timeout=20,
        )
        logger.info(f"[API] Parlay HTTP {resp.status_code}")
        data = resp.json() if resp.content else {}
        logger.info(f"[API] Parlay response: {str(data)[:300]}")
        has_error = isinstance(data, dict) and "error" in data
        success = (resp.status_code in (200, 201)) and not has_error
        return {
            "http_status": resp.status_code,
            "response": data,
            "success": success,
        }
    except Exception as e:
        logger.error(f"[API] Error placing parlay: {e}")
        return {"http_status": 0, "response": {}, "success": False, "error": str(e)}


# ── Función de conveniencia: login + buscar + apostar ─────────────────────────

def place_total_bet(username: str, password: str,
                    event_id: int, bet_type: str, line: float, stake: float,
                    proxies: Optional[dict] = None) -> Dict:
    """
    Flujo completo de apuesta Over/Under sin Playwright.

    Pasos:
      1. api_login() → accessToken
      2. find_total_selection() → (marketId, oddId, price)
      3. place_bet_api() → resultado

    Retorna:
      {"ok": bool, "message": str, "bet_id": int|None, "win": float|None,
       "odd": float, "bet_api_response": dict}
    """
    out = {"ok": False, "message": "", "bet_id": None, "win": None,
           "odd": None, "bet_api_response": None}

    # Paso 1: Login
    token = api_login(username, password, proxies=proxies)
    if not token:
        out["message"] = "Login fallido en PlayDoit API"
        return out

    # Paso 2: Buscar selección
    sel = find_total_selection(event_id, bet_type, line, proxies=proxies)
    if not sel:
        out["message"] = f"Selección {bet_type} {line} no encontrada para evento {event_id}"
        return out
    market_id, odd_id, price = sel
    out["odd"] = price

    # Paso 3: Apostar
    r = place_bet_api(token, event_id, market_id, odd_id, price, stake, proxies=proxies)
    out["bet_api_response"] = r.get("response")

    data = r.get("response", {})
    bets = (data.get("data") or {}).get("bets", []) if isinstance(data, dict) else []
    if bets:
        out["bet_id"] = bets[0].get("id")
        out["win"]    = bets[0].get("totalWin")

    if r.get("success") and bets:
        out["ok"]      = True
        out["message"] = f"Apuesta API OK: {bet_type} {line} @ {price}"
    elif r.get("success"):
        out["ok"]      = True
        out["message"] = f"Apuesta API enviada (HTTP {r['http_status']}, sin bets en respuesta)"
    else:
        out["message"] = f"API error HTTP {r.get('http_status')}: {str(data)[:200]}"

    return out
