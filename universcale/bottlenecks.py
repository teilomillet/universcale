"""Rank operations by where wall-clock actually goes.

Inclusive time double-counts parents; the useful signal for "what should I
optimise" is **self time** (a span's duration minus the time its children spent),
which attributes wall-clock to the operation that actually owns it. When a trace
carries no parent links the two coincide (flat trace).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .spans import Span


@dataclass(frozen=True)
class Bottleneck:
    name: str
    count: int
    self_mean_ms: float
    self_p50_ms: float
    self_p95_ms: float
    self_total_ms: float
    incl_mean_ms: float
    share_pct: float       # share of summed self-time across all operations


def _pct(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def _self_durations(spans: Sequence[Span]) -> Dict[int, float]:
    """Self time per span = duration - sum(direct children durations)."""
    by_id = {s.span_id: i for i, s in enumerate(spans) if s.span_id}
    child_time: Dict[int, float] = defaultdict(float)
    for s in spans:
        if s.parent_id and s.parent_id in by_id:
            child_time[by_id[s.parent_id]] += s.duration
    return {i: max(0.0, s.duration - child_time.get(i, 0.0)) for i, s in enumerate(spans)}


def bottlenecks(spans: Sequence[Span], *, top: Optional[int] = None) -> List[Bottleneck]:
    self_dur = _self_durations(spans)
    by_name_self: Dict[str, List[float]] = defaultdict(list)
    by_name_incl: Dict[str, List[float]] = defaultdict(list)
    for i, s in enumerate(spans):
        by_name_self[s.name].append(self_dur[i] * 1000.0)
        by_name_incl[s.name].append(s.duration * 1000.0)

    grand_self = sum(sum(v) for v in by_name_self.values()) or 1.0
    rows: List[Bottleneck] = []
    for name, self_ms in by_name_self.items():
        ordered = sorted(self_ms)
        total = sum(self_ms)
        incl = by_name_incl[name]
        rows.append(
            Bottleneck(
                name=name,
                count=len(self_ms),
                self_mean_ms=total / len(self_ms),
                self_p50_ms=_pct(ordered, 0.50),
                self_p95_ms=_pct(ordered, 0.95),
                self_total_ms=total,
                incl_mean_ms=sum(incl) / len(incl),
                share_pct=100.0 * total / grand_self,
            )
        )
    rows.sort(key=lambda r: r.self_total_ms, reverse=True)
    return rows[:top] if top else rows
