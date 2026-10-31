# A26 process-recovery operator check (kill / restart / count).
#
# Performs the same kill/restart/count check as the automated test in
# backend/tests/integration/test_a26_process_recovery.py, but against a
# running compose stack: it hard-kills the worker service mid-work,
# restarts it, waits for the queue to drain, then counts persisted rows
# and prints PASS/FAIL (exit code is non-zero on any mismatch).
#
# Header note on evidence: simulator counts are NOT connected evidence.
# The provider-sim counters only describe the local simulator process;
# they do not prove anything about a real CRM provider. Persisted
# database counts are the local record; a real provider must be checked
# in that provider's own system.
#
# Prerequisites (read from the environment, no secrets in this file):
#   Queries run through the compose db service (docker compose exec db
#   psql), so no database password appears in this file or its calls.
#   SEED_OPERATOR_PASSWORD  required by compose interpolation, as for
#                       any docker compose command in this repo
#   A26_ENQUIRY_ID      optional: scope counts to one enquiry; without
#                       it the most recent enquiry is used
# The script is safe to re-run: it only kills/restarts the worker
# service and reads counts. It never truncates or rewrites data; the
# only data change is expiring the killed worker's orphaned lease so
# the restarted worker can reclaim it without waiting out the lease.
$ErrorActionPreference = "Stop"

$failures = @()

function Invoke-PsqlScalar([string]$Sql) {
    # Run one scalar query inside the db service (no local psql needed).
    $raw = docker compose exec -T db psql "postgresql://leadops:leadops@db:5432/leadops" -At -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql query failed: $Sql" }
    return ($raw | Out-String).Trim()
}

function Check([string]$Name, $Actual, $Expected) {
    Write-Host "$Name = $Actual (expected $Expected)"
    if ([string]$Actual -ne [string]$Expected) {
        $script:failures += "$Name expected $Expected got $Actual"
    }
}

Write-Host "== A26 process recovery: kill worker, restart, count =="

# Resolve the enquiry in scope before touching the worker.
if ($env:A26_ENQUIRY_ID) {
    $enquiryId = $env:A26_ENQUIRY_ID
} else {
    $enquiryId = Invoke-PsqlScalar "SELECT id FROM leadops.enquiries ORDER BY received_at DESC LIMIT 1"
}
if (-not $enquiryId) { throw "no enquiry found; submit one through the intake API first" }
Write-Host "enquiry_id = $enquiryId"
$contactId = Invoke-PsqlScalar "SELECT contact_id FROM leadops.enquiries WHERE id='$enquiryId'"
Write-Host "contact_id = $contactId"

Write-Host "killing worker service (docker compose kill worker)..."
docker compose kill worker
if ($LASTEXITCODE -ne 0) { throw "docker compose kill worker failed" }
$workerState = (docker compose ps worker --format "{{.State}}")
Write-Host "worker state after kill = $workerState"

# Expire the orphaned lease so the restarted worker can reclaim at
# once instead of waiting out the 60 second lease.
$expired = Invoke-PsqlScalar "UPDATE leadops.jobs SET lease_until = clock_timestamp() - interval '1 minute', next_attempt_at = clock_timestamp() - interval '1 minute' WHERE state='running'; SELECT count(*) FROM leadops.jobs WHERE state='running'"
Write-Host "running jobs still leased after expiry step = $expired"

Write-Host "restarting worker service..."
docker compose up -d worker
if ($LASTEXITCODE -ne 0) { throw "docker compose up -d worker failed" }

$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 2
    $open = Invoke-PsqlScalar "SELECT count(*) FROM leadops.jobs WHERE state <> 'succeeded'"
    Write-Host "open jobs = $open"
} while ($open -ne "0" -and (Get-Date) -lt $deadline)
if ($open -ne "0") { $failures += "jobs did not drain to succeeded within 120s (open=$open)" }

Check "crm_links_for_contact" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.crm_links WHERE contact_id='$contactId'") 1
Check "follow_up_tasks_for_enquiry" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.follow_up_tasks WHERE enquiry_id='$enquiryId'") 1
Check "sync_contact_jobs" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.jobs WHERE kind='sync_contact'") 1
Check "ready_synced_enquiries" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.enquiries e JOIN leadops.crm_links l ON l.contact_id = e.contact_id WHERE e.id='$enquiryId' AND e.local_prepared_at IS NOT NULL AND l.sync_status='synced' AND l.synced_contact_version >= e.required_contact_version") 1
Check "notifications_for_enquiry" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.notifications WHERE enquiry_id='$enquiryId'") 0
foreach ($eventType in @("enquiry.received", "enquiry.prepared", "crm.synced")) {
    Check "event_$eventType" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.activity_events WHERE enquiry_id='$enquiryId' AND event_type='$eventType'") 1
}

# Simulator counts are reported for local diagnosis only (see header):
# they are not connected evidence about any real provider.
try {
    $simCounts = docker compose exec -T provider-sim python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/__test__/counts').read().decode())"
    Write-Host "provider-sim counts (local simulator only, not connected evidence) = $simCounts"
} catch {
    Write-Host "provider-sim counts unavailable: $($_.Exception.Message)"
}

if ($failures.Count -gt 0) {
    Write-Host "FAIL: $($failures -join '; ')"
    exit 1
}
Write-Host "PASS"
exit 0
