# universcale

Universal Scalability Law tooling for fitting and explaining capacity curves —
**from the traces you already have**.

```bash
uv add universcale
```

Zero dependencies. Pure standard library. Runs in CI, in a locked-down VM, or
inside another package with no install friction.

## Why

OpenTelemetry (and friends) already instrument your code and visualise spans.
What they *don't* tell you is the thing you actually need for capacity planning:

- **σ (contention)** — the serialized / queueing fraction of the work
- **κ (coherency)** — cross-talk cost; when positive, throughput *peaks then declines*
- **N_max** — the concurrency that maximises throughput (the wall you hit)
- **where the wall-clock actually goes** — ranked by self-time, not inclusive time

`universcale` is the analysis layer that turns spans into those numbers.

## Two ways in

**1. You already have a load sweep** — `(concurrency, throughput)` pairs:

```python
from universcale import fit_usl

fit = fit_usl([(1, 0.72), (2, 0.80), (4, 0.78), (8, 0.80), (16, 0.80)])
print(fit.shape, fit.n_max, fit.x_max)   # 'retrograde' 8.9 0.80
```

**2. You only have a trace** — and no patience for a bespoke load test.
`universcale` infers the concurrency curve from span *overlap* via Little's Law
(`X = N / R`), so a normal production trace reveals the ceiling:

```python
from universcale import load_spans, concurrency_series, series_observations, fit_usl

spans = load_spans("trace.json")                       # OTLP / Chrome / JSONL
buckets = concurrency_series(spans, "vlm_extraction")  # N inferred from overlap
fit = fit_usl(series_observations(buckets))
```

No load harness. The trace *is* the experiment.

## CLI

```bash
universcale trace.json                       # bottlenecks + USL of the top op
universcale trace.json --operation vlm_extraction
universcale trace.json --json                # machine-readable (for CI gates)
universcale trace.json --trace-out norm.json # re-emit for ui.perfetto.dev
```

```
=== where time goes (self time) ===
  operation              share   self_p50   self_p95   count
  vlm_extraction         62.0%    1638.3m    1951.9m       8  ############
  pipeline               26.3%     391.3m    1606.5m       8  #####
  render_pages            7.5%     200.1m     258.6m       8  ##

=== USL scaling for 'vlm_extraction' (concurrency inferred from span overlap) ===
  sigma=0.889  kappa=0.00140  gamma=0.731/s  R^2=0.818
  shape=retrograde   N_max=8.9 concurrent   X_max=0.801/s
```

## Visualisation

We don't reinvent a flame UI. `--trace-out` (and `universcale.report.to_chrome_trace`)
emit standard Chrome Trace Event JSON, which [ui.perfetto.dev](https://ui.perfetto.dev)
and `chrome://tracing` render as an interactive, zoomable flame timeline.

## Inputs

Any of: OTLP trace JSON (OpenTelemetry file exporter), Chrome Trace Event JSON
(Perfetto / chrome://tracing), or JSONL records (`{name, start, duration}` or
`{stage, t_offset_ms, elapsed_ms}`). Bring your own format via `from_records`.

## Honesty by design

- **Overfit guard** — a 3-point fit is exactly determined (R²≡1); it's flagged `exact_fit`.
- **R² and dof** are reported so you can trust (or distrust) a fit.
- **Inclusive vs self time** — bottlenecks rank by self-time so parents don't mask children.
- **No fabricated curves** — without ≥3 distinct concurrency levels, it says so.

## Design

- `usl.py` — the model + a zero-dependency least-squares fit (Gaussian elimination).
- `spans.py` — the `Span` model + adapters (OTLP / Chrome / records).
- `concurrency.py` — infer N from span overlap (Little's Law).
- `bottlenecks.py` — self-time ranking.
- `report.py` / `cli.py` — text/JSON reports and Perfetto export.

The core math is pure Python and has been cross-checked to reproduce a numpy
least-squares fit to full printed precision on real capacity data.
