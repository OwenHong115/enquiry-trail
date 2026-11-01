#!/usr/bin/env python3
"""Performance evidence sampler (plan targets: intake p95, ready p95).

Runs against a running stack. Python (stdlib only) is used instead of
PowerShell so the sampler runs identically on Windows and CI without
extra dependencies; the repo already requires Python 3.12 for tests.

Protocol (plan field shape):
  warmup: 10 intakes (discarded), then N=100 intakes at concurrency 5.
  For each intake: intake latency (POST /api/v1/intake/form) and
  time-to-ready (intake start until the enquiry detail first reports
  processing_summary == "ready", polled every 200 ms, 120 s cap).

Report fields (verification.yaml performance report_fields): CPU, RAM,
OS, commit, profile, N, errors, p50, p95, max. CPU includes model and
logical count; RAM is total physical memory via a stdlib-only method.
p95 uses the nearest-rank method. These are evidence samples from the
machine that ran them, not a production guarantee. Exit code is 0 when
both p95 targets are met and there were no measured errors, and 2 when
either p95 target is missed or errors > 0; failed samples are counted
in `errors` and never dropped from the report.

Environment:
  PERF_BASE_URL   default http://127.0.0.1:8080
  PERF_ORIGIN     Origin header to send (default: PERF_BASE_URL); set it to the
                  web origin when the API only allows that exact origin.
  PERF_USERNAME / PERF_PASSWORD (or E2E_USERNAME / E2E_PASSWORD)
  PERF_WARMUP=10  PERF_N=100  PERF_CONCURRENCY=5  PERF_COMMIT (optional)
  PERF_PROFILE    profile name reported in the report (default: mock)

CLI (backwards compatible; all flags optional, env vars still work):
  --profile       profile name (overrides PERF_PROFILE)
  --warmup / --n / --concurrency   override PERF_WARMUP / PERF_N /
                  PERF_CONCURRENCY
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid

BASE = os.environ.get("PERF_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
WARMUP = int(os.environ.get("PERF_WARMUP", "10"))
N = int(os.environ.get("PERF_N", "100"))
CONCURRENCY = int(os.environ.get("PERF_CONCURRENCY", "5"))
PROFILE = os.environ.get("PERF_PROFILE", "mock")
INTAKE_P95_TARGET_MS = 500.0
READY_P95_TARGET_MS = 10000.0


def _request(method, path, body=None, headers=None, cookie=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", os.environ.get("PERF_ORIGIN", BASE))
    if cookie:
        req.add_header("Cookie", cookie)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.headers, resp.read()


def _login():
    user = os.environ.get("PERF_USERNAME") or os.environ.get("E2E_USERNAME", "")
    password = os.environ.get("PERF_PASSWORD") or os.environ.get("E2E_PASSWORD", "")
    if not user or not password:
        raise SystemExit("PERF_USERNAME/PERF_PASSWORD (or E2E_USERNAME/E2E_PASSWORD) must be set")
    status, headers, raw = _request("POST", "/api/v1/auth/login", {"username": user, "password": password})
    if status != 200:
        raise SystemExit(f"login failed: HTTP {status}")
    body = json.loads(raw)
    cookie = headers.get("Set-Cookie", "").split(";")[0]
    return cookie, body["csrf_token"]


def _one(cookie, csrf, index):
    payload = {
        "first_name": "Perf", "last_name": "Sample",
        "email": f"perf-{index}-{uuid.uuid4().hex[:8]}@example.com",
        "phone": "+61412345678", "postcode": "2000", "service_type": "bathroom",
        "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
        "preferred_contact": "email", "acknowledge_demo": True,
    }
    start = time.perf_counter()
    status, _headers, raw = _request(
        "POST", "/api/v1/intake/form", payload,
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRF-Token": csrf}, cookie=cookie)
    intake_s = time.perf_counter() - start
    if status not in (200, 201, 202):
        raise RuntimeError(f"intake HTTP {status}: {raw[:200]!r}")
    enquiry_id = json.loads(raw)["enquiry_id"]
    deadline = start + 120
    ready_s = None
    while time.perf_counter() < deadline:
        _s, _h, detail_raw = _request("GET", f"/api/v1/enquiries/{enquiry_id}", cookie=cookie)
        if json.loads(detail_raw).get("processing_summary") == "ready":
            ready_s = time.perf_counter() - start
            break
        time.sleep(0.2)
    if ready_s is None:
        raise RuntimeError(f"enquiry {enquiry_id} did not reach ready within 120s")
    return intake_s * 1000, ready_s * 1000


def _percentile_nearest_rank(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(p / 100 * len(ordered))
    index = min(len(ordered) - 1, max(0, rank - 1))
    return ordered[index]


def _stats(values):
    if not values:
        return None, None, None
    return statistics.median(values), _percentile_nearest_rank(values, 95), max(values)


def _evaluate(intake_p95, ready_p95, errors):
    """Return 0 only when both p95 targets are met and errors == 0, else 2."""
    if errors:
        return 2
    if intake_p95 is None or ready_p95 is None:
        return 2
    if intake_p95 > INTAKE_P95_TARGET_MS or ready_p95 > READY_P95_TARGET_MS:
        return 2
    return 0


def _commit():
    if os.environ.get("PERF_COMMIT"):
        return os.environ["PERF_COMMIT"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _cpu_model():
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.lower().startswith("model name") and ":" in line:
                        model = line.split(":", 1)[1].strip()
                        if model:
                            return model
        except Exception:
            pass
    return platform.processor() or platform.machine() or "unknown"


def _ram_total():
    if platform.system() == "Windows":
        try:
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            mem = _Mem()
            mem.dwLength = ctypes.sizeof(_Mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):  # type: ignore[attr-defined]
                return f"{mem.ullTotalPhys / 2**30:.1f} GiB"
        except Exception:
            pass
        return "unknown"
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        return f"{pages * size / 2**30:.1f} GiB"
    except Exception:
        return "unknown"


def _host_info():
    model = _cpu_model()
    count = os.cpu_count()
    cpu = f"{model} ({count} logical CPUs)" if count else model
    return cpu, _ram_total(), f"{platform.system()} {platform.release()} ({platform.version()})"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Performance evidence sampler (stdlib only).")
    parser.add_argument("--profile", default=None, help="profile name (default: PERF_PROFILE or mock)")
    parser.add_argument("--warmup", type=int, default=None, help="warmup count (default: PERF_WARMUP or 10)")
    parser.add_argument("--n", type=int, default=None, help="measured count (default: PERF_N or 100)")
    parser.add_argument("--concurrency", type=int, default=None, help="concurrency (default: PERF_CONCURRENCY or 5)")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    profile = args.profile if args.profile is not None else PROFILE
    warmup = args.warmup if args.warmup is not None else WARMUP
    n = args.n if args.n is not None else N
    concurrency = args.concurrency if args.concurrency is not None else CONCURRENCY
    cookie, csrf = _login()
    counter = [0]

    def run_batch(count):
        results, errors = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = []
            for _ in range(count):
                counter[0] += 1
                futures.append(pool.submit(_one, cookie, csrf, counter[0]))
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as err:  # collected, reported, never hidden
                    errors.append(str(err))
        return results, errors

    warm, warm_errors = run_batch(warmup)
    print(f"warmup: {len(warm)} ok, {len(warm_errors)} failed")
    results, errors = run_batch(n)
    print(f"measured: {len(results)} ok, {len(errors)} failed of N={n} at concurrency={concurrency}")
    if errors:
        print(f"first error: {errors[0]}")
    intake = [r[0] for r in results]
    ready = [r[1] for r in results]
    intake_p50, intake_p95, intake_max = _stats(intake)
    ready_p50, ready_p95, ready_max = _stats(ready)
    cpu, ram, os_name = _host_info()
    print(f"CPU: {cpu}")
    print(f"RAM: {ram}")
    print(f"OS: {os_name}")
    print(f"commit: {_commit()}")
    print(f"profile: {profile}")
    print(f"N: {n}")
    print(f"errors: {len(errors)}")

    def fmt(value):
        return f"{value:.1f}" if value is not None else "n/a"

    print(f"intake_ms: p50={fmt(intake_p50)} p95={fmt(intake_p95)} max={fmt(intake_max)}")
    print(f"time_to_ready_ms: p50={fmt(ready_p50)} p95={fmt(ready_p95)} max={fmt(ready_max)}")
    print("targets: intake p95 <= 500ms, time-to-ready p95 <= 10000ms (plan targets; local sample only)")
    return _evaluate(intake_p95, ready_p95, len(errors))


if __name__ == "__main__":
    sys.exit(main())
