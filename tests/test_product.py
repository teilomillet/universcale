"""Product / acceptance tests: exercise universcale as a real user would.

These do not poke internals -- they drive the CLI and public API over realistic
trace fixtures (OTLP, Chrome) and assert the promises in the README hold:

  * "trace in, scaling ceiling out" (infer N from overlap, fit USL)
  * bottleneck ranking by self-time over a nested trace
  * a stable JSON contract for a CI capacity gate
  * Perfetto export round-trips
  * honesty: no fabricated fit without concurrency spread; overfit is flagged
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from universcale import fit_usl, load_spans
from universcale.cli import main


# --------------------------------------------------------------------------- #
# Realistic trace fixtures
# --------------------------------------------------------------------------- #
def _otlp(spans: List[Tuple[str, float, float, str, Optional[str]]]) -> dict:
    """Build an OTLP trace JSON doc (as the OTel file exporter would emit)."""
    otlp_spans = []
    for name, start_s, dur_s, span_id, parent_id in spans:
        otlp_spans.append(
            {
                "name": name,
                "spanId": span_id,
                "parentSpanId": parent_id or "",
                "traceId": "t",
                "startTimeUnixNano": str(int(start_s * 1e9)),
                "endTimeUnixNano": str(int((start_s + dur_s) * 1e9)),
                "attributes": [{"key": "op", "value": {"stringValue": name}}],
            }
        )
    return {"resourceSpans": [{"scopeSpans": [{"spans": otlp_spans}]}]}


def _chrome(spans: List[Tuple[str, float, float, int]]) -> dict:
    return {
        "traceEvents": [
            {"ph": "X", "name": n, "ts": s * 1e6, "dur": d * 1e6, "pid": 1, "tid": tid}
            for (n, s, d, tid) in spans
        ],
        "displayTimeUnit": "ms",
    }


def _concurrency_trace(op: str, levels, *, base=1.0, contention=0.4):
    """A trace where each level N runs N overlapping spans whose latency grows
    with N (contention) -- i.e. a real USL curve, recoverable from overlap alone.
    """
    spans = []
    t = 0.0
    sid = 0
    for n in levels:
        latency = base * (1 + contention * (n - 1))
        for _ in range(n):
            spans.append((op, t, latency, str(sid), None))
            sid += 1
        t += latency + 5.0  # gap so levels stay distinct
    return _otlp(spans)


def _write(tmp_path: Path, name: str, doc) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Flagship: trace in -> scaling ceiling out
# --------------------------------------------------------------------------- #
def test_cli_infers_scaling_ceiling_from_otlp_trace(tmp_path, capsys):
    trace = _write(tmp_path, "otlp.json", _concurrency_trace("vlm_extraction", [1, 2, 4, 8, 16]))
    rc = main([str(trace), "--operation", "vlm_extraction"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "USL scaling for 'vlm_extraction'" in out
    assert "N_max=" in out and "X_max=" in out
    assert "sigma=" in out and "kappa=" in out
    # Contention-bound trace must NOT report linear scaling.
    assert "shape=near-linear" not in out


def test_public_api_two_ways_in_agree(tmp_path):
    # Way 1: a load sweep you already have.
    sweep = fit_usl([(1, 1.0), (2, 1.43), (4, 1.82), (8, 2.0), (16, 1.9)])
    assert sweep is not None and sweep.n_max < 100

    # Way 2: only a trace -> infer the same kind of curve.
    from universcale import concurrency_series, series_observations

    trace = _concurrency_trace("op", [1, 2, 4, 8, 16])
    spans = load_spans(_write(tmp_path, "t.json", trace))
    inferred = fit_usl(series_observations(concurrency_series(spans, "op")))
    assert inferred is not None
    assert inferred.r2 > 0.8  # the inferred curve is a good USL fit


# --------------------------------------------------------------------------- #
# Bottleneck ranking over a nested Chrome trace
# --------------------------------------------------------------------------- #
def test_cli_ranks_bottleneck_in_nested_chrome_trace(tmp_path, capsys):
    # pipeline(0..2.0) contains vlm(0.1..1.9) and render(0.0..0.1 wrapped) ...
    doc = _chrome(
        [
            ("pipeline", 0.0, 2.0, 0),
            ("render", 0.0, 0.2, 0),
            ("vlm", 0.2, 1.7, 0),   # the heavy leaf
            ("parse", 1.9, 0.05, 0),
        ]
    )
    trace = _write(tmp_path, "chrome.json", doc)
    rc = main([str(trace)])
    out = capsys.readouterr().out
    assert rc == 0
    # vlm is the heaviest *self* time and should head the ranking, above its parent.
    lines = [ln for ln in out.splitlines() if "vlm" in ln or "pipeline" in ln]
    assert lines and "vlm" in lines[0]


# --------------------------------------------------------------------------- #
# CI capacity-gate contract (machine readable)
# --------------------------------------------------------------------------- #
def test_json_output_is_a_stable_gate_contract(tmp_path, capsys):
    trace = _write(tmp_path, "otlp.json", _concurrency_trace("vlm", [1, 2, 4, 8, 16]))
    rc = main([str(trace), "--operation", "vlm", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    # Contract a CI gate can rely on.
    assert {"operations", "bottlenecks", "usl_target", "usl"} <= report.keys()
    usl = report["usl"]
    assert {"sigma", "kappa", "n_max", "x_max", "r2", "dof", "exact_fit", "shape"} <= usl.keys()
    # A gate would assert, e.g., the ceiling has not regressed below a bound.
    assert usl["n_max"] is None or usl["n_max"] > 0


# --------------------------------------------------------------------------- #
# Perfetto export round-trips
# --------------------------------------------------------------------------- #
def test_trace_out_roundtrips_for_perfetto(tmp_path, capsys):
    src = _write(tmp_path, "otlp.json", _concurrency_trace("op", [1, 2, 4]))
    norm = tmp_path / "norm.json"
    rc = main([str(src), "--trace-out", str(norm)])
    assert rc == 0 and norm.exists()
    doc = json.loads(norm.read_text())
    assert "traceEvents" in doc and doc["displayTimeUnit"] == "ms"
    # The exported Chrome trace is itself loadable by universcale.
    assert load_spans(norm)


# --------------------------------------------------------------------------- #
# Honesty guarantees
# --------------------------------------------------------------------------- #
def test_refuses_to_fit_without_concurrency_spread(tmp_path, capsys):
    # A purely serial trace: every span runs alone -> only concurrency level 1.
    doc = _otlp([("op", float(i) * 2.0, 1.0, str(i), None) for i in range(6)])
    trace = _write(tmp_path, "serial.json", doc)
    rc = main([str(trace), "--operation", "op"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not enough concurrency spread" in out


def test_exact_fit_is_flagged_unreliable(tmp_path, capsys):
    # Exactly three concurrency levels -> exact (overfit) solve, must be flagged.
    trace = _write(tmp_path, "three.json", _concurrency_trace("op", [1, 2, 4]))
    rc = main([str(trace), "--operation", "op", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["usl"]["exact_fit"] is True


def test_missing_trace_file_errors_cleanly(tmp_path, capsys):
    rc = main([str(tmp_path / "nope.json")])
    assert rc == 1
    assert "error" in capsys.readouterr().err.lower()
