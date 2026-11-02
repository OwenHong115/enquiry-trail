# A26 compose process-recovery evidence

Filled by scripts/a26-process-recovery.ps1 from an actual run. The committed copy before any run is only a template.

- Timestamp (UTC): 2026-11-02T03:27:26.3654406Z
- Commit: 2ec59270b615825017c10b0abce88b86718e8c7c
- Enquiry ID: 1347a330-8598-4d39-8d87-9fb3436c6cf4
- Contact ID: 16f90f73-0d68-41ed-8d39-1e9007fba21b
- Claimed job (id|kind) observed before kill: 7790ad74-c303-46cc-8307-760c4fe45e55|prepare_enquiry
- Verdict: PASS

## Steps

- == A26 process recovery: claim barrier, kill worker, restart, count ==
- database connection taken from DATABASE_URL (environment or .env); user=leadops database=leadops
- crm_links lock requested: starting background lock-holder (docker compose exec db psql, ACCESS EXCLUSIVE on leadops.crm_links, pg_sleep(180))...
- crm_links lock-holder started: process id=13024; verifying lock in pg_locks (timeout 30s)...
- crm_links lock held: granted AccessExclusiveLock on leadops.crm_links verified via pg_locks (lock-holder process id=13024, bounded pg_sleep(180))
- enquiry_id = 1347a330-8598-4d39-8d87-9fb3436c6cf4 (created through intake API)
- contact_id = 16f90f73-0d68-41ed-8d39-1e9007fba21b
- waiting up to 120s for a prepare/sync job of this enquiry to be claimed (state='running', lease set)...
- claim barrier passed: claimed job (id|kind) = 7790ad74-c303-46cc-8307-760c4fe45e55|prepare_enquiry
- killing worker service (docker compose kill worker)...
- docker compose kill worker:  Container enquirytrail-worker-1 Killing
- docker compose kill worker:  Container enquirytrail-worker-1 Killed
- worker killed; stopping crm_links lock-holder before lease expiry and restart...
- crm_links lock released: lock-holder process 13024 stopped; bounded pg_sleep(180) auto-release remains as backstop if the session lingers
- crm_links lock backend terminated: 339035
- crm_links lock confirmed released
- worker state after kill =
- target enquiry leases expired = 1
- restarting worker service...
- docker compose up -d worker:  Container enquirytrail-db-1 Running
- docker compose up -d worker:  Container enquirytrail-provider-sim-1 Running
- docker compose up -d worker:  Container enquirytrail-db-1 Waiting
- docker compose up -d worker:  Container enquirytrail-db-1 Healthy
- docker compose up -d worker:  Container enquirytrail-migrate-1 Starting
- docker compose up -d worker:  Container enquirytrail-migrate-1 Started
- docker compose up -d worker:  Container enquirytrail-provider-sim-1 Waiting
- docker compose up -d worker:  Container enquirytrail-migrate-1 Waiting
- docker compose up -d worker:  Container enquirytrail-provider-sim-1 Healthy
- docker compose up -d worker:  Container enquirytrail-migrate-1 Exited
- docker compose up -d worker:  Container enquirytrail-worker-1 Starting
- docker compose up -d worker:  Container enquirytrail-worker-1 Started
- open jobs for this enquiry = 0
- prepare_enquiry_jobs_for_enquiry = 1 (expected 1)
- sync_contact_jobs_for_contact = 1 (expected 1)
- crm_links_for_contact = 1 (expected 1)
- follow_up_tasks_for_enquiry = 1 (expected 1)
- ready_synced_enquiries = 1 (expected 1)
- notifications_for_enquiry = 0 (expected 0)
- event_enquiry.received = 1 (expected 1)
- event_enquiry.prepared = 1 (expected 1)
- event_crm.synced = 1 (expected 1)
- provider-sim counts (local simulator only, not connected evidence) = {"counts": {"lookup_email": 3, "lookup_id": 0, "create": 3, "patch": 0, "probe": 0}, "contacts": 3}

## Counts

- claimed_job_before_kill: 7790ad74-c303-46cc-8307-760c4fe45e55|prepare_enquiry
- worker_state_after_kill:
- target_leases_expired: 1
- open_jobs_for_enquiry_after_drain: 0
- prepare_enquiry_jobs_for_enquiry: 1
- sync_contact_jobs_for_contact: 1
- crm_links_for_contact: 1
- follow_up_tasks_for_enquiry: 1
- ready_synced_enquiries: 1
- notifications_for_enquiry: 0
- event_enquiry.received: 1
- event_enquiry.prepared: 1
- event_crm.synced: 1
