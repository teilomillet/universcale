"""Human-readable and machine-readable reports over spans.

For *visualisation* we deliberately do not reinvent a flame UI: ``to_chrome_trace``
re-emits spans in Chrome Trace Event format, which https://ui.perfetto.dev and
chrome://tracing render as an interactive, zoomable flame timeline. The text
report is for the terminal / CI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .bottlenecks import bottlenecks
from .concurrency import concurrency_series, series_observations
from .spans import Span
from .usl import USLFit, fit_usl


def to_chrome_trace(spans: Sequence[Span]) -> Dict[str, Any]:
    """Re-emit spans as Chrome Trace Event JSON (open in ui.perfetto.dev)."""
    events: List[Dict[str, Any]] = []
    tids: Dict[Optional[str], int] = {}
    for s in spans:
        tid = tids.setdefault(s.trace_id, len(tids))
        events.append(
            {
                "ph": "X",
                "name": s.name,
                "pid": 1,
                "tid": tid,
                "ts": round(s.start * 1e6, 1),
                "dur": round(s.duration * 1e6, 1),
                "args": s.attributes,
            }
        )
    return {"traceEvents": events, "displayTimeUnit": "ms"}


def fit_operation(spans: Sequence[Span], operation: str) -> Optional[USLFit]:
    """Infer the concurrency curve for one operation from the trace and fit USL."""
    return fit_usl(series_observations(concurrency_series(spans, operation)))


def text_report(spans: Sequence[Span], *, operation: Optional[str] = None, top: int = 12) -> str:
    out: List[str] = []
    rows = bottlenecks(spans, top=top)
    out.append("=== where time goes (self time) ===")
    out.append(f"  {'operation':28} {'share':>7} {'self_p50':>10} {'self_p95':>10} {'count':>7}")
    for r in rows:
        bar = "#" * int(round(r.share_pct / 5.0))
        out.append(
            f"  {r.name:28} {r.share_pct:6.1f}% {r.self_p50_ms:9.1f}m {r.self_p95_ms:9.1f}m {r.count:7d}  {bar}"
        )

    # USL for the chosen operation, or auto-pick the top bottleneck.
    target = operation or (rows[0].name if rows else None)
    if target:
        buckets = concurrency_series(spans, target)
        out.append("")
        out.append(f"=== USL scaling for '{target}' (concurrency inferred from span overlap) ===")
        if len(buckets) < 3:
            levels = sorted({b.concurrency for b in buckets})
            out.append(
                f"  not enough concurrency spread to fit (levels seen: {levels or 'none'}). "
                "Need >= 3 distinct concurrency levels in the trace."
            )
        else:
            for b in buckets:
                out.append(
                    f"    N={b.concurrency:>3}  X={b.throughput:8.3f}/s  "
                    f"mean_latency={b.mean_latency_s * 1000:8.1f}ms  (n={b.sample_count})"
                )
            fit = fit_usl(series_observations(buckets))
            if not fit:
                out.append("  fit failed (degenerate data).")
            else:
                n_max = "inf" if fit.n_max == float("inf") else f"{fit.n_max:.1f}"
                out.append(
                    f"  sigma={fit.sigma:.4f}  kappa={fit.kappa:.5f}  gamma={fit.gamma:.3f}/s  "
                    f"R^2={fit.r2:.3f}{'  [EXACT FIT-unreliable]' if fit.exact_fit else ''}"
                )
                out.append(f"  shape={fit.shape}   N_max={n_max} concurrent   X_max={fit.x_max:.3f}/s")
    return "\n".join(out)


def report_dict(spans: Sequence[Span], *, operation: Optional[str] = None, top: int = 20) -> Dict[str, Any]:
    rows = bottlenecks(spans, top=top)
    target = operation or (rows[0].name if rows else None)
    fit = fit_operation(spans, target) if target else None
    return {
        "operations": len(rows),
        "bottlenecks": [r.__dict__ for r in rows],
        "usl_target": target,
        "usl": None
        if not fit
        else {
            "sigma": fit.sigma,
            "kappa": fit.kappa,
            "gamma": fit.gamma,
            "n_max": None if fit.n_max == float("inf") else fit.n_max,
            "x_max": None if fit.x_max == float("inf") else fit.x_max,
            "r2": fit.r2,
            "dof": fit.dof,
            "exact_fit": fit.exact_fit,
            "shape": fit.shape,
        },
    }
