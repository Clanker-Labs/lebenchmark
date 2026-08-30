from __future__ import annotations

import math

import pytest

from lebenchmark.stats import latency, quantile, two_proportion_z, wilson


class TestWilson:
    def test_reproduces_the_interval_that_motivates_the_repo(self):
        # 1 failure in 12 — the sample behind the documented "roughly 8%".
        r = wilson(1, 12)
        assert math.isclose(r.point, 1 / 12)
        assert math.isclose(r.low, 0.0149, abs_tol=5e-4)
        assert math.isclose(r.high, 0.3539, abs_tol=5e-4)

    def test_a_larger_sample_at_the_same_rate_is_far_tighter(self):
        small, large = wilson(1, 12), wilson(58, 700)
        assert math.isclose(small.point, large.point, abs_tol=0.002)
        assert large.half_width < small.half_width / 5

    def test_zero_failures_does_not_claim_certainty(self):
        r = wilson(0, 100)
        assert r.point == 0.0
        assert r.low == pytest.approx(0.0, abs=1e-12)
        assert r.high > 0.03  # the normal approximation would say 0.0

    def test_bounds_stay_inside_zero_and_one(self):
        for successes, trials in ((0, 5), (5, 5), (1, 3), (99, 100)):
            r = wilson(successes, trials)
            assert 0.0 <= r.low <= r.high <= 1.0

    def test_no_trials_is_not_a_crash(self):
        r = wilson(0, 0)
        assert math.isnan(r.point) and r.pct() == "n/a"


class TestQuantile:
    def test_endpoints_and_median(self):
        values = [1.0, 2.0, 3.0, 4.0]
        assert quantile(values, 0.0) == 1.0
        assert quantile(values, 1.0) == 4.0
        assert quantile(values, 0.5) == 2.5

    def test_empty_gives_nan_rather_than_raising(self):
        assert math.isnan(quantile([], 0.5))


class TestLatency:
    def test_summarises(self):
        s = latency([1.0, 2.0, 3.0, 4.0, 100.0])
        assert s.n == 5 and s.p50 == 3.0 and s.mean == 22.0

    def test_empty_is_safe(self):
        s = latency([])
        assert s.n == 0 and math.isnan(s.mean)


class TestTwoProportion:
    def test_identical_rates_are_indistinguishable(self):
        _, p = two_proportion_z(50, 100, 50, 100)
        assert p > 0.99

    def test_a_large_gap_on_large_samples_is_significant(self):
        _, p = two_proportion_z(90, 100, 50, 100)
        assert p < 1e-8

    def test_the_same_gap_on_tiny_samples_is_not(self):
        _, p = two_proportion_z(9, 10, 5, 10)
        assert p > 0.02
