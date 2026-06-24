"""``universcale`` command-line entry point.

    universcale trace.json                 # bottlenecks + USL of the top operation
    universcale trace.json --operation vlm_extraction
    universcale trace.json --json          # machine-readable
    universcale trace.json --trace-out normalized.json   # re-emit for Perfetto
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

from .htmlview import html_report
from .report import report_dict, text_report, to_chrome_trace
from .spans import load_spans


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="universcale", description="USL scaling analysis over traces.")
    parser.add_argument("trace", type=Path, help="Trace file: OTLP JSON, Chrome trace JSON, or JSONL records.")
    parser.add_argument("--operation", help="Operation/span name to fit the USL for (default: top bottleneck).")
    parser.add_argument("--top", type=int, default=12, help="How many operations to rank.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--trace-out", type=Path, help="Write normalized Chrome trace (open in ui.perfetto.dev).")
    parser.add_argument("--html", type=Path, help="Write a self-contained HTML report (USL + flame + bottlenecks).")
    parser.add_argument("--open", action="store_true", help="Open the HTML report in a browser (implies --html).")
    args = parser.parse_args(argv)

    try:
        spans = load_spans(args.trace)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not spans:
        print("no spans found in trace", file=sys.stderr)
        return 1

    if args.trace_out:
        args.trace_out.write_text(json.dumps(to_chrome_trace(spans)), encoding="utf-8")
        print(f"wrote {args.trace_out}  (open at https://ui.perfetto.dev)")

    html_path = args.html or (Path("universcale-report.html") if args.open else None)
    if html_path:
        html_path.write_text(html_report(spans, operation=args.operation, title=f"universcale — {args.trace.name}"), encoding="utf-8")
        print(f"wrote {html_path}")
        if args.open:
            webbrowser.open(html_path.resolve().as_uri())

    if args.json:
        print(json.dumps(report_dict(spans, operation=args.operation, top=args.top), indent=2, default=str))
    else:
        print(text_report(spans, operation=args.operation, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
