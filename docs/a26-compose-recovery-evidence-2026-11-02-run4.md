# A26 compose process-recovery evidence

Filled by scripts/a26-process-recovery.ps1 from an actual run. The committed copy before any run is only a template.

- Timestamp (UTC): 2026-11-03T02:21:14.5821492Z
- Commit: c29d78e5f4f58bf1c9cfa8265c2818c9ce6e17f7
- Enquiry ID: d63caee6-1dc8-4b21-af46-a7285adcc1a1
- Contact ID: 91a5ad7c-57e7-4611-98c5-510001432f52
- Claimed job (id|kind) observed before kill: 5945656a-a1a0-4e37-aef8-c6cbfccfcf82|prepare_enquiry
- In-flight stage proved: prepare-stage kill before any remote write (claimed job kind above); not a post-remote-write crash - remote-write crash recovery is A14's evidence.
- Verdict: PASS

## Steps

- == A26 process recovery: claim barrier, kill worker, restart, count ==
- database connection taken from DATABASE_URL (environment or .env); user=leadops database=leadops
- crm_links lock requested: starting background lock-holder (docker compose exec db psql, ACCESS EXCLUSIVE on leadops.crm_links, pg_sleep(180))...
- crm_links lock-holder started: process id=2688; verifying lock in pg_locks (timeout 30s)...
- crm_links lock held: granted AccessExclusiveLock on leadops.crm_links verified via pg_locks (lock-holder process id=2688, bounded pg_sleep(180))
- enquiry_id = d63caee6-1dc8-4b21-af46-a7285adcc1a1 (created through intake API)
- contact_id = 91a5ad7c-57e7-4611-98c5-510001432f52
- waiting up to 120s for a prepare/sync job of this enquiry to be claimed (state='running', lease set)...
- claim barrier passed: claimed job (id|kind) = 5945656a-a1a0-4e37-aef8-c6cbfccfcf82|prepare_enquiry
- recording worker container id before kill (docker compose ps -a -q worker)...
- docker compose ps -a -q worker: 328f244a3426f3607e942b8ed0290fbb6b21be331713544e91a24e2a22f3f4a2
- worker_container_id_before_kill = 328f244a3426f3607e942b8ed0290fbb6b21be331713544e91a24e2a22f3f4a2
- killing worker service (docker compose kill worker)...
- docker compose kill worker:  Container eqa26-worker-1 Killing 
- docker compose kill worker:  Container eqa26-worker-1 Killed 
- worker killed; stopping crm_links lock-holder before lease expiry and restart...
- crm_links lock released: lock-holder process 2688 stopped; bounded pg_sleep(180) auto-release remains as backstop if the session lingers
- crm_links lock backend terminated: 141
- crm_links lock confirmed released
- inspecting worker container 328f244a3426f3607e942b8ed0290fbb6b21be331713544e91a24e2a22f3f4a2 after kill (docker inspect)...
- worker state after kill = exited (docker inspect exit 0)
- worker_stopped_after_kill = stopped (expected stopped)
- target enquiry leases expired = 1
- restarting worker service...
- docker compose up -d worker:  Container eqa26-db-1 Running 
- docker compose up -d worker:  Container eqa26-provider-sim-1 Running 
- docker compose up -d worker:  Container eqa26-db-1 Waiting 
- docker compose up -d worker:  Container eqa26-db-1 Healthy 
- docker compose up -d worker:  Container eqa26-migrate-1 Starting 
- docker compose up -d worker:  Container eqa26-migrate-1 Started 
- docker compose up -d worker:  Container eqa26-migrate-1 Waiting 
- docker compose up -d worker:  Container eqa26-provider-sim-1 Waiting 
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

- claimed_job_before_kill: 5945656a-a1a0-4e37-aef8-c6cbfccfcf82|prepare_enquiry
- worker_container_id_before_kill: 328f244a3426f3607e942b8ed0290fbb6b21be331713544e91a24e2a22f3f4a2
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
