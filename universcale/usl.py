"""Universal Scalability Law: model, and a zero-dependency least-squares fit.

The USL describes throughput X as a function of concurrency N:

    X(N) = gamma * N / (1 + sigma*(N-1) + kappa*N*(N-1))

    gamma  ideal per-worker throughput (X at N=1 with no interference)
    sigma  contention   -- serialized/queueing fraction (Amdahl-like)
    kappa  coherency    -- cross-talk cost; if > 0 the curve is *retrograde*
                           (throughput peaks then DECLINES as load grows)

The peak (maximum-throughput concurrency) and that peak throughput:

    N_max = sqrt((1 - sigma) / kappa)          (when kappa > 0)
    X_max = X(N_max)

The fit is linear and needs no numpy. Dividing the model through by X:

    N / X(N) = (1/gamma) + (sigma/gamma)*(N-1) + (kappa/gamma)*N*(N-1)

so regressing Z = N/X on the columns [1, (N-1), N*(N-1)] recovers all three
coefficients via the 3x3 normal equations, solved here with Gaussian elimination.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Coherency below this is float noise, not a real cross-talk term: treat as 0,
# i.e. no retrograde peak. Keep consistent with USLFit.shape.
_KAPPA_EPS = 1e-9


@dataclass(frozen=True)
class USLFit:
    """Result of fitting the USL to (concurrency, throughput) observations."""

    sigma: float
    kappa: float
    gamma: float
    n_max: float          # math.inf when there is no retrograde peak
    x_max: float
    r2: float
    dof: int              # observations - 3; dof <= 0 is an exact (overfit) solve
    observations: List[Tuple[float, float]]

    @property
    def exact_fit(self) -> bool:
        """True when the fit is exactly determined -- R^2 is trivially 1.0."""
        return self.dof <= 0

    @property
    def shape(self) -> str:
        if self.kappa > _KAPPA_EPS:
            return "retrograde"          # peaks then declines
        if self.sigma > 0.5:
            return "contention-bound"    # steep early saturation
        if self.sigma > 0.05:
            return "plateau"             # mild contention
        return "near-linear"

    def throughput(self, n: float) -> float:
        return usl_throughput(n, gamma=self.gamma, sigma=self.sigma, kappa=self.kappa)

    def headroom(self, p: float) -> float:
        """Throughput-ceiling multiplier if a fraction ``p`` of load is removed.

        E.g. routing a fraction of work away from the bottleneck (native-PDF
        bypass of the VLM) lifts the ceiling by ~1/(1-p) for the same hardware.
        """
        return 1.0 / (1.0 - p) if 0.0 <= p < 1.0 else math.inf


def usl_throughput(n: float, *, gamma: float, sigma: float, kappa: float) -> float:
    denom = 1.0 + sigma * (n - 1.0) + kappa * n * (n - 1.0)
    return gamma * n / denom if denom else math.inf


def _solve_3x3(a: List[List[float]], b: List[float]) -> Optional[List[float]]:
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""
    m = [a[i][:] + [b[i]] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-15:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        for r in range(3):
            if r == col:
                continue
            f = m[r][col] / pv
            for c in range(col, 4):
                m[r][c] -= f * m[col][c]
    return [m[i][3] / m[i][i] for i in range(3)]


def fit_usl(observations: Sequence[Tuple[float, float]]) -> Optional[USLFit]:
    """Fit the USL to ``(concurrency, throughput)`` pairs.

    Averages duplicate concurrency levels, needs >= 3 distinct levels, and
    returns ``None`` when the data is degenerate (collinear / non-physical).
    """
    # Collapse duplicate N by averaging throughput; drop non-positive throughput.
    by_n: dict[float, List[float]] = {}
    for n, x in observations:
        if n and x and x > 0:
            by_n.setdefault(float(n), []).append(float(x))
    pairs = sorted((n, sum(v) / len(v)) for n, v in by_n.items())
    if len(pairs) < 3:
        return None

    # Normal equations for Z = N/X on columns [1, (N-1), N(N-1)].
    ata = [[0.0] * 3 for _ in range(3)]
    atb = [0.0, 0.0, 0.0]
    for n, x in pairs:
        row = [1.0, n - 1.0, n * (n - 1.0)]
        z = n / x
        for i in range(3):
            atb[i] += row[i] * z
            for j in range(3):
                ata[i][j] += row[i] * row[j]
    coeffs = _solve_3x3(ata, atb)
    if coeffs is None:
        return None
    a, b, c = coeffs
    if a <= 0:
        return None  # 1/gamma must be positive for a physical fit

    gamma = 1.0 / a
    sigma = b / a
    kappa = c / a

    # R^2 on the throughput curve itself (not the linearized Z).
    xs = [x for _, x in pairs]
    preds = [usl_throughput(n, gamma=gamma, sigma=sigma, kappa=kappa) for n, _ in pairs]
    mean_x = sum(xs) / len(xs)
    ss_res = sum((x - p) ** 2 for x, p in zip(xs, preds))
    ss_tot = sum((x - mean_x) ** 2 for x in xs)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    if kappa > _KAPPA_EPS:
        n_max = math.sqrt(max(0.0, 1.0 - sigma) / kappa)
        x_max = usl_throughput(n_max, gamma=gamma, sigma=sigma, kappa=kappa)
    else:
        n_max = math.inf
        x_max = gamma / sigma if sigma > 0 else math.inf

    return USLFit(
        sigma=sigma,
        kappa=kappa,
        gamma=gamma,
        n_max=n_max,
        x_max=x_max,
        r2=r2,
        dof=len(pairs) - 3,
        observations=pairs,
    )
