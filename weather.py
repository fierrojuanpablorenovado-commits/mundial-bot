"""
weather.py — Cliente Open-Meteo (gratis, sin API key)

Devuelve clima para la hora exacta del kickoff por sede del Mundial.
API: https://open-meteo.com/

Variables que extrae:
  - temp_c: temperatura (°C)
  - precip_pct: probabilidad de precipitación (%)
  - wind_kmh: velocidad de viento
  - humidity_pct: humedad relativa
  - condition: derivado (hot/rain/cold/normal)

Reglas que aplican al modelo:
  - precip_pct > 60% → goals_factor *= 0.92  (lluvia → menos goles)
  - temp_c > 33 + humidity > 60 → fatigue *= 1.15 (calor brutal Miami/Houston)
  - wind_kmh > 30 → corners +5% (más balones largos desviados)
"""
from __future__ import annotations

import time
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

import venues

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
TTL_SECONDS = 3600 * 6  # 6h

BASE = "https://api.open-meteo.com/v1/forecast"


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"weather_{key}.json"


def _read_cache(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.exists() or (time.time() - p.stat().st_mtime) > TTL_SECONDS:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_weather_for_match(venue_name: Optional[str], kickoff_utc: str) -> Optional[dict]:
    """
    Returns dict { temp_c, precip_pct, wind_kmh, humidity_pct, condition,
                   goal_factor, fatigue_factor }
    o None si no se puede determinar la sede o falla la API.
    """
    venue = venues.lookup(venue_name)
    if not venue:
        return None

    # Si tiene techo → clima irrelevante
    if venue.get("dome"):
        return {
            "stadium": venue["stadium"],
            "condition": "dome",
            "temp_c": 22, "precip_pct": 0, "wind_kmh": 0, "humidity_pct": 50,
            "goal_factor": 1.0,
            "fatigue_factor": 1.0,
        }

    # Fecha local del partido (Open-Meteo necesita YYYY-MM-DD)
    kickoff_dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    date_str = kickoff_dt.strftime("%Y-%m-%d")
    hour_utc = kickoff_dt.strftime("%H")

    cache_key = f"{venue['stadium'].replace(' ', '_')}_{date_str}"
    cached = _read_cache(cache_key)
    if cached:
        data = cached
    else:
        try:
            r = requests.get(BASE, params={
                "latitude": venue["lat"], "longitude": venue["lon"],
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,relative_humidity_2m",
                "start_date": date_str, "end_date": date_str,
                "timezone": "UTC",
            }, timeout=15)
            r.raise_for_status()
            data = r.json()
            _cache_path(cache_key).write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            print(f"[weather] fallo Open-Meteo: {e}")
            return None

    # Encontrar el slot horario más cercano al kickoff
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    target_iso = kickoff_dt.strftime("%Y-%m-%dT%H:00")
    idx = None
    for i, t in enumerate(times):
        if t.startswith(target_iso[:13]):
            idx = i
            break
    if idx is None and times:
        idx = min(range(len(times)), key=lambda i: abs(int(times[i][11:13]) - int(hour_utc)))

    if idx is None:
        return None

    temp = hourly["temperature_2m"][idx]
    precip = hourly["precipitation_probability"][idx]
    wind = hourly["wind_speed_10m"][idx]
    humidity = hourly["relative_humidity_2m"][idx]

    # Calcular factores que el modelo usará
    goal_factor = 1.0
    fatigue_factor = venue.get("fatigue_factor", 1.0)

    condition_parts = []
    if precip and precip > 60:
        goal_factor *= 0.92
        condition_parts.append(f"lluvia {precip}%")
    if temp and temp > 33 and humidity and humidity > 60:
        fatigue_factor *= 1.15
        condition_parts.append(f"calor extremo {temp}°C/{humidity}%hum")
    elif temp and temp < 5:
        goal_factor *= 0.95
        condition_parts.append(f"frío {temp}°C")
    if wind and wind > 30:
        condition_parts.append(f"viento {wind}km/h")

    condition = " + ".join(condition_parts) if condition_parts else "normal"

    return {
        "stadium": venue["stadium"],
        "temp_c": round(temp, 1) if temp is not None else None,
        "precip_pct": precip,
        "wind_kmh": round(wind, 1) if wind is not None else None,
        "humidity_pct": humidity,
        "condition": condition,
        "goal_factor": round(goal_factor, 3),
        "fatigue_factor": round(fatigue_factor, 3),
    }


if __name__ == "__main__":
    # Test 1: estadio con techo → dome
    w = get_weather_for_match("SoFi Stadium", "2026-06-15T22:00:00Z")
    print(f"SoFi (techo): {w}")

    # Test 2: Azteca al aire libre, día Mundial
    w = get_weather_for_match("Estadio Azteca", "2026-06-11T20:00:00Z")
    print(f"Azteca: {w}")

    # Test 3: MetLife (NY)
    w = get_weather_for_match("MetLife Stadium", "2026-06-12T18:00:00Z")
    print(f"MetLife: {w}")

    # Test 4: Hard Rock Miami (calor + humedad)
    w = get_weather_for_match("Hard Rock Stadium", "2026-06-20T15:00:00Z")
    print(f"Hard Rock Miami: {w}")
