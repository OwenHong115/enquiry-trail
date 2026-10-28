"""In-worker fault matching and consumption (P6, A31).

The worker reads demo_faults in-process; there is no separate fault
service. Claim identity: "<job_id>:<provider>:<operation>" scoped by
UNIQUE(scenario_run_id, claim_identity) on fault_claims, so a retry
of the same job and operation replays the first decision without
consuming another use. If the matcher itself cannot read, no
injection happens, the run is marked inconclusive, and business work
continues unblocked: check() never raises.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from app.ports.crm import ProviderError

_BEFORE_WRITE = {
    "http_503_before_write": ProviderError("unavailable", http_status=503),
    "http_401_before_write": ProviderError("auth_failed", http_status=401),
    "http_429_before_write": ProviderError("rate_limited", http_status=429, retry_after_seconds=7),
    "invalid_ai_response": ProviderError("invalid_response"),
}


def claim_identity(*, job_id: uuid.UUID, provider: str, operation: str) -> str:
    return f"{job_id}:{provider}:{operation}"


def mode_to_provider_error(mode: str) -> ProviderError | None:
    return _BEFORE_WRITE.get(mode)


class FaultMatcher:
    def __init__(self, reader=None, claimer=None, on_triggered=None,
                 on_director_unavailable=None) -> None:
        self._reader = reader
        self._claimer = claimer
        self._on_triggered = on_triggered
        self._on_director_unavailable = on_director_unavailable

    def check(self, session: Any, *, scenario_run_id: uuid.UUID,
              job_id: uuid.UUID, provider: str, operation: str) -> str | None:
        identity = claim_identity(job_id=job_id, provider=provider, operation=operation)
        try:
            if self._reader is not None:
                fault = self._reader(session, scenario_run_id, provider)
            else:
                from app.db import fault_repo
                fault = fault_repo.find_armed_fault(session, scenario_run_id, provider)
            if fault is None:
                return None
            if self._claimer is not None:
                mode, consumed = self._claimer(session, scenario_run_id, fault, identity)
            else:
                from app.db import fault_repo
                mode, consumed = fault_repo.try_claim(
                    session, run_id=scenario_run_id, fault=fault, claim_identity=identity)
            if consumed and self._on_triggered is not None:
                self._on_triggered(session, scenario_run_id, mode=mode, fault=fault)
            return mode
        except Exception:
            # Director unavailable: never wedge the queue.
            try:
                session.rollback()
            except Exception:
                pass
            try:
                if self._on_director_unavailable is not None:
                    self._on_director_unavailable(session, scenario_run_id)
                else:
                    from app.db import fault_repo
                    # Own transaction so the mark survives the failure.
                    with session.begin():
                        fault_repo.mark_inconclusive(session, scenario_run_id)
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass
            return None
