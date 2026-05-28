"""
event_catalog.py — Lee partidos disponibles en Altenar con sus mercados O/U reales.

FLUJO CORRECTO:
  1. GetUpcoming → lista de partidos próximos por deporte
  2. GetEventDetails → mercados y cuotas reales de cada partido
  3. Devuelve catálogo listo para pasar a OpenAI

El bot solo analiza y apuesta lo que PlayDoit tiene disponible.
"""
import requests
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

log = logging.getLogger(__name__)

API_BASE   = "https://sb2frontend-altenar2.biahosted.com/api/widget"
API_PARAMS = {
    "culture": "es-ES", "timezoneOffset": "360",
    "integration": "playdoit2", "deviceType": "1",
    "numFormat": "en-GB", "countryCode": "MX",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.playdoit.mx/",
    "Origin":  "https://www.playdoit.mx",
}

# Sport IDs en Altenar (verificados)
SPORTS = {
    "futbol":     66,
    "basquetbol": 67,   # NBA
    "hockey":     70,   # NHL + IIHF Mundial 2026
    "tenis_mesa": 77,   # Tenis de mesa (eliminado del análisis — sin datos)
}


def _get_upcoming(sport_id: int, hours_ahead: int = 52) -> List[dict]:
    """
    Llama GetUpcoming para un deporte y filtra eventos en las próximas N horas.
    Retorna lista de eventos con campos básicos: id, name, startDate, champName.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)
    events = []

    for page in range(1, 5):   # máximo 4 páginas (400 eventos)
        try:
            r = requests.get(
                f"{API_BASE}/GetUpcoming",
                params={**API_PARAMS, "eventCount": "100", "sportId": str(sport_id), "page": str(page)},
                headers=HEADERS, timeout=20,
            )
            r.raise_for_status()
            data       = r.json() or {}
            page_evs   = data.get("events", [])
            page_count = int(data.get("pageCount", 1))

            for ev in page_evs:
                start_str = ev.get("startDate", "")
                try:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if start_dt < now or start_dt > cutoff:
                        continue   # ya empezó o muy lejos
                except Exception:
                    pass           # sin fecha → incluir de todos modos
                events.append(ev)

            if page >= page_count:
                break
        except Exception as e:
            log.warning(f"[Catalog] GetUpcoming page={page} sportId={sport_id}: {e}")
            break

    return events


def _get_ou_markets(event_id: int) -> tuple[List[dict], dict]:
    """
    Llama GetEventDetails y extrae mercados O/U + metadata del evento.
    Retorna (markets_list, meta_dict):
      markets_list: [{market, type, line, cuota, market_id, odd_id, market_type_id}, ...]
      meta_dict:    {"champ_name": str, "category": str, "sport_name": str}
    """
    try:
        r = requests.get(
            f"{API_BASE}/GetEventDetails",
            params={**API_PARAMS, "eventId": event_id},
            headers=HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json() or {}

        # Extraer metadata: champ.name = liga real (ej. "Bundesliga", "Premier League")
        meta = {
            "champ_name":  (data.get("champ") or {}).get("name", ""),
            "category":    (data.get("category") or {}).get("name", ""),
            "sport_name":  (data.get("sport") or {}).get("name", ""),
        }

        markets    = data.get("markets", [])
        odds_list  = data.get("odds", [])
        odds_by_id = {o["id"]: o for o in odds_list}

        # Mapear odd_id → market
        odd_to_market  = {}
        market_info    = {}
        for mkt in markets:
            mid = mkt.get("id")
            mkt_name = (mkt.get("name") or mkt.get("typeName") or "").lower()
            market_info[mid] = {"typeId": mkt.get("typeId"), "name": mkt_name}
            for row in (mkt.get("desktopOddIds") or []):
                for oid in (row if isinstance(row, list) else [row]):
                    odd_to_market[oid] = mid

        result = []
        seen   = set()

        for odd in odds_list:
            otype = odd.get("typeId")
            if otype not in (12, 13):   # 12=over, 13=under
                continue
            odd_id   = odd.get("id")
            odd_name = str(odd.get("name", ""))
            price    = float(odd.get("price", 0) or 0)
            if price <= 1.01:
                continue   # cuota inválida

            market_id = odd_to_market.get(odd_id)
            if not market_id:
                continue

            mkt       = market_info.get(market_id, {})
            mkt_tid   = mkt.get("typeId")
            mkt_name  = mkt.get("name", "")

            # Extraer línea del nombre del odd ("Más de 2.5" → 2.5)
            try:
                line = float(odd_name.split()[-1])
            except Exception:
                continue

            # Clasificar mercado
            if mkt_tid == 18 or ("total" in mkt_name and not any(
                    k in mkt_name for k in ["corner", "esquina", "tarjeta", "card"])):
                market = "goles" if line < 15 else "puntos"
            elif any(k in mkt_name for k in ["corner", "esquina", "tiro de esquina"]):
                market = "corners"
            elif any(k in mkt_name for k in ["tarjeta", "card", "booking"]):
                market = "tarjetas"
            else:
                # Inferir por valor de línea
                if line >= 100:
                    market = "puntos"    # NBA
                elif line >= 7.5:
                    market = "corners"
                else:
                    market = "goles"

            bet_type = "over" if otype == 12 else "under"
            key = (market, bet_type, line)
            if key in seen:
                continue
            seen.add(key)

            result.append({
                "market":        market,
                "type":          bet_type,
                "line":          line,
                "cuota":         round(price, 2),
                "market_id":     market_id,
                "odd_id":        odd_id,
                "market_type_id": mkt_tid,
            })

        return sorted(result, key=lambda x: (x["market"], x["line"], x["type"])), meta

    except Exception as e:
        log.warning(f"[Catalog] GetEventDetails eventId={event_id}: {e}")
        return [], {"champ_name": "", "category": "", "sport_name": ""}


def get_upcoming_catalog(hours_ahead: int = 52,
                         max_soccer: int = 40,
                         max_nba: int = 15,
                         max_hockey: int = 15) -> List[dict]:
    """
    Catálogo completo de partidos disponibles en PlayDoit con mercados O/U reales.

    Args:
        hours_ahead: ventana de tiempo (default 52h = cubre el día de sesión y el siguiente)
        max_soccer:  máximo de partidos de fútbol a procesar
        max_nba:     máximo de partidos de NBA a procesar

    Returns: lista de dicts con:
        {event_id, partido, home_team, away_team, league, sport,
         fecha_utc, mercados:[{market, type, line, cuota, market_id, odd_id}]}
    """
    log.info(f"[Catalog] Leyendo catálogo PlayDoit (próximas {hours_ahead}h)...")
    result = []

    # ── Fútbol soccer ────────────────────────────────────────────────────────
    soccer_raw = _get_upcoming(SPORTS["futbol"], hours_ahead)
    log.info(f"[Catalog] Fútbol: {len(soccer_raw)} eventos encontrados")

    for ev in soccer_raw[:max_soccer]:
        event_id = ev.get("id")
        if not event_id:
            continue
        markets, meta = _get_ou_markets(event_id)
        # Solo incluir si tiene mercado de goles
        if not any(m["market"] == "goles" for m in markets):
            continue
        name  = ev.get("name", "")
        parts = [p.strip() for p in name.split(" vs. ")]
        result.append({
            "event_id":  event_id,
            "partido":   name,
            "home_team": parts[0] if parts else name,
            "away_team": parts[1] if len(parts) > 1 else "",
            "league":    meta.get("champ_name", ""),   # FIX: viene de GetEventDetails
            "category":  meta.get("category", ""),     # País (ej. "Alemania", "Inglaterra")
            "sport":     "futbol",
            "fecha_utc": ev.get("startDate", ""),
            "mercados":  markets,
        })

    log.info(f"[Catalog] Fútbol con mercados O/U: {sum(1 for r in result if r['sport']=='futbol')}")

    # ── NBA ──────────────────────────────────────────────────────────────────
    nba_raw = _get_upcoming(SPORTS["basquetbol"], hours_ahead)
    log.info(f"[Catalog] NBA: {len(nba_raw)} eventos encontrados")

    for ev in nba_raw[:max_nba]:
        event_id = ev.get("id")
        if not event_id:
            continue
        markets, meta = _get_ou_markets(event_id)
        if not any(m["market"] == "puntos" for m in markets):
            continue
        name  = ev.get("name", "")
        parts = [p.strip() for p in name.split(" vs. ")]
        result.append({
            "event_id":  event_id,
            "partido":   name,
            "home_team": parts[0] if parts else name,
            "away_team": parts[1] if len(parts) > 1 else "",
            "league":    meta.get("champ_name", "NBA"),
            "category":  meta.get("category", ""),
            "sport":     "basquetbol",
            "fecha_utc": ev.get("startDate", ""),
            "mercados":  markets,
        })

    # ── Hockey (NHL + IIHF Mundial) ──────────────────────────────────────────
    hockey_raw = _get_upcoming(SPORTS["hockey"], hours_ahead)
    log.info(f"[Catalog] Hockey: {len(hockey_raw)} eventos encontrados")

    for ev in hockey_raw[:max_hockey]:
        event_id = ev.get("id")
        if not event_id:
            continue
        markets, meta = _get_ou_markets(event_id)
        # Hockey usa market="goles" (línea ~5.5)
        if not any(m["market"] == "goles" for m in markets):
            continue
        name  = ev.get("name", "")
        # Hockey usa " @ " también (AHL/NHL: "Chicago Wolves @ Colorado Eagles")
        if " @ " in name:
            parts = [p.strip() for p in name.split(" @ ")]
            # En convención hockey norteamericana: "Away @ Home" → invertir
            parts = [parts[1], parts[0]] if len(parts) > 1 else parts
        else:
            parts = [p.strip() for p in name.split(" vs. ")]
        result.append({
            "event_id":  event_id,
            "partido":   name,
            "home_team": parts[0] if parts else name,
            "away_team": parts[1] if len(parts) > 1 else "",
            "league":    meta.get("champ_name", "Hockey"),
            "category":  meta.get("category", ""),
            "sport":     "hockey",
            "fecha_utc": ev.get("startDate", ""),
            "mercados":  markets,
        })

    log.info(f"[Catalog] TOTAL eventos con mercados O/U: {len(result)}")
    return result


def format_catalog_for_ai(catalog: List[dict]) -> str:
    """
    Formatea el catálogo para incluirlo en el prompt de OpenAI.
    Incluye event_id, partido, cuotas y mercados disponibles.
    """
    if not catalog:
        return "(Sin partidos disponibles en PlayDoit)"

    lines = [f"CATÁLOGO PLAYDOIT — {len(catalog)} PARTIDOS DISPONIBLES CON MERCADOS O/U:\n"]

    soccer  = [e for e in catalog if e["sport"] == "futbol"]
    nba     = [e for e in catalog if e["sport"] == "basquetbol"]

    if soccer:
        lines.append(f"── FÚTBOL ({len(soccer)} partidos) ──")
        for ev in soccer:
            fecha = ev["fecha_utc"][:16].replace("T", " ") if ev["fecha_utc"] else "?"
            lines.append(f"\nevent_id={ev['event_id']} | {ev['partido']} | {ev['league']} | {fecha} UTC")
            # Agrupar mercados por tipo
            goles   = [m for m in ev["mercados"] if m["market"] == "goles"   and m["type"] == "over"]
            corners = [m for m in ev["mercados"] if m["market"] == "corners"  and m["type"] == "over"]
            tarjetas= [m for m in ev["mercados"] if m["market"] == "tarjetas" and m["type"] == "over"]
            if goles:
                mkt_str = " | ".join(f"O/U {m['line']} Goles Over={m['cuota']} odd_id={m['odd_id']} mkt_id={m['market_id']}" for m in goles)
                lines.append(f"  Goles: {mkt_str}")
            if corners:
                mkt_str = " | ".join(f"O/U {m['line']} Corners Over={m['cuota']} odd_id={m['odd_id']} mkt_id={m['market_id']}" for m in corners)
                lines.append(f"  Corners: {mkt_str}")
            if tarjetas:
                mkt_str = " | ".join(f"O/U {m['line']} Tarjetas Over={m['cuota']} odd_id={m['odd_id']} mkt_id={m['market_id']}" for m in tarjetas)
                lines.append(f"  Tarjetas: {mkt_str}")

    if nba:
        lines.append(f"\n── NBA ({len(nba)} partidos) ──")
        for ev in nba:
            fecha = ev["fecha_utc"][:16].replace("T", " ") if ev["fecha_utc"] else "?"
            lines.append(f"\nevent_id={ev['event_id']} | {ev['partido']} | {fecha} UTC")
            puntos = [m for m in ev["mercados"] if m["market"] == "puntos" and m["type"] == "over"]
            if puntos:
                mkt_str = " | ".join(f"O/U {m['line']} Pts Over={m['cuota']} odd_id={m['odd_id']} mkt_id={m['market_id']}" for m in puntos)
                lines.append(f"  Puntos: {mkt_str}")

    return "\n".join(lines)
