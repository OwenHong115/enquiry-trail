# A26 process-recovery operator check (kill / restart / count).
#
# Performs the same kill/restart/count check as the automated test in
# backend/tests/integration/test_a26_process_recovery.py, but against a
# running compose stack: it identifies (or, with operator credentials,
# creates) one enquiry, waits until that enquiry's prepare/sync job is
# claimed by the worker (state 'running' with a lease set), hard-kills
# the worker service mid-work, expires only that enquiry's orphaned
# job lease, restarts the worker, waits for that enquiry's jobs to
# drain, then counts persisted rows for that enquiry/contact only and
# prints PASS/FAIL (exit code is non-zero on any mismatch).
#
# Stage proved by this script: the kill lands while the claimed job
# (its kind is recorded in the record) is at the prepare stage,
# blocked on crm_links before any remote write. This record does
# not claim a post-remote-write crash; the prepare-stage kill is
# what this record proves; remote-write crash recovery is A14's
# evidence.
#
# Header note on evidence: simulator counts are NOT connected evidence.
# The provider-sim counters only describe the local simulator process;
# they do not prove anything about a real CRM provider. Persisted
# database counts are the local record; a real provider must be checked
# in that provider's own system.
#
# Prerequisites (no secrets in this file; no credentials are hardcoded):
#   DATABASE_URL        required: taken from the environment, or from
#                       a DATABASE_URL line in the repo-root .env file.
#                       The script fails with a clear message when it
#                       is absent from both. Only the user, password
#                       and database name are parsed out of it; queries
#                       run through the compose db service (docker
#                       compose exec db psql), with the password passed
#                       to psql through the PGPASSWORD environment
#                       variable, never as a literal in this file.
#   SEED_OPERATOR_PASSWORD  required by compose interpolation, as for
#                       any docker compose command in this repo. When
#                       it (and SEED_OPERATOR_USERNAME or
#                       A26_OPERATOR_USERNAME) is available and no
#                       enquiry is named, the script also uses it to
#                       log in and create a fresh enquiry through the
#                       intake API, so the claim barrier below starts
#                       from a known, not-yet-claimed job.
#   A26_WEB_ORIGIN      optional: web UI origin sent as the Origin
#                       header on the intake request (the -WebOrigin
#                       parameter wins). Default:
#                       http://127.0.0.1:5173. The Origin must be an
#                       allowed web origin (one of ALLOWED_ORIGINS when
#                       that is explicitly configured), not the API
#                       base URL ($ApiBaseUrl, http://127.0.0.1:8080);
#                       sending the API base gets 403 origin_invalid
#                       when ALLOWED_ORIGINS lists only the web origin.
#   A26_ENQUIRY_ID      optional: scope the run to one enquiry (the
#                       -EnquiryId parameter wins). Without it, and
#                       without operator credentials to create one,
#                       the most recent enquiry is used; if that
#                       enquiry's jobs were never observed claimed,
#                       the barrier fails and the worker is not killed.
#   -RecordPath         optional: evidence record written on every
#                       run (PASS or FAIL). Default:
#                       docs/a26-compose-recovery-evidence.md under
#                       the repo root. The committed copy of that file
#                       is only a template; a run replaces it with the
#                       timestamp, commit, steps, counts and verdict
#                       observed in that run.
# In-flight window: before the target enquiry is created or reused,
# the script holds ACCESS EXCLUSIVE on leadops.crm_links (only that
# table; never leadops.jobs or leadops.enquiries) from a background
# lock-holder psql session bounded by pg_sleep(180). A claimed
# prepare/sync job then stays in state 'running' while the worker
# blocks on its mid-sync crm_links read/insert, so the claim barrier
# below reliably observes the claim before the kill. The holder is
# stopped right after the worker kill, and again from a finally path
# on any failure after acquisition; the bounded sleep auto-releases
# the lock if the holder process lingers.
#
# The script is safe to re-run: it only kills/restarts the worker
# service and reads counts. It never truncates or rewrites data; the
# only data change is expiring the target enquiry's orphaned lease so
# the restarted worker can reclaim it without waiting out the lease.
param(
    [string]$EnquiryId = $env:A26_ENQUIRY_ID,
    [string]$RecordPath = "docs/a26-compose-recovery-evidence.md",
    [int]$ClaimTimeoutSeconds = 120,
    [int]$LockTimeoutSeconds = 30,
    [int]$DrainTimeoutSeconds = 120,
    [string]$ApiBaseUrl = "http://127.0.0.1:8080",
    [string]$WebOrigin = $(if ($env:A26_WEB_ORIGIN) { $env:A26_WEB_ORIGIN } else { "http://127.0.0.1:5173" })
)

$ErrorActionPreference = "Stop"

$script:failures = @()
$script:steps = @()
$script:counts = [ordered]@{}
$script:enquiryId = $null
$script:contactId = $null
$script:claimedJob = $null
$script:lockHolder = $null
$script:lockHeld = $false
$script:lockHoldSeconds = 180
$script:repoRoot = Split-Path -Parent $PSScriptRoot

function Add-Step([string]$Message) {
    $script:steps += $Message
    Write-Host $Message
}

$script:lastComposeOutput = @()

function Invoke-Compose([string[]]$ComposeArgs) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = docker compose @ComposeArgs 2>&1
        $exitCode = $LASTEXITCODE
        $script:lastComposeOutput = @($output | ForEach-Object { "$_" })
        foreach ($line in $script:lastComposeOutput) {
            if ($line) { Add-Step "docker compose $($ComposeArgs -join ' '): $line" }
        }
        return $exitCode
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Get-ConfigValue([string]$Name) {
    $fromEnv = [Environment]::GetEnvironmentVariable($Name)
    if ($fromEnv) { return $fromEnv.Trim() }
    foreach ($candidate in @((Join-Path $script:repoRoot ".env"), (Join-Path (Get-Location) ".env"))) {
        if (Test-Path $candidate) {
            foreach ($line in Get-Content $candidate) {
                if ($line -match "^\s*$Name\s*=\s*(.*)\s*$") {
                    $value = $Matches[1].Trim().Trim('"').Trim("'")
                    if ($value) { return $value }
                }
            }
        }
    }
    return $null
}

function Resolve-DatabaseUrl {
    $url = Get-ConfigValue "DATABASE_URL"
    if (-not $url) {
        throw "DATABASE_URL is not set in the environment and no DATABASE_URL line was found in a .env file (looked in repo root and current directory). Set DATABASE_URL in the environment or add DATABASE_URL=<url> to .env (see .env.example); this script does not hardcode database credentials."
    }
    return $url
}

function Get-DbParts([string]$Url) {
    $rest = $Url.Trim() -replace "^postgres(?:ql)?(\+[A-Za-z0-9_]+)?://", ""
    if ($rest -eq $Url.Trim()) { throw "DATABASE_URL could not be parsed: expected a postgres URL (see .env.example)." }
    if ($rest -notmatch "^([^:]+):([^@]*)@([^/]+)/([^?]+)") {
        throw "DATABASE_URL could not be parsed: expected user, password, host and database name (see .env.example)."
    }
    return @{
        User = [Uri]::UnescapeDataString($Matches[1])
        Password = [Uri]::UnescapeDataString($Matches[2])
        Database = [Uri]::UnescapeDataString($Matches[4].Trim())
    }
}

$script:dbParts = $null

function Invoke-PsqlScalar([string]$Sql) {
    # Run one scalar query inside the db service (no local psql needed).
    # Credentials come only from DATABASE_URL; the host/port in that URL
    # address the host side of the compose db service, so inside the db
    # container psql connects to the local server with the parsed user
    # and database. The password travels via PGPASSWORD, not arguments.
    $previousPassword = $env:PGPASSWORD
    $env:PGPASSWORD = $script:dbParts.Password
    try {
        $raw = docker compose exec -T -e PGPASSWORD db psql -U $script:dbParts.User -d $script:dbParts.Database -At -c $Sql
        if ($LASTEXITCODE -ne 0) { throw "psql query failed: $Sql" }
        return ($raw | Out-String).Trim()
    } finally {
        $env:PGPASSWORD = $previousPassword
    }
}

function Get-CrmLockHolderArgumentString {
    # Argument string for Start-Process (docker compose). The SQL is
    # double-quoted as a single -c value; user/database come from the
    # parsed DATABASE_URL and must not contain characters that would
    # break that quoting. The password never appears here: it travels
    # via the PGPASSWORD environment variable (see Start-CrmLinksLock).
    foreach ($part in @($script:dbParts.User, $script:dbParts.Database)) {
        if ($part -match '[\s"]') { throw "DATABASE_URL user/database contains whitespace or a quote; cannot build the lock-holder command safely" }
    }
    $sql = "BEGIN; LOCK TABLE leadops.crm_links IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep($($script:lockHoldSeconds)); COMMIT;"
    return "compose exec -T -e PGPASSWORD db psql -U $($script:dbParts.User) -d $($script:dbParts.Database) -v ON_ERROR_STOP=1 -c `"$sql`""
}

function Confirm-CrmLinksLockReleased {
    # Poll pg_locks for a granted AccessExclusiveLock on leadops.crm_links for up to 30s; if still held, terminate the holding backend(s), then poll again until free (up to 30s). Returns $true only when no granted AccessExclusiveLock remains. May throw when DB is unreachable; callers decide whether to gate (throw) or swallow (finally path).
    $lockQuery = "SELECT count(*) FROM pg_locks WHERE locktype='relation' AND relation='leadops.crm_links'::regclass AND granted AND mode='AccessExclusiveLock'"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $held = Invoke-PsqlScalar $lockQuery
        if ($held -match '^\d+$' -and [int]$held -eq 0) { Add-Step "crm_links lock confirmed released"; return $true }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    $held = Invoke-PsqlScalar $lockQuery
    if ($held -match '^\d+$' -and [int]$held -eq 0) { Add-Step "crm_links lock confirmed released"; return $true }
    $pidsRaw = Invoke-PsqlScalar "SELECT pid FROM pg_locks WHERE locktype='relation' AND relation='leadops.crm_links'::regclass AND granted AND mode='AccessExclusiveLock'"
    $pids = @($pidsRaw -split '\s+' | Where-Object { $_ -match '^\d+$' })
    Invoke-PsqlScalar "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype='relation' AND relation='leadops.crm_links'::regclass AND granted AND mode='AccessExclusiveLock'" | Out-Null
    foreach ($pidValue in $pids) { Add-Step "crm_links lock backend terminated: $pidValue" }
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $held = Invoke-PsqlScalar $lockQuery
        if ($held -match '^\d+$' -and [int]$held -eq 0) { Add-Step "crm_links lock confirmed released"; return $true }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    $held = Invoke-PsqlScalar $lockQuery
    if ($held -match '^\d+$' -and [int]$held -eq 0) { Add-Step "crm_links lock confirmed released"; return $true }
    return $false
}

function Stop-CrmLinksLock([switch]$RequireRelease) {
    # Idempotent: safe from the post-kill step and the finally path.
    # With -RequireRelease (post-kill), the release is verified via
    # Confirm-CrmLinksLockReleased and failure throws before lease
    # expiry/restart. Without it (finally), confirmation is best-effort:
    # DB errors are swallowed/step-logged so the primary error is kept.
    if ($null -eq $script:lockHolder -and -not $RequireRelease) { return }
    $hadHolder = $null -ne $script:lockHolder
    if ($hadHolder) {
        $holder = $script:lockHolder
        $script:lockHolder = $null
        $wasHeld = $script:lockHeld
        $script:lockHeld = $false
        try {
            $holder.Refresh()
            if ($holder.HasExited) {
                Add-Step "crm_links lock released: lock-holder process $($holder.Id) already exited (bounded pg_sleep($($script:lockHoldSeconds)) session ended); wasHeld=$wasHeld"
            } else {
                Stop-Process -Id $holder.Id -Force -ErrorAction Stop
                try { $holder.WaitForExit(5000) | Out-Null } catch { }
                Add-Step "crm_links lock released: lock-holder process $($holder.Id) stopped; bounded pg_sleep($($script:lockHoldSeconds)) auto-release remains as backstop if the session lingers"
            }
        } catch {
            Add-Step "crm_links lock released: stopping lock-holder process $($holder.Id) failed ($($_.Exception.Message)); bounded pg_sleep($($script:lockHoldSeconds)) will auto-release the lock"
        }
    }
    if ($RequireRelease) {
        $released = $false
        try {
            $released = Confirm-CrmLinksLockReleased
        } catch {
            throw "crm_links lock could not be confirmed released after stopping the lock-holder ($($_.Exception.Message)); worker NOT restarted against a held lock"
        }
        if (-not $released) {
            throw "crm_links lock could not be confirmed released after stopping the lock-holder and terminating the holding backend(s); worker NOT restarted against a held lock"
        }
    } elseif ($hadHolder) {
        try {
            $released = Confirm-CrmLinksLockReleased
            if (-not $released) {
                Add-Step "crm_links lock release not confirmed in finally path (non-gating); bounded pg_sleep($($script:lockHoldSeconds)) will auto-release the lock"
            }
        } catch {
            Add-Step "crm_links lock release check failed in finally path (non-gating, swallowed): $($_.Exception.Message)"
        }
    }
}

function Start-CrmLinksLock {
    # Start the bounded lock-holder and prove the lock is held before
    # any enquiry is created or reused. Only leadops.crm_links is
    # locked; leadops.jobs and leadops.enquiries are never locked.
    Add-Step "crm_links lock requested: starting background lock-holder (docker compose exec db psql, ACCESS EXCLUSIVE on leadops.crm_links, pg_sleep($($script:lockHoldSeconds)))..."
    $argumentString = Get-CrmLockHolderArgumentString
    $previousPassword = $env:PGPASSWORD
    $env:PGPASSWORD = $script:dbParts.Password
    try {
        $script:lockHolder = Start-Process -FilePath "docker" -ArgumentList $argumentString -WorkingDirectory $script:repoRoot -WindowStyle Hidden -PassThru
    } finally {
        $env:PGPASSWORD = $previousPassword
    }
    if ($null -eq $script:lockHolder) { throw "crm_links lock not acquired: lock-holder process did not start; worker was NOT killed" }
    Add-Step "crm_links lock-holder started: process id=$($script:lockHolder.Id); verifying lock in pg_locks (timeout ${LockTimeoutSeconds}s)..."
    $lockDeadline = (Get-Date).AddSeconds($LockTimeoutSeconds)
    do {
        $script:lockHolder.Refresh()
        if ($script:lockHolder.HasExited) {
            throw "crm_links lock not acquired: lock-holder process $($script:lockHolder.Id) exited (code $($script:lockHolder.ExitCode)) before the lock was verified; worker was NOT killed"
        }
        $granted = Invoke-PsqlScalar "SELECT count(*) FROM pg_locks WHERE relation = 'leadops.crm_links'::regclass AND mode = 'AccessExclusiveLock' AND granted"
        if ($granted -eq "1" -or $granted -eq "t") { $script:lockHeld = $true; break }
        # pg_locks may show more than one row only if another holder
        # exists; any granted AccessExclusive row proves the table lock.
        if ($granted -match '^\d+$' -and [int]$granted -ge 1) { $script:lockHeld = $true; break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $lockDeadline)
    if (-not $script:lockHeld) {
        throw "crm_links lock not acquired within ${LockTimeoutSeconds}s (pg_locks showed no granted AccessExclusiveLock on leadops.crm_links); worker was NOT killed; crm_links lock was released"
    }
    Add-Step "crm_links lock held: granted AccessExclusiveLock on leadops.crm_links verified via pg_locks (lock-holder process id=$($script:lockHolder.Id), bounded pg_sleep($($script:lockHoldSeconds)))"
}

function Assert-Guid([string]$Value, [string]$What) {
    if ($Value -notmatch "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$") {
        throw "$What is not a valid id: $Value"
    }
    return $Value.ToLowerInvariant()
}

function Check([string]$Name, $Actual, $Expected) {
    $script:counts[$Name] = $Actual
    Add-Step "$Name = $Actual (expected $Expected)"
    if ([string]$Actual -ne [string]$Expected) {
        $script:failures += "$Name expected $Expected got $Actual"
    }
}

function New-A26Enquiry {
    # Create one fresh enquiry through the public intake API, using the
    # seeded operator login (same flow as scripts/perf_measure.py).
    $username = Get-ConfigValue "A26_OPERATOR_USERNAME"
    if (-not $username) { $username = Get-ConfigValue "SEED_OPERATOR_USERNAME" }
    $password = Get-ConfigValue "SEED_OPERATOR_PASSWORD"
    if (-not $username -or -not $password) {
        throw "operator credentials unavailable for enquiry creation"
    }
    $base = $ApiBaseUrl.TrimEnd("/")
    $webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $loginBody = @{ username = $username; password = $password } | ConvertTo-Json
    $login = Invoke-WebRequest -Uri "$base/api/v1/auth/login" -Method Post -Body $loginBody -ContentType "application/json" -WebSession $webSession -UseBasicParsing
    $csrf = ($login.Content | ConvertFrom-Json).csrf_token
    $suffix = [Guid]::NewGuid().ToString("N").Substring(0, 8)
    $payload = [ordered]@{
        first_name = "A26"; last_name = "Recovery"
        email = "a26-recovery-$suffix@example.com"
        phone = "+61412345678"; postcode = "2000"; service_type = "kitchen"
        message = "I need a kitchen renovation next month. My budget is around AUD 20,000."
        preferred_contact = "email"; acknowledge_demo = $true
    } | ConvertTo-Json
    $headers = @{
        "Idempotency-Key" = [Guid]::NewGuid().ToString()
        "X-CSRF-Token" = $csrf
        "Origin" = $WebOrigin.TrimEnd("/")
    }
    $receipt = Invoke-WebRequest -Uri "$base/api/v1/intake/form" -Method Post -Body $payload -ContentType "application/json" -Headers $headers -WebSession $webSession -UseBasicParsing
    return [string](($receipt.Content | ConvertFrom-Json).enquiry_id)
}

function Write-RecoveryRecord([string]$Verdict) {
    $target = $RecordPath
    if (-not [IO.Path]::IsPathRooted($target)) { $target = Join-Path $script:repoRoot $target }
    $commit = "unknown"
    try {
        $resolved = git -C $script:repoRoot rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) { $commit = ($resolved | Out-String).Trim() }
    } catch { $commit = "unknown" }
    $lines = @(
        "# A26 compose process-recovery evidence",
        "",
        "Filled by scripts/a26-process-recovery.ps1 from an actual run. The committed copy before any run is only a template.",
        "",
        "- Timestamp (UTC): $((Get-Date).ToUniversalTime().ToString('o'))",
        "- Commit: $commit",
        "- Enquiry ID: $($script:enquiryId)",
        "- Contact ID: $($script:contactId)",
        "- Claimed job (id|kind) observed before kill: $($script:claimedJob)",
        "- In-flight stage proved: prepare-stage kill before any remote write (claimed job kind above); not a post-remote-write crash - remote-write crash recovery is A14's evidence.",
        "- Verdict: $Verdict",
        "",
        "## Steps",
        ""
    )
    foreach ($step in $script:steps) { $lines += "- $step" }
    $lines += @("", "## Counts", "")
    foreach ($key in $script:counts.Keys) { $lines += "- ${key}: $($script:counts[$key])" }
    if ($script:failures.Count -gt 0) {
        $lines += @("", "## Failures", "")
        foreach ($failure in $script:failures) { $lines += "- $failure" }
    }
    $parent = Split-Path -Parent $target
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Set-Content -Path $target -Value $lines -Encoding UTF8
    Write-Host "record written to $target"
}

try {
    Add-Step "== A26 process recovery: claim barrier, kill worker, restart, count =="
    Set-Location $script:repoRoot

    $databaseUrl = Resolve-DatabaseUrl
    $script:dbParts = Get-DbParts $databaseUrl
    Add-Step "database connection taken from DATABASE_URL (environment or .env); user=$($script:dbParts.User) database=$($script:dbParts.Database)"

    # Real in-flight window: hold crm_links BEFORE creating/reusing
    # the target enquiry, so a claimed job stays 'running' while the
    # worker blocks on crm_links instead of finishing in ~140ms.
    Start-CrmLinksLock

    # Resolve or create the enquiry in scope before touching the worker.
    if ($EnquiryId) {
        $script:enquiryId = Assert-Guid $EnquiryId "enquiry id"
        Add-Step "enquiry_id = $($script:enquiryId) (supplied)"
    } else {
        try {
            $script:enquiryId = Assert-Guid (New-A26Enquiry) "created enquiry id"
            Add-Step "enquiry_id = $($script:enquiryId) (created through intake API)"
        } catch {
            Add-Step "enquiry creation unavailable ($($_.Exception.Message)); falling back to most recent enquiry"
            $script:enquiryId = Invoke-PsqlScalar "SELECT id FROM leadops.enquiries ORDER BY received_at DESC LIMIT 1"
            if (-not $script:enquiryId) { throw "no enquiry found; submit one through the intake API first" }
            $script:enquiryId = Assert-Guid $script:enquiryId "enquiry id"
            Add-Step "enquiry_id = $($script:enquiryId) (most recent)"
        }
    }
    $script:contactId = Invoke-PsqlScalar "SELECT contact_id FROM leadops.enquiries WHERE id='$($script:enquiryId)'"
    if (-not $script:contactId) { throw "enquiry $($script:enquiryId) not found" }
    $script:contactId = Assert-Guid $script:contactId "contact id"
    Add-Step "contact_id = $($script:contactId)"

    # Every job query below is scoped to this enquiry/contact: the
    # prepare job carries entity_id = enquiry, the shared sync job is
    # keyed by dedupe_key = 'sync_contact:<contact_id>'.
    $jobScope = "(entity_id = '$($script:enquiryId)' OR dedupe_key = 'sync_contact:$($script:contactId)')"

    # In-flight barrier: do not kill until this enquiry's prepare/sync
    # job is claimed (state 'running' with a lease set). Fail, without
    # killing the worker, if no claim is observed in time.
    Add-Step "waiting up to ${ClaimTimeoutSeconds}s for a prepare/sync job of this enquiry to be claimed (state='running', lease set)..."
    $claimDeadline = (Get-Date).AddSeconds($ClaimTimeoutSeconds)
    do {
        $script:claimedJob = Invoke-PsqlScalar "SELECT id::text || '|' || kind FROM leadops.jobs WHERE $jobScope AND state = 'running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL ORDER BY created_at LIMIT 1"
        if ($script:claimedJob) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $claimDeadline)
    if (-not $script:claimedJob) {
        throw "claim barrier failed: no prepare/sync job for enquiry $($script:enquiryId) was claimed (state='running' with lease set) within ${ClaimTimeoutSeconds}s; worker was NOT killed; crm_links lock was released"
    }
    $script:counts["claimed_job_before_kill"] = $script:claimedJob
    Add-Step "claim barrier passed: claimed job (id|kind) = $($script:claimedJob)"

    Add-Step "killing worker service (docker compose kill worker)..."
    if ((Invoke-Compose @("kill", "worker")) -ne 0) { throw "docker compose kill worker failed" }
    Add-Step "worker killed; stopping crm_links lock-holder before lease expiry and restart..."
    Stop-CrmLinksLock -RequireRelease
    Invoke-Compose @("ps", "-a", "worker", "--format", "{{.State}}") | Out-Null
    $workerStateLines = @($script:lastComposeOutput | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    $workerState = if ($workerStateLines.Count -gt 0) { $workerStateLines[0].ToLowerInvariant() } else { "" }
    if (-not $workerState) {
        Invoke-Compose @("ps", "-a", "-q", "worker") | Out-Null
        $workerIds = @($script:lastComposeOutput | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
        if ($workerIds.Count -eq 0) { $workerState = "stopped-absent" }
    }
    # $workerState was normalized from the ps -a output above; an
    # empty value here means the state could not be parsed and the
    # worker_stopped_after_kill check below must fail, not pass.
    $script:counts["worker_state_after_kill"] = $workerState
    Add-Step "worker state after kill = $workerState"
    $workerStopped = if ($workerState -eq "exited" -or $workerState -eq "stopped-absent") { "stopped" } else { "not-stopped:$workerState" }
    Check "worker_stopped_after_kill" $workerStopped "stopped"

    # Expire only this enquiry's orphaned lease (same scoped job set as
    # the barrier), so the restarted worker can reclaim it at once
    # instead of waiting out the 60 second lease. No other enquiry's
    # running leases are touched.
    $expiredCount = Invoke-PsqlScalar "WITH updated AS (UPDATE leadops.jobs SET lease_until = clock_timestamp() - interval '1 minute', next_attempt_at = clock_timestamp() - interval '1 minute' WHERE state = 'running' AND $jobScope RETURNING id) SELECT count(*) FROM updated"
    $script:counts["target_leases_expired"] = $expiredCount
    Add-Step "target enquiry leases expired = $expiredCount"

    Add-Step "restarting worker service..."
    if ((Invoke-Compose @("up", "-d", "worker")) -ne 0) { throw "docker compose up -d worker failed" }

    $drainDeadline = (Get-Date).AddSeconds($DrainTimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
        $open = Invoke-PsqlScalar "SELECT count(*) FROM leadops.jobs WHERE $jobScope AND state <> 'succeeded'"
        Add-Step "open jobs for this enquiry = $open"
    } while ($open -ne "0" -and (Get-Date) -lt $drainDeadline)
    $script:counts["open_jobs_for_enquiry_after_drain"] = $open
    if ($open -ne "0") { $script:failures += "jobs for enquiry $($script:enquiryId) did not drain to succeeded within ${DrainTimeoutSeconds}s (open=$open)" }

    # All counts are scoped to this enquiry / its contact.
    Check "prepare_enquiry_jobs_for_enquiry" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.jobs WHERE kind = 'prepare_enquiry' AND entity_id = '$($script:enquiryId)'") 1
    Check "sync_contact_jobs_for_contact" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.jobs WHERE kind = 'sync_contact' AND dedupe_key = 'sync_contact:$($script:contactId)'") 1
    Check "crm_links_for_contact" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.crm_links WHERE contact_id = '$($script:contactId)'") 1
    Check "follow_up_tasks_for_enquiry" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.follow_up_tasks WHERE enquiry_id = '$($script:enquiryId)'") 1
    Check "ready_synced_enquiries" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.enquiries e JOIN leadops.crm_links l ON l.contact_id = e.contact_id WHERE e.id = '$($script:enquiryId)' AND e.local_prepared_at IS NOT NULL AND l.sync_status = 'synced' AND l.synced_contact_version >= e.required_contact_version") 1
    Check "notifications_for_enquiry" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.notifications WHERE enquiry_id = '$($script:enquiryId)'") 0
    foreach ($eventType in @("enquiry.received", "enquiry.prepared", "crm.synced")) {
        Check "event_$eventType" (Invoke-PsqlScalar "SELECT count(*) FROM leadops.activity_events WHERE enquiry_id = '$($script:enquiryId)' AND event_type = '$eventType'") 1
    }

    # Remote uniqueness for this target only (not global sim counts):
    # exactly one remote contact with the target contact's email, and
    # its id must equal this contact's crm_links.remote_id.
    try {
        $targetEmail = Invoke-PsqlScalar "SELECT leadops.contacts.email_norm FROM leadops.enquiries JOIN leadops.contacts ON leadops.contacts.id = leadops.enquiries.contact_id WHERE leadops.enquiries.id = '$($script:enquiryId)'"
        if (-not $targetEmail) { throw "target contact email not found for enquiry $($script:enquiryId)" }
        $linkedRemoteId = Invoke-PsqlScalar "SELECT remote_id FROM leadops.crm_links WHERE contact_id = '$($script:contactId)'"
        if (-not $linkedRemoteId) { throw "crm_links.remote_id not found for contact $($script:contactId)" }
        $encodedEmail = [Uri]::EscapeDataString($targetEmail.Trim())
        $simContactsRaw = docker compose exec -T provider-sim python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/__test__/contacts?email=$encodedEmail').read().decode())"
        if ($LASTEXITCODE -ne 0) { throw "provider-sim contacts query failed (exit $LASTEXITCODE)" }
        $simContacts = (($simContactsRaw | Out-String).Trim() | ConvertFrom-Json)
        $remoteContacts = @($simContacts.contacts)
        Check "remote_contacts_for_target_email" $remoteContacts.Count 1
        $remoteIdMatches = ($remoteContacts.Count -eq 1 -and [string]$remoteContacts[0].id -eq $linkedRemoteId.Trim())
        Check "remote_id_matches_link" $remoteIdMatches $true
    } catch {
        Add-Step "remote uniqueness check failed: $($_.Exception.Message)"
        Check "remote_contacts_for_target_email" "unavailable" 1
        Check "remote_id_matches_link" $false $true
    }

    # Simulator counts are reported for local diagnosis only (see header):
    # they are not connected evidence about any real provider.
    try {
        $simCounts = docker compose exec -T provider-sim python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/__test__/counts').read().decode())"
        Add-Step "provider-sim counts (local simulator only, not connected evidence) = $simCounts"
    } catch {
        Add-Step "provider-sim counts unavailable: $($_.Exception.Message)"
    }
} catch {
    $script:failures += $_.Exception.Message
    Add-Step "error: $($_.Exception.Message)"
} finally {
    Stop-CrmLinksLock
}

$verdict = if ($script:failures.Count -gt 0) { "FAIL" } else { "PASS" }
try {
    Write-RecoveryRecord $verdict
} catch {
    Write-Host "failed to write recovery record: $($_.Exception.Message)"
    $script:failures += "failed to write recovery record: $($_.Exception.Message)"
    $verdict = "FAIL"
}
if ($script:failures.Count -gt 0) {
    Write-Host "FAIL: $($script:failures -join '; ')"
    exit 1
}
Write-Host "PASS"
exit 0
