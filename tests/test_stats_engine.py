"""Tests del motor estadístico."""
import math
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import stats_engine as se


def test_poisson_pmf_basic():
    """PMF(0, λ) = e^-λ"""
    assert abs(se.poisson_pmf(0, 1.5) - math.exp(-1.5)) < 1e-9
    assert abs(se.poisson_pmf(0, 2.0) - math.exp(-2.0)) < 1e-9


def test_poisson_pmf_zero_lambda():
    """λ=0 → solo P(0)=1, resto 0"""
    assert se.poisson_pmf(0, 0) == 1.0
    assert se.poisson_pmf(1, 0) == 0.0


def test_poisson_matrix_sums_to_one():
    """Matriz 8x8 con λ moderados debe sumar ~1"""
    m = se.poisson_matrix(1.5, 1.2, size=8)
    total = sum(sum(row) for row in m)
    assert abs(total - 1.0) < 0.01


def test_over_under_complementary():
    """P(O X.5) + P(U X.5) = 1"""
    m = se.poisson_matrix(1.5, 1.2)
    p_o = se.prob_over(m, 2.5)
    p_u = se.prob_under(m, 2.5)
    assert abs(p_o + p_u - 1.0) < 0.01


def test_1x2_sums_to_one():
    m = se.poisson_matrix(1.5, 1.2)
    p1, px, p2 = se.prob_1x2(m)
    assert abs(p1 + px + p2 - 1.0) < 0.01


def test_btts_in_range():
    m = se.poisson_matrix(1.5, 1.2)
    p = se.prob_btts(m)
    assert 0 <= p <= 1


def test_implied_probability():
    assert abs(se.implied_probability(2.0) - 0.5) < 1e-9
    assert abs(se.implied_probability(1.5) - 0.6667) < 0.001


def test_edge_calculation():
    """Si modelo dice 60% y cuota implica 50% (cuota 2.0) → edge +20%"""
    edge = se.compute_edge(0.6, 2.0)
    assert abs(edge - 20.0) < 0.1


def test_edge_negative_when_overpriced():
    """Si modelo dice 40% y cuota implica 50% → edge -20%"""
    edge = se.compute_edge(0.4, 2.0)
    assert edge < 0


def test_kelly_zero_for_no_edge():
    """Si P=cuota implícita, Kelly=0"""
    assert se.kelly_fraction(0.5, 2.0) == 0.0


def test_kelly_positive_with_edge():
    """P=60%, cuota 2.0 → Kelly positivo"""
    k = se.kelly_fraction(0.6, 2.0, kelly_div=1.0)
    assert k > 0


def test_kelly_fractional_more_conservative():
    """Kelly 1/4 < Kelly 1/1"""
    k1 = se.kelly_fraction(0.6, 2.0, kelly_div=1.0)
    k4 = se.kelly_fraction(0.6, 2.0, kelly_div=4.0)
    assert k4 < k1
    assert abs(k4 - k1/4) < 1e-9


def test_analyze_match_returns_markets():
    """analyze_match devuelve mercados con probabilidades sensatas"""
    home = se.TeamRating(atq=1.5, def_=1.2, sample_size=10)
    away = se.TeamRating(atq=1.3, def_=1.4, sample_size=10)
    result = se.analyze_match("Home", "Away", home, away, league="FIFA World Cup")
    markets_by_id = {m.market: m for m in result.markets}
    assert "goles_o25" in markets_by_id
    assert "goles_u25" in markets_by_id
    assert 0 < markets_by_id["goles_o25"].model_prob < 1


def test_world_cup_league_average_normalization():
    """Que FIFA World Cup tenga su entry en LEAGUE_AVG_GOALS"""
    assert "FIFA World Cup" in se.LEAGUE_AVG_GOALS
    assert 1.2 <= se.LEAGUE_AVG_GOALS["FIFA World Cup"] <= 1.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
