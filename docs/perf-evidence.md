# Performance evidence

Local evidence sample only. These numbers describe one run on one
developer machine against the isolated local Compose stack. They are
not a production guarantee and do not sign off any acceptance item.

## Run identity

- Branch: `feat/e2e-perf-small-gaps`
- Application commit at measurement time: `57129da4a0c290cdbca100c039c75f58d06f3f8a`
  (the sampler's Origin-header fix in `bbb71bb` changed only the
  measurement script, not application code)
- Script: `scripts/perf_measure.py` (Python, standard library only;
  chosen over PowerShell so the sampler runs identically on Windows
  and CI with no extra dependencies)
- Stack: isolated Compose project `wt14e2e` (db on local port 55433
  via a temporary, uncommitted port override because the normal dev
  database already held 55432; dev services were not touched),
  seeded operator `demo-operator`, credentials from the environment only
- Protocol: warmup 10 intakes, then N=100 intakes at concurrency 5;
  intake latency is the `POST /api/v1/intake/form` duration and
  time-to-ready is intake start until the enquiry detail first reports
  `ready`, polled every 200 ms
- Environment printed by the sampler: CPU Intel64 Family 6 Model 183
  Stepping 1, GenuineIntel; RAM 31.7 GiB; OS Windows 11 (10.0.26200)

## Observed output (verbatim)

```text
warmup: 10 ok, 0 failed
measured: 100 ok, 0 failed of N=100 at concurrency=5
CPU: Intel64 Family 6 Model 183 Stepping 1, GenuineIntel
RAM: 31.7 GiB
OS: Windows 11 (10.0.26200)
commit: 57129da4a0c290cdbca100c039c75f58d06f3f8a
intake_ms: p50=46.0 p95=84.6 max=91.2
time_to_ready_ms: p50=1040.6 p95=1259.7 max=1280.1
targets: intake p95 <= 500ms, time-to-ready p95 <= 10000ms (plan targets; local sample only)
```

Plan targets for comparison: intake p95 500 ms, time-to-ready p95
10 s. The observed local sample is within both targets. Re-run the
script against a production-like environment before treating the
targets as demonstrated there.

## Sampler report format

The sampler prints every `verification.yaml` performance report field:
`CPU` (model and logical count), `RAM` (total, stdlib-only), `OS`,
`commit`, `profile` (default `mock`, from `--profile` or
`PERF_PROFILE`), `N`, `errors`, and `p50`/`p95`/`max` for both series
(p95 nearest-rank). Failed measured samples are counted in `errors`
and the full report is still printed. The exit code is 0 only when
both p95 targets are met and `errors` is 0; it is 2 when either p95
target is missed or `errors` > 0. The verbatim sample above predates
the `profile`/`N`/`errors` lines and is left unchanged; no new
measurements are recorded here.
