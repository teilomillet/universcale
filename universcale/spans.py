"""A minimal span model and adapters from common trace formats.

`universcale` does not instrument code -- OpenTelemetry (and friends) already do
that well. It *analyses* spans someone else produced. So the only data model is a
tiny, dependency-free ``Span``, plus adapters that normalise the common export
formats into it. Everything downstream (concurrency inference, USL fit,
bottleneck ranking) consumes ``list[Span]``.

All times are normalised to **seconds** (float), with ``start``/``end`` on an
arbitrary but consistent epoch so durations and overlaps are meaningful.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union


@dataclass
class Span:
    name: str
    start: float            # seconds
    end: float              # seconds
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _as_seconds(value: float, unit: str) -> float:
    return value * {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[unit]


def from_chrome_trace(doc: Union[Dict[str, Any], List[Any]]) -> List[Span]:
    """Chrome Trace Event format (the `traceEvents` list; ts/dur in microseconds).

    This is what `chrome://tracing` / Perfetto read and what many profilers emit.
    """
    events = doc.get("traceEvents", doc) if isinstance(doc, dict) else doc
    spans: List[Span] = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict) or ev.get("ph") != "X":
            continue  # only complete (duration) events
        start = _as_seconds(float(ev.get("ts", 0.0)), "us")
        dur = _as_seconds(float(ev.get("dur", 0.0)), "us")
        spans.append(
            Span(
                name=str(ev.get("name", "?")),
                start=start,
                end=start + dur,
                trace_id=str(ev.get("tid")) if ev.get("tid") is not None else None,
                span_id=str(i),
                attributes=dict(ev.get("args", {}) or {}),
            )
        )
    # Chrome encodes nesting by containment on a track (tid), not explicit parent
    # ids. Recover parent = the smallest span on the same track that contains it,
    # so self-time attribution is correct.
    _infer_parents_by_containment(spans)
    return spans


def _infer_parents_by_containment(spans: List[Span]) -> None:
    by_track: Dict[Optional[str], List[Span]] = {}
    for s in spans:
        by_track.setdefault(s.trace_id, []).append(s)
    for track_spans in by_track.values():
        # Outer-first ordering: a parent opens before and closes after its child.
        ordered = sorted(track_spans, key=lambda s: (s.start, -s.end))
        stack: List[Span] = []
        for s in ordered:
            while stack and stack[-1].end <= s.start:
                stack.pop()
            if stack and stack[-1].start <= s.start and stack[-1].end >= s.end and stack[-1] is not s:
                s.parent_id = stack[-1].span_id
            stack.append(s)


def from_otlp(doc: Dict[str, Any]) -> List[Span]:
    """OTLP trace JSON (opentelemetry exporter `file`/`otlp-json`).

    Walks resourceSpans -> scopeSpans -> spans; times are unix nanoseconds.
    """
    spans: List[Span] = []
    for resource in doc.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", resource.get("instrumentationLibrarySpans", [])):
            for s in scope.get("spans", []):
                start_ns = float(s.get("startTimeUnixNano", 0))
                end_ns = float(s.get("endTimeUnixNano", start_ns))
                attrs = {}
                for kv in s.get("attributes", []):
                    val = kv.get("value", {})
                    attrs[kv.get("key")] = next(iter(val.values()), None) if val else None
                spans.append(
                    Span(
                        name=str(s.get("name", "?")),
                        start=_as_seconds(start_ns, "ns"),
                        end=_as_seconds(end_ns, "ns"),
                        trace_id=s.get("traceId"),
                        span_id=s.get("spanId"),
                        parent_id=s.get("parentSpanId") or None,
                        attributes=attrs,
                    )
                )
    return spans


def from_records(records: Iterable[Dict[str, Any]], *, time_unit: str = "ms") -> List[Span]:
    """Generic adapter for plain dict records.

    Accepts either ``start``/``end`` or ``start``/``duration`` (or ``elapsed_ms``)
    in ``time_unit``. This is the escape hatch for any home-grown profiler -- e.g.
    the `pipeline_profile` logs, where each record has t_offset/elapsed.
    """
    spans: List[Span] = []
    for r in records:
        name = str(r.get("name") or r.get("stage") or "?")
        if "start" in r:
            start = _as_seconds(float(r["start"]), time_unit)
        elif "t_offset_ms" in r:
            start = _as_seconds(float(r["t_offset_ms"]), "ms")
        else:
            start = 0.0
        if "end" in r:
            end = _as_seconds(float(r["end"]), time_unit)
        elif "duration" in r:
            end = start + _as_seconds(float(r["duration"]), time_unit)
        elif "elapsed_ms" in r:
            end = start + _as_seconds(float(r["elapsed_ms"]), "ms")
        else:
            end = start
        spans.append(
            Span(
                name=name,
                start=start,
                end=end,
                trace_id=str(r.get("request_id") or r.get("trace_id") or "") or None,
                parent_id=r.get("parent"),
                attributes={k: v for k, v in r.items() if k not in {"name", "stage", "start", "end", "duration", "elapsed_ms", "t_offset_ms"}},
            )
        )
    return spans


def load_spans(path: Union[str, Path]) -> List[Span]:
    """Auto-detect the trace format of a JSON/JSONL file and load spans."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    # JSONL of records?
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        raise ValueError(f"{path}: not JSON")
    if "\n" in stripped and stripped.startswith("{") and '"traceEvents"' not in text and '"resourceSpans"' not in text:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        return from_records(records)
    doc = json.loads(text)
    if isinstance(doc, dict) and "traceEvents" in doc:
        return from_chrome_trace(doc)
    if isinstance(doc, dict) and "resourceSpans" in doc:
        return from_otlp(doc)
    if isinstance(doc, list):
        return from_records(doc)
    raise ValueError(f"{path}: unrecognised trace format")
