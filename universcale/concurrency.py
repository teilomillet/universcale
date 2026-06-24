"""Infer a USL throughput-vs-concurrency curve from ordinary spans.

This is the piece that makes USL analysis cheap and universal. Classically you
fit the USL from a *bespoke load test* that drives fixed concurrency levels and
measures throughput at each. That is expensive and rarely run.

But a trace already contains the experiment: at any instant, the number of
overlapping spans of an operation *is* its concurrency, and each span's duration
is its residence time R. By Little's Law, throughput at concurrency N is

    X(N) = N / R(N)

So: bucket each span by the (time-weighted) concurrency it actually experienced,
take the mean residence time per bucket, and you have (N, X) pairs to fit -- from
production traffic, no load harness required.

The estimator is honest about its assumptions: it needs a spread of concurrency
in the trace (a trace that only ever ran at N=1 cannot reveal a ceiling), which
the caller can check via the returned per-bucket sample counts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .spans import Span


@dataclass(frozen=True)
class ConcurrencyBucket:
    concurrency: int
    sample_count: int
    mean_latency_s: float
    throughput: float        # N / mean_latency  (Little's Law)


def experienced_concurrency(spans: Sequence[Span]) -> Dict[int, float]:
    """Map each span (by index) to the time-weighted mean concurrency it saw.

    Concurrency for a span = the average number of co-running spans of the SAME
    operation over that span's lifetime (itself included), integrated across its
    duration. A sweep line over start/end events keeps this O(n log n).
    """
    # Group by operation; concurrency is per-operation, not global.
    by_name: Dict[str, List[int]] = defaultdict(list)
    for i, s in enumerate(spans):
        by_name[s.name].append(i)

    result: Dict[int, float] = {}
    for name, idxs in by_name.items():
        # Build a sorted event timeline: (+1) at start, (-1) at end.
        events: List[Tuple[float, int]] = []
        for i in idxs:
            events.append((spans[i].start, 1))
            events.append((spans[i].end, -1))
        events.sort()

        for i in idxs:
            s = spans[i]
            dur = s.duration
            if dur <= 0:
                result[i] = 1.0
                continue
            # Integrate active count over [s.start, s.end). Walk the timeline,
            # tracking the running active count, accumulating count*overlap.
            active = 0
            weighted = 0.0
            prev_t = None
            for t, delta in events:
                if prev_t is not None and active > 0:
                    lo = max(prev_t, s.start)
                    hi = min(t, s.end)
                    if hi > lo:
                        weighted += active * (hi - lo)
                active += delta
                prev_t = t
                if t >= s.end:
                    break
            result[i] = weighted / dur if dur > 0 else 1.0
    return result


def concurrency_series(
    spans: Sequence[Span],
    name: Optional[str] = None,
    *,
    min_samples: int = 1,
) -> List[ConcurrencyBucket]:
    """Bucket spans of ``name`` by experienced concurrency -> (N, throughput).

    ``name=None`` analyses every operation's spans together (use when the trace
    is a single operation). Returns buckets sorted by concurrency.
    """
    selected = [s for s in spans if name is None or s.name == name]
    if not selected:
        return []
    idx_map = {id(s): i for i, s in enumerate(spans)}
    exp = experienced_concurrency(spans)

    by_level: Dict[int, List[float]] = defaultdict(list)
    for s in selected:
        i = idx_map.get(id(s))
        c = exp.get(i, 1.0) if i is not None else 1.0
        level = max(1, round(c))
        if s.duration > 0:
            by_level[level].append(s.duration)

    buckets: List[ConcurrencyBucket] = []
    for level, lats in sorted(by_level.items()):
        if len(lats) < min_samples:
            continue
        mean_latency = sum(lats) / len(lats)
        buckets.append(
            ConcurrencyBucket(
                concurrency=level,
                sample_count=len(lats),
                mean_latency_s=mean_latency,
                throughput=level / mean_latency if mean_latency > 0 else 0.0,
            )
        )
    return buckets


def series_observations(buckets: Sequence[ConcurrencyBucket]) -> List[Tuple[float, float]]:
    """Reduce buckets to the ``(concurrency, throughput)`` pairs ``fit_usl`` wants."""
    return [(b.concurrency, b.throughput) for b in buckets]
