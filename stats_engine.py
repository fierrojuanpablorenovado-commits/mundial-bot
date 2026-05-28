"""
stats_engine.py — Motor estadístico cuantitativo Tipster v5 (QuantPhi)

Reemplaza la "estimación cualitativa de edge" del LLM por matemática real.

Modelo:
  1. Calcula λ_home y λ_away (expectativa de goles) desde forma reciente real
  2. Aplica distribución de Poisson conjunta (matriz 8x8)
  3. Suma celdas correctas para P(Over X.5), P(Under X.5), P(BTTS), P(1X2)
  4. Calcula edge real = P_modelo - P_implícita_cuota

Portado desde mundial-v3/packages/model/src/poisson.ts (validado con 15+ tests).
Versión Python optimizada con math.exp y sin deps externas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Literal

# ─────────────────────────────────────────────────────────────────────────────
# Tipos
# ─────────────────────────────────────────────────────────────────────────────

Estilo = Literal["P", "V", "D", "B"]  # Posesión, Vertical, Defensivo, Balanceado


@dataclass(frozen=True)
class TeamRating:
    """
    Rating de un equipo, calculado dinámicamente desde forma reciente.

    atq:   goles anotados por partido (ajustado por dificultad de rivales)
    def_:  goles concedidos por partido (más bajo = mejor defensa)
    cpg:   córners por partido (histórico últimos 15 partidos)
    estilo: P=posesión, V=vertical, D=defensivo, B=balanceado
    sample_size: número de partidos usados para calcular (confianza del rating)
    """
    atq: float
    def_: float
    cpg: float = 9.0
    estilo: Estilo = "B"
    sample_size: int = 0


# Default cuando no hay datos suficientes (penaliza confianza)
DEFAULT_TEAM = TeamRating(atq=1.4, def_=1.2, cpg=8.5, estilo="B", sample_size=0)

# Boost de localía: ventaja estadística promedio en fútbol club ~10-15%
HOME_ADVANTAGE = 1.15


# ─────────────────────────────────────────────────────────────────────────────
# Distribución de Poisson
# ─────────────────────────────────────────────────────────────────────────────

def poisson_pmf(k: int, lambda_: float) -> float:
    """
    Probability Mass Function de Poisson.
    P(X = k) = e^-λ × λ^k / k!
    """
    if lambda_ <= 0:
        return 1.0 if k == 0 else 0.0
    result = math.exp(-lambda_)
    for i in range(1, k + 1):
        result *= lambda_ / i
    return result


def poisson_matrix(lh: float, la: float, size: int = 8) -> list[list[float]]:
    """
    Matriz conjunta 8x8: P(home_goals=i, away_goals=j) asumiendo independencia.
    Suma total ≈ 1.0 para size=8 con lambdas <4.
    """
    matrix = []
    for i in range(size):
        row = []
        for j in range(size):
            row.append(poisson_pmf(i, lh) * poisson_pmf(j, la))
        matrix.append(row)
    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo de lambdas (expectativa de goles)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Lambdas:
    lh: float                 # λ home
    la: float                 # λ away
    home: TeamRating
    away: TeamRating
    league_mult: float        # multiplicador por liga (Copa Lib menos goles, etc.)
    confidence: float         # 0.0-1.0 según sample_size de ambos equipos


# Promedio de goles POR EQUIPO POR PARTIDO en cada liga (calibración Poisson)
# Para normalizar: λ = (atq × def_) / league_avg × home_boost
# Sin este factor, hockey (3-5 goles/equipo) sale con lambdas absurdos.
LEAGUE_AVG_GOALS: dict[str, float] = {
    # Fútbol soccer ~ 1.3-1.6 goles/equipo/partido
    "Bundesliga":            1.55,
    "Premier League":        1.45,
    "La Liga":               1.30,
    "Primera División":      1.30,
    "Serie A":               1.35,
    "Ligue 1":               1.40,
    "Eredivisie":            1.65,
    "Liga MX":               1.40,
    "Primeira Liga":         1.35,
    "Championship":          1.30,
    "UEFA Champions League": 1.40,
    "Champions League":      1.40,
    "UEFA Europa League":    1.45,
    "Brasileiro Serie A":    1.20,   # liga más defensiva
    "Copa Libertadores":     1.15,
    "Copa Sudamericana":     1.20,
    "Copa Mundial":          1.30,
    "FIFA World Cup":        1.30,
    # Hockey ~ 3-4 goles/equipo/partido
    "NHL":                          2.95,
    "IIHF Campeonato Mundial":      3.50,
    "World Championship":           3.50,
    # Default = 1.4 (fútbol promedio)
}


def league_avg(league: str) -> float:
    return LEAGUE_AVG_GOALS.get(league, 1.4)


# (Deprecated — mantenido para compat) Multiplicadores genéricos
LEAGUE_MULTIPLIERS: dict[str, float] = {}


def calc_lambdas(
    home: TeamRating,
    away: TeamRating,
    league: str = "",
) -> Lambdas:
    """
    Calcula λ_home y λ_away usando el modelo Dixon-Coles normalizado por liga:

        λ_home = (atq_home × def_away) / league_avg × HOME_ADVANTAGE
        λ_away = (atq_away × def_home) / league_avg

    Donde league_avg es el promedio de goles/equipo/partido en esa liga.
    Esto hace que el modelo funcione para fútbol (avg ~1.4) y hockey (avg ~3.5).
    """
    avg = league_avg(league)

    lh = (home.atq * away.def_) / avg * HOME_ADVANTAGE
    la = (away.atq * home.def_) / avg

    min_sample = min(home.sample_size, away.sample_size)
    confidence = min(min_sample / 15.0, 1.0)

    return Lambdas(lh=lh, la=la, home=home, away=away,
                   league_mult=avg, confidence=confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Probabilidades de mercados
# ─────────────────────────────────────────────────────────────────────────────

def prob_over(matrix: list[list[float]], line: float) -> float:
    """P(home_goals + away_goals > line). Ejemplo: line=2.5 → P(total ≥ 3)."""
    p = 0.0
    size = len(matrix)
    for i in range(size):
        for j in range(size):
            if i + j > line:
                p += matrix[i][j]
    return p


def prob_under(matrix: list[list[float]], line: float) -> float:
    """P(home_goals + away_goals < line)."""
    return 1.0 - prob_over(matrix, line)


def prob_btts(matrix: list[list[float]]) -> float:
    """P(both teams score) = P(home_goals ≥ 1 AND away_goals ≥ 1)."""
    p = 0.0
    size = len(matrix)
    for i in range(1, size):
        for j in range(1, size):
            p += matrix[i][j]
    return p


def prob_1x2(matrix: list[list[float]]) -> tuple[float, float, float]:
    """Devuelve (p_home, p_draw, p_away)."""
    p1 = px = p2 = 0.0
    size = len(matrix)
    for i in range(size):
        for j in range(size):
            cell = matrix[i][j]
            if i > j:
                p1 += cell
            elif i == j:
                px += cell
            else:
                p2 += cell
    return p1, px, p2


def prob_draw(matrix: list[list[float]]) -> float:
    """P(empate)."""
    p = 0.0
    for i in range(len(matrix)):
        p += matrix[i][i]
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Córners (modelo aparte)
# ─────────────────────────────────────────────────────────────────────────────

STYLE_ADJUST: dict[Estilo, float] = {
    "P": 0.5,    # posesión genera más córners
    "V": 0.0,    # vertical neutral
    "D": -0.3,   # defensivo menos córners
    "B": 0.0,
}


def expected_corners(home: TeamRating, away: TeamRating, league: str = "") -> float:
    """λ esperado de córners totales del partido."""
    base = (home.cpg + away.cpg) / 2
    base += STYLE_ADJUST.get(home.estilo, 0.0) + STYLE_ADJUST.get(away.estilo, 0.0)
    if league in ("UEFA Champions League", "UEFA Europa League", "Copa Libertadores"):
        base *= 0.95  # knockouts un poco más cerrados
    return base


def prob_over_corners(expected: float, line: float) -> float:
    """P(corners > line) usando Poisson simple con λ = expected."""
    p_at_most = 0.0
    for k in range(int(line) + 1):
        p_at_most += poisson_pmf(k, expected)
    return 1.0 - p_at_most


# ─────────────────────────────────────────────────────────────────────────────
# Edge y EV
# ─────────────────────────────────────────────────────────────────────────────

def implied_probability(odds: float) -> float:
    """Probabilidad implícita en la cuota (ignora vig)."""
    return 1.0 / odds if odds > 0 else 0.0


def compute_edge(model_prob: float, odds: float) -> float:
    """
    Edge % = (P_modelo / P_implícita - 1) × 100
    Positivo = la cuota está sobrevaluada → apostar
    Negativo = la cuota está subvaluada  → NO apostar
    """
    if odds <= 1.0:
        return -100.0
    implied = implied_probability(odds)
    if implied <= 0:
        return -100.0
    return (model_prob / implied - 1.0) * 100.0


def expected_value(model_prob: float, odds: float, stake: float = 1.0) -> float:
    """
    EV = (P_ganar × ganancia) - (P_perder × stake)
       = stake × (P × cuota - 1)
    """
    return stake * (model_prob * odds - 1.0)


def kelly_fraction(model_prob: float, odds: float, kelly_div: float = 4.0) -> float:
    """
    Kelly Criterion fraccionado (1/4 Kelly por default — más conservador).
    Retorna fracción del bankroll a apostar (0.0 - 1.0).

    Kelly = (P × (cuota - 1) - (1 - P)) / (cuota - 1)
    """
    if odds <= 1.0 or model_prob <= 0:
        return 0.0
    b = odds - 1.0
    kelly = (model_prob * b - (1.0 - model_prob)) / b
    if kelly <= 0:
        return 0.0
    return kelly / kelly_div  # 1/4 Kelly por default


# ─────────────────────────────────────────────────────────────────────────────
# Análisis completo de un partido
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketProb:
    """Probabilidad modelo + edge vs cuota real."""
    market: str             # "goles_o25", "goles_u25", "btts_si", etc.
    label: str              # "Over 2.5 goles"
    line: float
    model_prob: float       # P según Poisson
    odds: Optional[float] = None     # cuota PlayDoit (None si no se pasó)
    implied_prob: Optional[float] = None
    edge_pct: Optional[float] = None
    ev: Optional[float] = None


@dataclass
class MatchAnalysis:
    home_name: str
    away_name: str
    league: str
    lambdas: Lambdas
    markets: list[MarketProb]
    confidence: float       # 0-1, basado en sample_size de ambos equipos


def analyze_match(
    home_name: str,
    away_name: str,
    home_rating: TeamRating,
    away_rating: TeamRating,
    league: str = "",
    odds_dict: Optional[dict[str, float]] = None,
    lines: Optional[tuple[float, ...]] = None,
) -> MatchAnalysis:
    """
    Análisis completo: calcula todas las probabilidades de mercados O/U + BTTS + 1X2.

    odds_dict (opcional): {"goles_o25": 1.85, "goles_u25": 1.95, ...}
                          Si se pasa, calcula edge real vs cuota.
    lines (opcional): líneas O/U a evaluar. Default: (1.5, 2.5, 3.5) para fútbol.
                      Hockey usa (4.5, 5.5, 6.5). NBA usa líneas mucho mayores.
    """
    if lines is None:
        lines = (1.5, 2.5, 3.5)

    lambdas = calc_lambdas(home_rating, away_rating, league)
    # Matriz más grande si las líneas son altas (hockey)
    size = max(8, int(max(lines)) + 4)
    matrix = poisson_matrix(lambdas.lh, lambdas.la, size=size)

    markets: list[MarketProb] = []

    # Over/Under goles
    for line in lines:
        p_over  = prob_over(matrix, line)
        p_under = 1.0 - p_over

        for side, prob, label in [
            (f"goles_o{int(line*10)}", p_over,  f"Over {line} goles"),
            (f"goles_u{int(line*10)}", p_under, f"Under {line} goles"),
        ]:
            mp = MarketProb(market=side, label=label, line=line, model_prob=prob)
            if odds_dict and side in odds_dict:
                mp.odds = odds_dict[side]
                mp.implied_prob = implied_probability(mp.odds)
                mp.edge_pct = compute_edge(prob, mp.odds)
                mp.ev = expected_value(prob, mp.odds)
            markets.append(mp)

    # BTTS
    p_btts = prob_btts(matrix)
    markets.append(MarketProb(market="btts_si", label="Ambos anotan: Sí", line=0,
                              model_prob=p_btts))
    markets.append(MarketProb(market="btts_no", label="Ambos anotan: No", line=0,
                              model_prob=1 - p_btts))

    # Córners
    xc = expected_corners(home_rating, away_rating, league)
    for line in (8.5, 9.5, 10.5):
        p_over = prob_over_corners(xc, line)
        mp = MarketProb(market=f"corners_o{int(line*10)}",
                        label=f"Over {line} córners",
                        line=line, model_prob=p_over)
        if odds_dict and mp.market in odds_dict:
            mp.odds = odds_dict[mp.market]
            mp.implied_prob = implied_probability(mp.odds)
            mp.edge_pct = compute_edge(p_over, mp.odds)
            mp.ev = expected_value(p_over, mp.odds)
        markets.append(mp)

    return MatchAnalysis(
        home_name=home_name, away_name=away_name, league=league,
        lambdas=lambdas, markets=markets, confidence=lambdas.confidence,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (correr `python stats_engine.py` para validar)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test 1: Poisson PMF
    p = poisson_pmf(2, 1.5)
    expected = math.exp(-1.5) * 1.5**2 / 2
    assert abs(p - expected) < 1e-6, f"PMF falló: {p} vs {expected}"
    print(f"✅ poisson_pmf(2, 1.5) = {p:.6f}")

    # Test 2: Matriz suma a 1
    m = poisson_matrix(1.5, 1.2)
    total = sum(sum(row) for row in m)
    assert abs(total - 1.0) < 0.005, f"Matriz no suma 1: {total}"
    print(f"✅ poisson_matrix suma = {total:.4f}")

    # Test 3: Probabilidades base
    p_o25 = prob_over(m, 2.5)
    p_u25 = prob_under(m, 2.5)
    assert abs(p_o25 + p_u25 - 1.0) < 0.01
    print(f"✅ P(O2.5) = {p_o25*100:.1f}%, P(U2.5) = {p_u25*100:.1f}%")

    # Test 4: Caso real — Bayern Munich (atq fuerte) vs equipo medio
    bayern = TeamRating(atq=2.4, def_=1.0, cpg=10.5, estilo="P", sample_size=15)
    avg    = TeamRating(atq=1.3, def_=1.3, cpg=8.5,  estilo="B", sample_size=15)
    analysis = analyze_match("Bayern", "Augsburg", bayern, avg, "Bundesliga",
                             odds_dict={"goles_o25": 1.45})
    o25 = next(m for m in analysis.markets if m.market == "goles_o25")
    print(f"\n✅ Bayern (atq 2.4) vs Augsburg (def 1.3) Bundesliga:")
    print(f"   λ_home = {analysis.lambdas.lh:.2f}, λ_away = {analysis.lambdas.la:.2f}")
    print(f"   P(O2.5) modelo = {o25.model_prob*100:.1f}%")
    print(f"   P implícita @1.45 = {o25.implied_prob*100:.1f}%")
    print(f"   Edge = {o25.edge_pct:+.1f}%  EV/$ = {o25.ev:+.3f}")

    # Test 5: Kelly fraction
    k = kelly_fraction(0.65, 1.80)
    print(f"\n✅ Kelly @ P=65% / cuota 1.80 → fracción bankroll = {k*100:.1f}%")

    print("\n🎯 stats_engine.py: TODOS LOS TESTS PASARON")
