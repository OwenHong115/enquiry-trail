# Acceptance log (operator record)

This log maps every acceptance item (A01-A33) and contract check
(C01/C02) to the automated evidence that exists in this repository,
the commit that delivered that evidence, and the result observed when
the cited suites were run in this worktree. It does not sign off any
item. Every verdict is `PENDING OPERATOR` until the operator records
their own decision, date, and signature outside the automated suites.

## Run record (observed in this worktree)

- Worktree: `.worktrees/wt8` on branch `feat/acceptance-handover-pack`,
  based on `d5b6777` (merge of `feat/e2e-browser-verification`).
- Backend unit: from `backend/`, interpreter
  `..\..\..\backend\.venv\Scripts\python.exe -m pytest tests/unit -q`
  -> **136 passed** in 10.59s.
- Backend integration: same interpreter with
  `DATABASE_URL=postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops`
  and the compose `db` service from the main checkout
  (`docker compose up -d db`, throwaway compose-only environment
  values supplied inline, never written to a tracked file)
  -> **234 passed, 1 warning** in 230.50s (the known
  Starlette/httpx deprecation warning only).
- Frontend: `npm --prefix frontend ci` then
  `npm --prefix frontend run test` -> **2 test files passed,
  14 tests passed**.
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
| A01 | Normal intake | `backend/tests/integration/test_s2_intake.py::test_a01_submit_persists_receipt_contact_enquiry_and_task` (one receipt/contact/enquiry/task, owner maya, rules hash, canonical hashes, BusinessClock instants, prepare job and enquiry.received event); detail shape also in `backend/tests/integration/test_api_detail.py::test_ready_detail_matches_fixture_shape` | `0de4ed9` | Integration suite green (234 passed) | PENDING OPERATOR |
| A02 | Same key concurrent | `backend/tests/integration/test_s3_identity.py::test_a02_same_key_concurrent_submits_persist_once` (20-thread barrier, one persisted set) | `19f0c5e` | Integration suite green (234 passed) | PENDING OPERATOR |
| A03 | Key payload conflict | `backend/tests/integration/test_s3_identity.py::test_a03_same_key_changed_payload_conflicts`; HTTP 409 mapping in `backend/tests/integration/test_api_intake.py::test_same_key_changed_message_conflicts` | `19f0c5e` | Integration suite green (234 passed) | PENDING OPERATOR |
| A04 | Content window | `backend/tests/integration/test_s3_identity.py::test_a04_content_window_anchors_on_first_receipt`, `test_a04_anchor_resets_per_canonical_chain`, `test_a04_content_lock_blocks_concurrent_duplicate` (600s window anchored on first receipt) | `8aac888` | Integration suite green (234 passed) | PENDING OPERATOR |
| A05 | Returning customer | `backend/tests/integration/test_s3_identity.py::test_a05_returning_customer_reuses_contact` | `19f0c5e` | Integration suite green (234 passed) | PENDING OPERATOR |
| A06 | No unsafe merge | `backend/tests/integration/test_s3_identity.py::test_a06_same_phone_distinct_emails_do_not_merge`, `test_a06_same_name_distinct_emails_do_not_merge` | `19f0c5e` | Integration suite green (234 passed) | PENDING OPERATOR |
| A07 | Normalization | `backend/tests/unit/test_normalization.py` (email whitespace/tag forms, AU phone forms, invalid phone rejected); HTTP 422 mapping in `backend/tests/integration/test_api_intake.py::test_invalid_phone_is_domain_422` | `fcc9474` | Unit suite green (136 passed) and integration suite green (234 passed) | PENDING OPERATOR |
| A08 | Review and route | `backend/tests/integration/test_s4_review_task.py::test_a08_review_resolution`; `backend/tests/integration/test_api_actions.py::test_review_resolve_returns_detail_and_persists` | See git history for `test_s4_review_task.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A09 | Routing precedence | `backend/tests/unit/test_routing.py::test_routing_first_match_table`, `test_routing_config_hash_matches_authority`; transcription pin in `backend/tests/integration/test_s2_intake.py::test_routing_transcription_matches_authority_hash` | `fcc9474` | Unit suite green (136 passed) and integration suite green (234 passed) | PENDING OPERATOR |
| A10 | Preserve CRM fields | `backend/tests/integration/test_s5_crm.py::test_a10_preserves_nonblank_remote_fields`, `test_a10_capability_off_still_syncs_without_patch` (masked preservation) | `b04b863` | Integration suite green (234 passed) | PENDING OPERATOR |
| A11 | 503 before write | `backend/tests/integration/test_s6a_retry.py::test_a11_retryable_failures_backoff_then_succeed` | See git history for `test_s6a_retry.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A12 | Lost success response | `backend/tests/integration/test_s6b_unknown.py::test_a12_lost_create_response_reconciles_by_readback` (one remote contact, link set, causal event order) | See git history for `test_s6b_unknown.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A13 | Unknown patch / unresolved lookup | `backend/tests/integration/test_closeout_regressions.py::test_readback_notfound_ladder_absolute_instants` (T+1/T+5/T+21); `backend/tests/integration/test_r2_recovery.py` reconciliation cases | See git history for `test_closeout_regressions.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A14 | Crash after write | `backend/tests/integration/test_fault_demo.py::test_crash_after_remote_write_recovers_by_readback` (PARTIAL: armed in-process hook and lease expiry, not a real child-process kill) | `567c43a` | PARTIAL - integration suite green (234 passed); real process-exit half not asserted here, see A26 | PENDING OPERATOR |
| A15 | Stale worker fencing | `backend/tests/integration/test_r8_lease_fencing.py::test_stale_worker_must_not_finalize_a_success`, `test_stale_worker_must_not_reschedule_or_block_new_owner` | See git history for `test_r8_lease_fencing.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A16 | Auth block/resume | `backend/tests/integration/test_s6c_blocked.py::test_a16_auth_failure_blocks_provider`, `test_a16_no_writes_while_blocked`, `test_a16_failed_probe_keeps_blocking`, `test_a16_successful_probe_resumes_and_requeues` | `94285cb` | Integration suite green (234 passed) | PENDING OPERATOR |
| A17 | Retry-After | `backend/tests/integration/test_s6a_retry.py::test_a17_retry_after_defers_next_attempt_exactly` | See git history for `test_s6a_retry.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A18 | Permanent failure/manual retry | `backend/tests/integration/test_r16_operator_retry.py::test_dead_job_is_reopened_with_one_audit_event`, `test_stale_retry_is_a_clean_conflict_not_a_second_cycle`, `test_recovery_follows_the_unresolved_operation` | See git history for `test_r16_operator_retry.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A19 | Ordinary notification idempotency | None. Slack appears only as a seeded provider name in integration-status tests; no notification delivery or idempotency test exists. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| A20 | Slack delivery unknown/failure | None. No Slack delivery-unknown test exists. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| A21 | AI validation/fallback | `backend/tests/unit/test_extraction.py` (valid fields kept, invalid budget/evidence dropped to null); `backend/tests/integration/test_extraction.py::test_valid_extraction_persisted_with_snapshot`, `test_invalid_budget_and_evidence_dropped_to_null`, `test_extractor_failure_falls_back_and_still_prepares` | `eca2d0b` | Unit suite green (136 passed) and integration suite green (234 passed) | PENDING OPERATOR |
| A22 | Untrusted text | `frontend/tests/app.test.tsx` escaped-rendering cases; backend intake stores message as data (see `backend/tests/integration/test_api_intake.py`) (PARTIAL: no dedicated malicious-HTML end-to-end case was identified) | `3770890` | PARTIAL - frontend suite green (14 passed) and integration suite green (234 passed); dedicated injection case not asserted | PENDING OPERATOR |
| A23 | Independent versions | `backend/tests/integration/test_owner_drafts.py::test_draft_save_and_owner_change_do_not_conflict`, `test_owner_change_race_exactly_one_succeeds` | `b722df5` | Integration suite green (234 passed) | PENDING OPERATOR |
| A24 | Terminal task idempotency | `backend/tests/integration/test_s4_review_task.py::test_a24_complete_is_terminal_and_repeatable`, `test_a24_cancel_is_terminal_and_repeatable`; HTTP repeat/opposite-action cases in `backend/tests/integration/test_api_actions.py` | See git history for `test_s4_review_task.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| A25 | Intake DB rollback | `backend/tests/integration/test_s2_intake.py::test_a25_failed_task_insert_leaves_no_partial_intake` (PARTIAL: atomicity proven; the API-level 503 mapping is explicitly not asserted in that file) | `7128f3d` | PARTIAL - integration suite green (234 passed); 503 mapping half not asserted | PENDING OPERATOR |
| A26 | Actual process exit | None for the compose-kill form. Closest evidence is worker recovery in `backend/tests/integration/test_p1b_worker.py` and the A14 hook case; neither kills a real worker process via Compose. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| A27 | Security and limits | `backend/tests/integration/test_p3_ingress.py` (Origin, body bound), `backend/tests/integration/test_p3_ratelimit_webhook.py` (429 envelope, spoofed X-Forwarded-For ignored, webhook signature/replay), `backend/tests/unit/test_p3_ingress_unit.py` (proxy handling) | `8101bce` | Integration suite green (234 passed) and unit suite green (136 passed) | PENDING OPERATOR |
| A28 | UI and polling | `frontend/tests/app.test.tsx` polling-visibility cases; `e2e/core-ui.spec.ts` (labelled sign-in, quote-to-task flow, hidden polling pause, draft retained on 409) | `1b3d3df` | Frontend suite green here (14 passed); Playwright 5 passed as recorded in `docs/e2e-browser-evidence.md` (cited document, not re-run here) | PENDING OPERATOR |
| A29 | Illegal config | `backend/tests/unit/test_connected_pinning.py` (http mode without base URL/token fails startup; connected extraction without model fails startup; no fixture fallback); `backend/tests/unit/test_crm_settings.py` | `347a954` | Unit suite green (136 passed) | PENDING OPERATOR |
| A30 | Live provider smoke | `backend/tests/live/test_live_smoke.py::test_live_create_read_patch_and_repeat_create` exists but is opt-in and was not run; no real account or credentials in this delivery. Connected mode is UNVERIFIED per `docs/connected-handover.md` (M0 O01-O04 open). | `347a954` | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| A31 | Fault matcher unavailable | `backend/tests/unit/test_fault_demo.py::test_director_unavailable_never_raises_and_reports_inconclusive`; `backend/tests/integration/test_fault_demo.py::test_director_unavailable_marks_inconclusive`, `test_scenario_lookup_failure_leaves_job_handled`, `test_matcher_consumes_once_and_replay_is_safe` | `567c43a` | Unit suite green (136 passed) and integration suite green (234 passed) | PENDING OPERATOR |
| A32 | Contact version coordination | `backend/tests/integration/test_a32_contact_version.py` (7 tests: contact/version update, rearm with older enquiry ready, unknown stays readback-first, mid-sync race, changed-data new enquiry with remote patch, unsupported patch does not advance link, finalization serialization) | `efdc07d` | Integration suite green (234 passed) | PENDING OPERATOR |
| A33 | Timeline cursor commit safety | `backend/tests/integration/test_a33_timeline.py::test_timeline_order_shape_and_audit_only_excluded`, `test_timeline_cursor_pagination_exactly_once`, `test_timeline_commit_safety_two_transactions` | See git history for `test_a33_timeline.py` | Integration suite green (234 passed) | PENDING OPERATOR |
| C01 | Enum/vocabulary consistency | None in this repository. The plan package (vocabulary source and generator) lives outside this repo and is not vendored; no generated enum copy exists here to compare. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |
| C02 | Fixtures validate against schema | None in this repository. The plan fixtures and OpenAPI schema live outside this repo; the six detail-state shapes are covered behaviourally by API detail tests, but no fixture-validation run exists here. | None | NO AUTOMATED EVIDENCE | PENDING OPERATOR |

## Coverage summary (automated evidence only, not acceptance)

- Items with automated evidence observed green here: A01-A13,
  A15-A18, A21, A23, A24, A27-A29, A31-A33 (26 items), plus A28
  frontend evidence here with Playwright cited from
  `docs/e2e-browser-evidence.md`.
- Partial: A14 (no real process kill), A22 (no dedicated injection
  case), A25 (503 mapping not asserted).
- No automated evidence: A19, A20, A26, A30, C01, C02.
- Every verdict above is `PENDING OPERATOR`. No item is signed,
  dated, or accepted on the operator's behalf in this file.
