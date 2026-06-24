"""Self-contained HTML report: USL panel + flame timeline + bottleneck bars.

When you analyse a trace you already hold everything for both the scaling fit and
the visual, so this renders them together into one standalone page (inline SVG,
no JS, no deps) that opens directly in a browser -- the flame "pops up" alongside
the USL numbers, showing where the time that causes the ceiling is actually spent.
"""

from __future__ import annotations

import html
from collections import defaultdict
from statistics import median
from typing import Dict, List, Optional, Sequence

from .bottlenecks import bottlenecks
from .concurrency import concurrency_series, series_observations
from .spans import Span
from .usl import fit_usl

_PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
]


def _colour(name: str) -> str:
    return _PALETTE[hash(name) % len(_PALETTE)]


def _rect(x: float, y: float, w: float, h: float, fill: str, label: str, sub: str) -> str:
    w = max(w, 0.6)
    text = ""
    if w > 44:
        text = (
            f'<text x="{x + 5:.1f}" y="{y + h / 2 + 4:.1f}" font-size="11" fill="#fff">'
            f"{html.escape(label)}<tspan fill='#e8e8e8'> {html.escape(sub)}</tspan></text>"
        )
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" rx="2" '
        f'stroke="#1a1a1a" stroke-width="0.5"><title>{html.escape(label)} — {html.escape(sub)}</title></rect>{text}'
    )


def _by_request(spans: Sequence[Span]) -> Dict[Optional[str], List[Span]]:
    groups: Dict[Optional[str], List[Span]] = defaultdict(list)
    for s in spans:
        groups[s.trace_id].append(s)
    return groups


def _representative_request(spans: Sequence[Span]) -> List[Span]:
    """The request whose total span equals the median total -- a fair flame."""
    groups = _by_request(spans)
    if not groups:
        return []
    spans_of = list(groups.values())
    totals = [max(s.end for s in g) - min(s.start for s in g) for g in spans_of]
    med = median(totals)
    return min(spans_of, key=lambda g: abs((max(s.end for s in g) - min(s.start for s in g)) - med))


def _depth(span: Span, by_id: Dict[str, Span]) -> int:
    depth, parent = 0, span.parent_id
    seen = set()
    while parent and parent in by_id and parent not in seen:
        seen.add(parent)
        depth += 1
        parent = by_id[parent].parent_id
    return depth


def svg_flame(req_spans: Sequence[Span], width: int = 1080) -> str:
    if not req_spans:
        return "<p>no spans</p>"
    t0 = min(s.start for s in req_spans)
    total = max(s.end for s in req_spans) - t0 or 1.0
    scale = (width - 20) / total
    by_id = {s.span_id: s for s in req_spans if s.span_id}
    row_h, gap = 30, 4
    rows: List[str] = []
    max_depth = 0
    for s in sorted(req_spans, key=lambda s: (s.start, -s.end)):
        d = _depth(s, by_id)
        max_depth = max(max_depth, d)
        x = 10 + (s.start - t0) * scale
        w = s.duration * scale
        y = 10 + d * (row_h + gap)
        pct = 100.0 * s.duration / total
        rows.append(_rect(x, y, w, row_h, _colour(s.name), s.name, f"{s.duration * 1000:.0f}ms · {pct:.0f}%"))
    height = 20 + (max_depth + 1) * (row_h + gap)
    axis = (
        f'<line x1="10" y1="{height - 2}" x2="{10 + total * scale:.0f}" y2="{height - 2}" stroke="#888"/>'
        f'<text x="{10 + total * scale:.0f}" y="{height - 6}" font-size="10" fill="#888" text-anchor="end">'
        f"{total * 1000:.0f}ms total</text>"
    )
    return f'<svg width="{width}" height="{height + 12}" font-family="ui-monospace,monospace">{"".join(rows)}{axis}</svg>'


def svg_bars(spans: Sequence[Span], width: int = 1080) -> str:
    rows = bottlenecks(spans)
    if not rows:
        return "<p>no operations</p>"
    bar_max = max(r.self_mean_ms for r in rows) or 1.0
    label_w, track_w, row_h = 220, width - 520, 26
    out: List[str] = []
    for i, r in enumerate(rows):
        y = 8 + i * (row_h + 6)
        bw = track_w * r.self_mean_ms / bar_max
        out.append(f'<text x="6" y="{y + 17}" font-size="12" fill="#222">{html.escape(r.name)}</text>')
        out.append(f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{row_h}" fill="{_colour(r.name)}" rx="3"/>')
        wx = label_w + track_w * r.self_p95_ms / bar_max
        out.append(
            f'<line x1="{wx:.1f}" y1="{y + 2}" x2="{wx:.1f}" y2="{y + row_h - 2}" stroke="#c0392b" stroke-width="2">'
            f"<title>p95 {r.self_p95_ms:.0f}ms</title></line>"
        )
        stat = f"{r.self_mean_ms:.0f}ms mean · {r.share_pct:.0f}% · p95 {r.self_p95_ms:.0f}"
        out.append(f'<text x="{label_w + track_w + 10}" y="{y + 17}" font-size="11" fill="#444">{html.escape(stat)}</text>')
    height = 16 + len(rows) * (row_h + 6)
    return f'<svg width="{width}" height="{height}" font-family="ui-monospace,monospace">{"".join(out)}</svg>'


def _usl_panel(spans: Sequence[Span], operation: Optional[str]) -> str:
    rows = bottlenecks(spans)
    target = operation or (rows[0].name if rows else None)
    if not target:
        return "<p>no operation to fit.</p>"
    buckets = concurrency_series(spans, target)
    if len(buckets) < 3:
        levels = sorted({b.concurrency for b in buckets})
        return (
            f"<p><b>{html.escape(target)}</b>: not enough concurrency spread to fit a USL curve "
            f"(levels seen: {levels or 'none'}). The trace needs ≥3 distinct concurrency levels.</p>"
        )
    fit = fit_usl(series_observations(buckets))
    series = "".join(
        f"<tr><td>N={b.concurrency}</td><td>{b.throughput:.3f}/s</td>"
        f"<td>{b.mean_latency_s * 1000:.0f}ms</td><td>n={b.sample_count}</td></tr>"
        for b in buckets
    )
    if not fit:
        return f"<p>{html.escape(target)}: fit failed.</p>"
    n_max = "∞" if fit.n_max == float("inf") else f"{fit.n_max:.1f}"
    flag = " <span style='color:#c0392b'>[exact fit — unreliable]</span>" if fit.exact_fit else ""
    return (
        f"<p><b>USL for {html.escape(target)}</b> (concurrency inferred from span overlap){flag}</p>"
        f"<p class='big'>σ={fit.sigma:.3f} · κ={fit.kappa:.5f} · "
        f"N<sub>max</sub>={n_max} · X<sub>max</sub>={fit.x_max:.3f}/s · "
        f"<b>{html.escape(fit.shape)}</b> · R²={fit.r2:.3f}</p>"
        f"<table>{series}</table>"
    )


def html_report(spans: Sequence[Span], *, operation: Optional[str] = None, title: str = "universcale") -> str:
    rep = _representative_request(spans)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:24px;color:#222;background:#fafafa}}
h1{{font-size:18px}}h2{{font-size:14px;margin-top:26px;color:#444}}
.panel{{background:#fff;border:1px solid #e3e3e3;border-radius:6px;padding:12px 16px;margin-top:8px}}
.big{{font-size:15px}}table{{border-collapse:collapse;margin-top:6px;font-size:12px}}
td{{padding:1px 14px 1px 0;color:#555}}svg{{background:#fff;border:1px solid #e3e3e3;border-radius:6px;padding:8px;margin-top:8px}}
sub{{font-size:0.75em}}</style></head>
<body><h1>{html.escape(title)}</h1>
<h2>Scaling (USL)</h2><div class="panel">{_usl_panel(spans, operation)}</div>
<h2>Flame — where time goes in a representative request</h2>{svg_flame(rep)}
<h2>Bottlenecks (self time; bar = mean, red tick = p95)</h2>{svg_bars(spans)}
</body></html>"""
