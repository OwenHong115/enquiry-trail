# A26 compose process-recovery evidence

Filled by scripts/a26-process-recovery.ps1 from an actual run. The committed copy before any run is only a template.

- Timestamp (UTC): 2026-11-02T08:36:46.1446234Z
- Commit: 1d7c9e1806a9e470bc2b5cc76dc411964dc93cec
- Enquiry ID: 8dfcc888-d46a-4798-99ee-7aea2892df38
- Contact ID: b98e7129-f35a-419e-bc0d-d3426842a568
- Claimed job (id|kind) observed before kill: db51a846-90e3-4569-965e-26fb8ace3878|prepare_enquiry
- In-flight stage proved: prepare-stage kill before any remote write (claimed job kind above); not a post-remote-write crash - remote-write crash recovery is A14's evidence.
- Verdict: PASS

## Steps

- == A26 process recovery: claim barrier, kill worker, restart, count ==
- database connection taken from DATABASE_URL (environment or .env); user=leadops database=leadops
- crm_links lock requested: starting background lock-holder (docker compose exec db psql, ACCESS EXCLUSIVE on leadops.crm_links, pg_sleep(180))...
- crm_links lock-holder started: process id=33520; verifying lock in pg_locks (timeout 30s)...
- crm_links lock held: granted AccessExclusiveLock on leadops.crm_links verified via pg_locks (lock-holder process id=33520, bounded pg_sleep(180))
- enquiry_id = 8dfcc888-d46a-4798-99ee-7aea2892df38 (created through intake API)
- contact_id = b98e7129-f35a-419e-bc0d-d3426842a568
- waiting up to 120s for a prepare/sync job of this enquiry to be claimed (state='running', lease set)...
- claim barrier passed: claimed job (id|kind) = db51a846-90e3-4569-965e-26fb8ace3878|prepare_enquiry
- killing worker service (docker compose kill worker)...
- docker compose kill worker:  Container eqa26-worker-1 Killing 
- docker compose kill worker:  Container eqa26-worker-1 Killed 
- worker killed; stopping crm_links lock-holder before lease expiry and restart...
- crm_links lock released: lock-holder process 33520 stopped; bounded pg_sleep(180) auto-release remains as backstop if the session lingers
- crm_links lock backend terminated: 346
- crm_links lock confirmed released
- docker compose ps -a worker --format {{.State}}: exited
- worker state after kill = exited
- worker_stopped_after_kill = stopped (expected stopped)
- target enquiry leases expired = 1
- restarting worker service...
- docker compose up -d worker:  Container eqa26-db-1 Running 
- docker compose up -d worker:  Container eqa26-provider-sim-1 Running 
- docker compose up -d worker:  Container eqa26-db-1 Waiting 
- docker compose up -d worker:  Container eqa26-db-1 Healthy 
- docker compose up -d worker:  Container eqa26-migrate-1 Starting 
- docker compose up -d worker:  Container eqa26-migrate-1 Started 
- docker compose up -d worker:  Container eqa26-provider-sim-1 Waiting 
- docker compose up -d worker:  Container eqa26-migrate-1 Waiting 
- docker compose up -d worker:  Container eqa26-provider-sim-1 Healthy 
- docker compose up -d worker:  Container eqa26-migrate-1 Exited 
- docker compose up -d worker:  Container eqa26-worker-1 Starting 
- docker compose up -d worker:  Container eqa26-worker-1 Started 
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
- remote_contacts_for_target_email = 1 (expected 1)
- remote_id_matches_link = True (expected True)
- provider-sim counts (local simulator only, not connected evidence) = {"counts": {"lookup_email": 1, "lookup_id": 0, "create": 1, "patch": 0, "probe": 0}, "contacts": 1}

## Counts

- claimed_job_before_kill: db51a846-90e3-4569-965e-26fb8ace3878|prepare_enquiry
- worker_state_after_kill: exited
- worker_stopped_after_kill: stopped
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
- remote_contacts_for_target_email: 1
- remote_id_matches_link: True
