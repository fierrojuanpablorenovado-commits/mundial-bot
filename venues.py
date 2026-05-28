"""
venues.py — Tabla estática de las 16 sedes del Mundial 2026

Hardcodeado porque no cambia y evita una API call. Match por substring
contra el campo `venue` que devuelve football-data.org.

Factores:
  - altitude_m: >1500m baja resistencia aeróbica → más errores defensivos
                + balón más rápido → más goles esperados (boost +5-10%)
  - climate_tier: hot_humid / temperate / mild — afecta fatiga + ritmo
  - dome: true si tiene techo cerrado (clima irrelevante)
"""

VENUES = {
    # ─── México (3) ───
    "Estadio Azteca": {
        "city": "Ciudad de México", "country": "MEX",
        "lat": 19.302, "lon": -99.150, "altitude_m": 2240,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.08,  # +8% goles esperados por altitud
        "fatigue_factor": 1.15,        # +15% fatiga
    },
    "Estadio Banorte": {  # nombre alterno Azteca tras patrocinio 2025
        "city": "Ciudad de México", "country": "MEX",
        "lat": 19.302, "lon": -99.150, "altitude_m": 2240,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.08, "fatigue_factor": 1.15,
    },
    "Estadio Akron": {
        "city": "Guadalajara", "country": "MEX",
        "lat": 20.681, "lon": -103.460, "altitude_m": 1500,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.04, "fatigue_factor": 1.08,
    },
    "Estadio BBVA": {
        "city": "Monterrey", "country": "MEX",
        "lat": 25.669, "lon": -100.244, "altitude_m": 540,
        "climate_tier": "hot_humid", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.10,
    },

    # ─── USA (11) ───
    "Mercedes-Benz Stadium": {
        "city": "Atlanta", "country": "USA",
        "lat": 33.755, "lon": -84.401, "altitude_m": 320,
        "climate_tier": "hot_humid", "dome": True,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Gillette Stadium": {
        "city": "Foxborough (Boston)", "country": "USA",
        "lat": 42.091, "lon": -71.264, "altitude_m": 30,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "AT&T Stadium": {
        "city": "Arlington (Dallas)", "country": "USA",
        "lat": 32.747, "lon": -97.092, "altitude_m": 178,
        "climate_tier": "hot_humid", "dome": True,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "NRG Stadium": {
        "city": "Houston", "country": "USA",
        "lat": 29.685, "lon": -95.411, "altitude_m": 12,
        "climate_tier": "hot_humid", "dome": True,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Arrowhead Stadium": {
        "city": "Kansas City", "country": "USA",
        "lat": 39.049, "lon": -94.484, "altitude_m": 230,
        "climate_tier": "hot_humid", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.05,
    },
    "SoFi Stadium": {
        "city": "Inglewood (Los Angeles)", "country": "USA",
        "lat": 33.953, "lon": -118.339, "altitude_m": 30,
        "climate_tier": "mild", "dome": True,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Hard Rock Stadium": {
        "city": "Miami Gardens", "country": "USA",
        "lat": 25.958, "lon": -80.239, "altitude_m": 3,
        "climate_tier": "hot_humid", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.12,
    },
    "MetLife Stadium": {
        "city": "East Rutherford (NY/NJ)", "country": "USA",
        "lat": 40.814, "lon": -74.074, "altitude_m": 7,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Lincoln Financial Field": {
        "city": "Philadelphia", "country": "USA",
        "lat": 39.901, "lon": -75.167, "altitude_m": 12,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Levi's Stadium": {
        "city": "Santa Clara (SF Bay)", "country": "USA",
        "lat": 37.403, "lon": -121.970, "altitude_m": 4,
        "climate_tier": "mild", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "Lumen Field": {
        "city": "Seattle", "country": "USA",
        "lat": 47.595, "lon": -122.331, "altitude_m": 5,
        "climate_tier": "mild", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },

    # ─── Canadá (2) ───
    "BMO Field": {
        "city": "Toronto", "country": "CAN",
        "lat": 43.633, "lon": -79.418, "altitude_m": 76,
        "climate_tier": "temperate", "dome": False,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
    "BC Place": {
        "city": "Vancouver", "country": "CAN",
        "lat": 49.277, "lon": -123.112, "altitude_m": 5,
        "climate_tier": "mild", "dome": True,
        "altitude_goal_boost": 1.0, "fatigue_factor": 1.0,
    },
}


def lookup(venue_name: str | None) -> dict | None:
    """Match por substring contra los nombres de estadios."""
    if not venue_name:
        return None
    v_lower = venue_name.lower()
    for stadium, info in VENUES.items():
        if stadium.lower() in v_lower or v_lower in stadium.lower():
            return {"stadium": stadium, **info}
    # Match fuzzy por palabras clave de ciudad
    for stadium, info in VENUES.items():
        city_words = info["city"].lower().split()
        if any(w for w in city_words if len(w) > 4 and w in v_lower):
            return {"stadium": stadium, **info}
    return None


if __name__ == "__main__":
    print(f"Total sedes Mundial 2026: {len(set(v['city'] for v in VENUES.values()))}")
    print(f"Sedes con altitud >1000m: {[s for s,v in VENUES.items() if v['altitude_m']>1000]}")
    print(f"Sedes con techo: {[s for s,v in VENUES.items() if v['dome']]}")
    # Test lookup
    for test in ["MetLife", "Estadio Azteca", "SoFi", "unknown"]:
        result = lookup(test)
        print(f"  lookup({test!r}) → {result['stadium'] if result else None}")
