"""The small amount of statistics this needs, so results carry error bars.

A rate quoted without an interval is the reason this repo exists. The figure in
`docs/AGENTS.md` — "roughly 8% of calls" — comes from 1 failure in 12. The
Wilson 95% interval on 1/12 runs from 1.5% to 35.4%: consistent with a rate that
would never matter and with one that breaks every agent loop on the box. It was
an honest observation reported honestly; it just cannot bear the weight of a
retry policy. Every rate this repo prints comes with the interval attached.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Two-sided 95% normal quantile.
Z95 = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Rate:
    successes: int
    trials: int
    low: float
    high: float

    @property
    def point(self) -> float:
        return self.successes / self.trials if self.trials else float("nan")

    @property
    def half_width(self) -> float:
        return (self.high - self.low) / 2

    def pct(self, places: int = 1) -> str:
        if not self.trials:
            return "n/a"
        return f"{self.point * 100:.{places}f}%"

    def ci_pct(self, places: int = 1) -> str:
        if not self.trials:
            return "n/a"
        return f"[{self.low * 100:.{places}f}, {self.high * 100:.{places}f}]"

    def __str__(self) -> str:
        return f"{self.pct()} {self.ci_pct()} (n={self.trials})"


def wilson(successes: int, trials: int, z: float = Z95) -> Rate:
    """Wilson score interval.

    Not the textbook normal approximation: at the rates that matter here — a few
    percent, and sometimes zero — the normal interval reaches below zero and
    claims certainty it has not got. Wilson stays inside [0, 1] and does not
    collapse to a point width when no failures are observed.
    """
    if trials <= 0:
        return Rate(successes, trials, float("nan"), float("nan"))
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return Rate(successes, trials, max(0.0, centre - margin), min(1.0, centre + margin))


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile. Empty input gives NaN rather than raising."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[int(pos)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass(frozen=True, slots=True)
class Latency:
    n: int
    mean: float
    p50: float
    p95: float
    p99: float

    def __str__(self) -> str:
        return f"mean {self.mean:.2f}s  p50 {self.p50:.2f}s  p95 {self.p95:.2f}s"


def latency(values: list[float]) -> Latency:
    if not values:
        nan = float("nan")
        return Latency(0, nan, nan, nan, nan)
    return Latency(
        n=len(values),
        mean=sum(values) / len(values),
        p50=quantile(values, 0.50),
        p95=quantile(values, 0.95),
        p99=quantile(values, 0.99),
    )


def two_proportion_z(a_succ: int, a_n: int, b_succ: int, b_n: int) -> tuple[float, float]:
    """Pooled two-proportion z test. Returns (z, two-sided p).

    Used for one question only: is the difference between two models' failure
    rates larger than the run's noise. p is computed from an erf-based normal
    CDF so the package keeps its three dependencies.
    """
    if a_n <= 0 or b_n <= 0:
        return float("nan"), float("nan")
    p_pool = (a_succ + b_succ) / (a_n + b_n)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / a_n + 1 / b_n))
    if se == 0:
        return float("nan"), float("nan")
    z = (a_succ / a_n - b_succ / b_n) / se
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p
