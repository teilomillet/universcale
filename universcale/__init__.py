"""universcale -- Universal Scalability Law tooling for capacity curves.

Two ways in:

1. You already have ``(concurrency, throughput)`` measurements (e.g. a load
   sweep)::

       from universcale import fit_usl
       fit = fit_usl([(1, 0.72), (2, 0.80), (4, 0.78), (8, 0.80), (16, 0.80)])
       print(fit.n_max, fit.x_max, fit.shape)

2. You only have a trace (OpenTelemetry / Chrome / Perfetto). Infer the curve
   from span overlap (Little's Law) and fit it -- no load harness needed::

       from universcale import load_spans, concurrency_series, series_observations, fit_usl
       spans = load_spans("trace.json")
       fit = fit_usl(series_observations(concurrency_series(spans, "vlm_extraction")))

Plus ``bottlenecks(spans)`` for where wall-clock goes. The core is pure stdlib.
"""

from __future__ import annotations

from .bottlenecks import Bottleneck, bottlenecks
from .concurrency import (
    ConcurrencyBucket,
    concurrency_series,
    experienced_concurrency,
    series_observations,
)
from .spans import Span, from_chrome_trace, from_otlp, from_records, load_spans
from .usl import USLFit, fit_usl, usl_throughput

__all__ = [
    "USLFit",
    "fit_usl",
    "usl_throughput",
    "Span",
    "load_spans",
    "from_chrome_trace",
    "from_otlp",
    "from_records",
    "ConcurrencyBucket",
    "concurrency_series",
    "series_observations",
    "experienced_concurrency",
    "Bottleneck",
    "bottlenecks",
]

__version__ = "0.1.0"
