# Acceptance log (operator record)

This log maps every acceptance item (A01-A33) and contract check
(C01/C02) to the automated evidence that exists in this repository,
the commit that delivered that evidence, and the result observed when
the cited suites were run in this worktree. It does not sign off any
item. Every verdict is `PENDING OPERATOR` until the operator records
their own decision, date, and signature outside the automated suites.

## Run record (observed in this worktree)

- Worktree: `.worktrees/wt15` on branch `docs/final-truth-closeout`.
  Measured at base `a305db162285f5c65e1468ca563480b900587dac` on
  2026-11-01: local `main` was at that same commit and was 0 behind /
  26 ahead of `origin/main`
  (`5872be3a1a7a0ab6754ea4fea472d53beea3d39a`;
  `git rev-list --left-right --count origin/main...a305db1` ->
  `0 26`). This docs branch and subsequent local merges stay
  unpushed until the final push. Last re-verified 2026-11-01.
- Backend unit: from `.worktrees/wt15/backend`, interpreter
  `backend/.venv/Scripts/python.exe` (main-tree venv; wt15 has no own
  venv) with `-m pytest tests/unit -q` -> **156 passed** in 9.87s.
  Collect-only (`--collect-only -q`) also reports 156.
- Backend integration: same interpreter with
  `DATABASE_URL=postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops`
  and the compose `db` service from the main checkout
  (`docker compose up -d db`, throwaway compose-only environment
  values supplied inline, never written to a tracked file)
  -> **270 passed, 1 warning** in 233.88s (the known
  Starlette/httpx deprecation warning only). Collect-only reports
  270. Unit and integration are collected and run separately,
  matching `.github/workflows/ci.yml`: collecting both in one
  invocation fails on duplicate test-module basenames
  (`test_extraction.py`, `test_fault_demo.py` exist in both suites).
- Ruff: `ruff check app tests` is clean after `37cfa17` (merged as
  `1a63d02`). Verified in this worktree after merging `main`: PATH
  `ruff` 0.15.18 run as `ruff check app tests` from
  `.worktrees/wt15/backend` reports `All checks passed!` (exit 0).
  (`python -m ruff` in the main-tree venv reports
  `No module named ruff`.) The earlier F401
  (`timedelta imported but unused` at
  `backend/tests/integration/test_api_auth.py`) is fixed by that
  merge. H03 still OPEN only because no GitHub Actions run has been
  observed (see `docs/checklist.md`).
- Frontend: `npm ci` in `frontend/`, then `npm run test` ->
  **2 test files passed, 14 tests passed**; `npm run build` passed
  (`tsc -b && vite build`); `npm run lint` (ESLint) exited 0.
- Playwright (A28): not re-run in this worktree. The cited evidence is
  the recorded run in `docs/e2e-browser-evidence.md` (5 passed,
  0 failed, 0 skipped, real Chromium against the real stack on
  branch `feat/e2e-browser-verification`, test commit `1b3d3df`).
  That document is cited as a document, not as a run observed here.
- Live suite (`backend/tests/live/`): not run. It is opt-in
  (`--run-live` / `RUN_LIVE=1`) and needs a dedicated real account.
  Connected mode remains UNVERIFIED (see `docs/connected-handover.md`).

Result column meanings: a suite count above means the cited test was
part of a green run observed here. `NO AUTOMATED EVIDENCE` means no
test in this repository asserts the item. `PARTIAL` names the exact
part that is not asserted.

## A01-A33 and C01/C02

| ID | Item (short title) | Automated evidence (verified by reading the test) | Delivering commit | Observed automated result | Verdict |
|---|---|---|---|---|---|
| A01 | Normal intake | `backend/tests/integration/test_s2_intake.py::test_a01_submit_persists_receipt_contact_enquiry_and_task` (one receipt/contact/enquiry/task, owner maya, rules hash, canonical hashes, BusinessClock instants, prepare job and enquiry.received event); detail shape also in `backend/tests/integration/test_api_detail.py::test_ready_detail_matches_fixture_shape` | `0de4ed9` | Integration suite green (270 passed) | PENDING OPERATOR |
| A02 | Same key concurrent | `backend/tests/integration/test_s3_identity.py::test_a02_same_key_concurrent_submits_persist_once` (20-thread barrier, one persisted set) | `19f0c5e` | Integration suite green (270 passed) | PENDING OPERATOR |
| A03 | Key payload conflict | `backend/tests/integration/test_s3_identity.py::test_a03_same_key_changed_payload_conflicts`; HTTP 409 mapping in `backend/tests/integration/test_api_intake.py::test_same_key_changed_message_conflicts` | `19f0c5e` | Integration suite green (270 passed) | PENDING OPERATOR |
| A04 | Content window | `backend/tests/integration/test_s3_identity.py::test_a04_content_window_anchors_on_first_receipt`, `test_a04_anchor_resets_per_canonical_chain`, `test_a04_content_lock_blocks_concurrent_duplicate` (600s window anchored on first receipt) | `8aac888` | Integration suite green (270 passed) | PENDING OPERATOR |
| A05 | Returning customer | `backend/tests/integration/test_s3_identity.py::test_a05_returning_customer_reuses_contact` | `19f0c5e` | Integration suite green (270 passed) | PENDING OPERATOR |
| A06 | No unsafe merge | `backend/tests/integration/test_s3_identity.py::test_a06_same_phone_distinct_emails_do_not_merge`, `test_a06_same_name_distinct_emails_do_not_merge` | `19f0c5e` | Integration suite green (270 passed) | PENDING OPERATOR |
| A07 | Normalization | `backend/tests/unit/test_normalization.py` (email whitespace/tag forms, AU phone forms, invalid phone rejected); HTTP 422 mapping in `backend/tests/integration/test_api_intake.py::test_invalid_phone_is_domain_422` | `fcc9474` | Unit suite green (156 passed) and integration suite green (270 passed) | PENDING OPERATOR |
| A08 | Review and route | `backend/tests/integration/test_s4_review_task.py::test_a08_review_resolution`; `backend/tests/integration/test_api_actions.py::test_review_resolve_returns_detail_and_persists` | See git history for `test_s4_review_task.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A09 | Routing precedence | `backend/tests/unit/test_routing.py::test_routing_first_match_table`, `test_routing_config_hash_matches_authority`; transcription pin in `backend/tests/integration/test_s2_intake.py::test_routing_transcription_matches_authority_hash` | `fcc9474` | Unit suite green (156 passed) and integration suite green (270 passed) | PENDING OPERATOR |
| A10 | Preserve CRM fields | `backend/tests/integration/test_s5_crm.py::test_a10_preserves_nonblank_remote_fields`, `test_a10_capability_off_still_syncs_without_patch` (masked preservation) | `b04b863` | Integration suite green (270 passed) | PENDING OPERATOR |
| A11 | 503 before write | `backend/tests/integration/test_s6a_retry.py::test_a11_retryable_failures_backoff_then_succeed` | See git history for `test_s6a_retry.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A12 | Lost success response | `backend/tests/integration/test_s6b_unknown.py::test_a12_lost_create_response_reconciles_by_readback` (one remote contact, link set, causal event order) | See git history for `test_s6b_unknown.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A13 | Unknown patch / unresolved lookup (attention status literal is `needs_intervention` in the read model; no code produces `intervention_required` - verified by search over `backend/app` and `backend/tests` on 2026-11-01; the contract package itself lives outside this repository, see C01/C02) | `backend/tests/integration/test_closeout_regressions.py::test_readback_notfound_ladder_absolute_instants` (T+1/T+5/T+21); `backend/tests/integration/test_r2_recovery.py` reconciliation cases | See git history for `test_closeout_regressions.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A14 | Crash after write | `backend/tests/integration/test_fault_demo.py::test_crash_after_remote_write_recovers_by_readback` (PARTIAL: armed in-process hook and lease expiry, not a real child-process kill) | `567c43a` | PARTIAL - integration suite green (270 passed); the evidence is the job-lease crash path (armed in-process hook plus lease expiry and readback recovery), not a Compose worker kill; the real process-exit half is A26. Last re-verified 2026-11-01 | PENDING OPERATOR |
| A15 | Stale worker fencing | `backend/tests/integration/test_r8_lease_fencing.py::test_stale_worker_must_not_finalize_a_success`, `test_stale_worker_must_not_reschedule_or_block_new_owner` | See git history for `test_r8_lease_fencing.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A16 | Auth block/resume | `backend/tests/integration/test_s6c_blocked.py::test_a16_auth_failure_blocks_provider`, `test_a16_no_writes_while_blocked`, `test_a16_failed_probe_keeps_blocking`, `test_a16_successful_probe_resumes_and_requeues` | `94285cb` | Integration suite green (270 passed) | PENDING OPERATOR |
| A17 | Retry-After | `backend/tests/integration/test_s6a_retry.py::test_a17_retry_after_defers_next_attempt_exactly` | See git history for `test_s6a_retry.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A18 | Permanent failure/manual retry | `backend/tests/integration/test_r16_operator_retry.py::test_dead_job_is_reopened_with_one_audit_event`, `test_stale_retry_is_a_clean_conflict_not_a_second_cycle`, `test_recovery_follows_the_unresolved_operation` | See git history for `test_r16_operator_retry.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A19 | Ordinary notification idempotency | `backend/tests/integration/test_slack_notifications.py::test_a19_one_ready_one_notification_one_message_one_event`, `test_http_sim_a19_sends_exactly_one_message`, `test_a19_duplicate_idempotency_key_intake_sends_exactly_once`; unit: `backend/tests/unit/test_slack_notifications_unit.py` (11 tests), `backend/tests/unit/test_slack_channel_wiring.py` (3 tests). Slack files total 25 collected tests (11 integration + 14 unit). Last re-verified 2026-11-01 | `ab2e4f8` | Integration suite green (270 passed) and unit suite green (156 passed) | PENDING OPERATOR |
| A20 | Slack delivery unknown/failure | `backend/tests/integration/test_slack_notifications.py::test_a20_lost_success_is_delivery_unknown_no_resend`, `test_a20_definite_failure_records_failed`, `test_http_sim_a20_drop_success_is_unknown_one_message`, `test_http_sim_definite_auth_failure_is_failed`, `test_retryable_failure_uses_job_retry_then_succeeds`. Last re-verified 2026-11-01 | `ab2e4f8` | Integration suite green (270 passed) | PENDING OPERATOR |
| A21 | AI validation/fallback | `backend/tests/unit/test_extraction.py` (valid fields kept, invalid budget/evidence dropped to null); `backend/tests/integration/test_extraction.py::test_valid_extraction_persisted_with_snapshot`, `test_invalid_budget_and_evidence_dropped_to_null`, `test_extractor_failure_falls_back_and_still_prepares` | `eca2d0b` | Unit suite green (156 passed) and integration suite green (270 passed) | PENDING OPERATOR |
| A22 | Untrusted text | `frontend/tests/app.test.tsx` escaped-rendering cases; backend intake stores message as data (see `backend/tests/integration/test_api_intake.py`) (PARTIAL: no dedicated malicious-HTML end-to-end case was identified) | `3770890` | PARTIAL - frontend suite green (14 passed) and integration suite green (270 passed); S8 reviewer basis is the escaped-rendering cases in `frontend/tests/app.test.tsx` plus intake storing the message as data; a dedicated malicious-HTML end-to-end injection case is still not asserted. Last re-verified 2026-11-01 | PENDING OPERATOR |
| A23 | Independent versions | `backend/tests/integration/test_owner_drafts.py::test_draft_save_and_owner_change_do_not_conflict`, `test_owner_change_race_exactly_one_succeeds` | `b722df5` | Integration suite green (270 passed) | PENDING OPERATOR |
| A24 | Terminal task idempotency | `backend/tests/integration/test_s4_review_task.py::test_a24_complete_is_terminal_and_repeatable`, `test_a24_cancel_is_terminal_and_repeatable`; HTTP repeat/opposite-action cases in `backend/tests/integration/test_api_actions.py` | See git history for `test_s4_review_task.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| A25 | Intake DB rollback | `backend/tests/integration/test_s2_intake.py::test_a25_failed_task_insert_leaves_no_partial_intake` (atomicity); API-level 503 mapping now implemented via `IntakeUnavailableError` in `backend/app/services/intake.py` (raised on database failure) and asserted by `backend/tests/integration/test_a25_intake_503.py::test_a25_database_failure_is_503_atomic_and_retryable`, `test_a25_non_database_error_still_returns_500` plus `backend/tests/unit/test_a25_intake_unavailable.py` (targeted run: 5 passed). Last re-verified 2026-11-01 | `7128f3d` | Integration suite green (270 passed) and unit suite green (156 passed) | PENDING OPERATOR |
| A26 | Actual process exit | None for the compose-kill form. Closest evidence is worker recovery in `backend/tests/integration/test_p1b_worker.py`, the A14 hook case, and the operator script `scripts/a26-process-recovery.ps1`; none of these is an automated Compose worker-kill test in the suite. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| A27 | Security and limits | `backend/tests/integration/test_p3_ingress.py` (Origin, body bound), `backend/tests/integration/test_p3_ratelimit_webhook.py` (429 envelope, spoofed X-Forwarded-For ignored, webhook signature/replay), `backend/tests/unit/test_p3_ingress_unit.py` (proxy handling) | `8101bce` | Integration suite green (270 passed) and unit suite green (156 passed) | PENDING OPERATOR |
| A28 | UI and polling | `frontend/tests/app.test.tsx` polling-visibility cases; `e2e/core-ui.spec.ts` (labelled sign-in, quote-to-task flow, hidden polling pause, draft retained on 409) | `1b3d3df` | Frontend suite green here (14 passed); Playwright 5 passed as recorded in `docs/e2e-browser-evidence.md` (cited document, not re-run here) | PENDING OPERATOR |
| A29 | Illegal config | `backend/tests/unit/test_connected_pinning.py` (http mode without base URL/token fails startup; connected extraction without model fails startup; no fixture fallback); `backend/tests/unit/test_crm_settings.py` | `347a954` | Unit suite green (156 passed) | PENDING OPERATOR |
| A30 | Live provider smoke | `backend/tests/live/test_live_smoke.py::test_live_create_read_patch_and_repeat_create` exists but is opt-in and was not run; no real account or credentials in this delivery. Worker/heartbeat read endpoints exist (`GET /api/v1/health/workers`, heartbeat rows) as supporting infrastructure, not as live-provider evidence. No `verify.py` exists in this repository (checked 2026-11-01). Connected mode is UNVERIFIED per `docs/connected-handover.md` (M0 O01-O04 open). Last re-verified 2026-11-01 | `347a954` | NO AUTOMATED EVIDENCE (live run not performed) | PENDING OPERATOR |
| A31 | Fault matcher unavailable | `backend/tests/unit/test_fault_demo.py::test_director_unavailable_never_raises_and_reports_inconclusive`; `backend/tests/integration/test_fault_demo.py::test_director_unavailable_marks_inconclusive`, `test_scenario_lookup_failure_leaves_job_handled`, `test_matcher_consumes_once_and_replay_is_safe` | `567c43a` | Unit suite green (156 passed) and integration suite green (270 passed) | PENDING OPERATOR |
| A32 | Contact version coordination | `backend/tests/integration/test_a32_contact_version.py` (7 tests: contact/version update, rearm with older enquiry ready, unknown stays readback-first, mid-sync race, changed-data new enquiry with remote patch, unsupported patch does not advance link, finalization serialization) | `efdc07d` | Integration suite green (270 passed) | PENDING OPERATOR |
| A33 | Timeline cursor commit safety | `backend/tests/integration/test_a33_timeline.py::test_timeline_order_shape_and_audit_only_excluded`, `test_timeline_cursor_pagination_exactly_once`, `test_timeline_commit_safety_two_transactions` | See git history for `test_a33_timeline.py` | Integration suite green (270 passed) | PENDING OPERATOR |
| C01 | Enum/vocabulary consistency | None in this repository. The contract package is outside this repository and is not vendored, by hard boundary; C01 cannot be verified inside this repo. Verification requires the operator to run the contract package against the backend. Section ids cited only; no contract text copied. Last re-verified 2026-11-01 | None | NO AUTOMATED EVIDENCE (excluded by boundary; operator-run only) | PENDING OPERATOR |
| C02 | Fixtures validate against schema | None in this repository. The contract package is outside this repository and is not vendored, by hard boundary; C02 cannot be verified inside this repo. Verification requires the operator to run the contract package against the backend. The six detail-state shapes are covered behaviourally by API detail tests, but no fixture-validation run exists here. Last re-verified 2026-11-01 | None | NO AUTOMATED EVIDENCE (excluded by boundary; operator-run only) | PENDING OPERATOR |

## Coverage summary (automated evidence only, not acceptance)

- Items with automated evidence observed green here: A01-A13,
  A15-A21, A23-A25, A27-A29, A31-A33 (29 items), plus A28
  frontend evidence here with Playwright cited from
  `docs/e2e-browser-evidence.md`. Counts behind this summary:
  backend unit 156 passed, backend integration 270 passed,
  frontend 14 passed (all observed 2026-11-01 at base `a305db1`).
- Partial: A14 (job-lease crash evidence, no real process kill),
  A22 (no dedicated injection case).
- No automated evidence: A26, A30, C01, C02. C01/C02 are excluded
  by the hard boundary above (contract package outside this repo,
  operator-run only). A30 stays unverified: M0 O01-O04 have no
  operator evidence (see `docs/connected-handover.md`).
- Performance: `docs/perf-evidence.md` records a LOCAL SAMPLE ONLY
  (N=100, concurrency 5, isolated local stack): intake p95 84.6 ms,
  time-to-ready p95 1259.7 ms against plan targets of 500 ms and
  10 s. It is not a load-test certification. Verdict for any
  performance acceptance reading stays PENDING OPERATOR.
  Last re-verified 2026-11-01.
- Every verdict above is `PENDING OPERATOR`. No item is signed,
  dated, or accepted on the operator's behalf in this file.
