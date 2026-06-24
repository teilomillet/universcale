"""Concurrency inference from span overlap, and the trace -> USL pipeline."""

from __future__ import annotations

from universcale import (
    Span,
    bottlenecks,
    concurrency_series,
    experienced_concurrency,
    fit_usl,
    series_observations,
)


def _op(name, start, dur, **attrs):
    return Span(name=name, start=start, end=start + dur, attributes=attrs)


def test_experienced_concurrency_serial_vs_parallel():
    # Two non-overlapping spans => concurrency 1 each.
    serial = [_op("x", 0.0, 1.0), _op("x", 2.0, 1.0)]
    exp = experienced_concurrency(serial)
    assert round(exp[0]) == 1 and round(exp[1]) == 1

    # Three fully-overlapping spans => concurrency 3 each.
    parallel = [_op("x", 0.0, 1.0), _op("x", 0.0, 1.0), _op("x", 0.0, 1.0)]
    exp = experienced_concurrency(parallel)
    assert all(round(v) == 3 for v in exp.values())


def test_concurrency_is_per_operation():
    # Overlapping spans of DIFFERENT names do not inflate each other.
    spans = [_op("a", 0.0, 1.0), _op("b", 0.0, 1.0)]
    exp = experienced_concurrency(spans)
    assert all(round(v) == 1 for v in exp.values())


def test_series_uses_little_law():
    # At concurrency 2 with 0.5s latency, throughput should be 2/0.5 = 4/s.
    spans = [_op("x", 0.0, 0.5), _op("x", 0.0, 0.5)]
    buckets = concurrency_series(spans, "x")
    assert len(buckets) == 1
    b = buckets[0]
    assert b.concurrency == 2
    assert abs(b.throughput - 4.0) < 1e-6


def test_trace_to_usl_recovers_ceiling():
    # Build a synthetic trace where latency grows with concurrency (contention):
    # at level N, run N overlapping spans whose latency = base * (1 + s*(N-1)).
    # That yields throughput X = N / latency, a USL curve with a real ceiling.
    base, s = 1.0, 0.4
    spans = []
    t = 0.0
    for n in (1, 2, 4, 8, 16):
        latency = base * (1 + s * (n - 1))
        for _ in range(n):
            spans.append(_op("vlm", t, latency))
        t += latency + 5.0  # gap so levels don't bleed together
    buckets = concurrency_series(spans, "vlm")
    levels = [b.concurrency for b in buckets]
    assert levels == [1, 2, 4, 8, 16]
    fit = fit_usl(series_observations(buckets))
    assert fit is not None
    assert fit.r2 > 0.8
    # Contention-dominated: throughput saturates, doesn't scale linearly.
    assert fit.throughput(16) < 16 * fit.throughput(1)


def test_bottlenecks_self_time_excludes_children():
    parent = Span(name="pipeline", start=0.0, end=1.0, span_id="p")
    child = Span(name="vlm", start=0.1, end=0.9, span_id="c", parent_id="p")
    rows = {r.name: r for r in bottlenecks([parent, child])}
    # parent self time = 1.0 - 0.8 = 0.2s = 200ms; child = 800ms.
    assert abs(rows["pipeline"].self_mean_ms - 200.0) < 1.0
    assert abs(rows["vlm"].self_mean_ms - 800.0) < 1.0
    # vlm owns the larger share of self time.
    assert rows["vlm"].share_pct > rows["pipeline"].share_pct
