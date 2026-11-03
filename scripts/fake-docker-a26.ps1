# Fake `docker` executable body for test_a26_recovery_flow.ps1.
# Driven only by environment state in $env:A26_FAKE_STATE; never
# touches a real Docker daemon, database, or network service.
$stateDir = $env:A26_FAKE_STATE
$all = ($args -join ' ')
if ($env:A26_FAKE_LOG) { Add-Content -Path $env:A26_FAKE_LOG -Value $all }

if ($all -match 'LOCK TABLE') {
    New-Item -ItemType File -Path (Join-Path $stateDir 'lock-holder-started') -Force | Out-Null
    Start-Sleep -Seconds 25
    exit 0
}
if ($all -match 'psql') {
    if ($all -match 'pg_terminate_backend') {
        New-Item -ItemType File -Path (Join-Path $stateDir 'lock-released') -Force | Out-Null
        Write-Output 't'
        exit 0
    }
    if ($all -match 'pg_locks') {
        if (Test-Path (Join-Path $stateDir 'lock-released')) { Write-Output '0' } else { Write-Output '1' }
        exit 0
    }
    if ($all -match "state = 'running' AND lease_token") { Write-Output 'fakejob1|prepare_enquiry'; exit 0 }
    if ($all -match 'SET lease_until') {
        New-Item -ItemType File -Path (Join-Path $stateDir 'lease-modified') -Force | Out-Null
        Write-Output '1'
        exit 0
    }
    if ($all -match "state <> 'succeeded'") { Write-Output '0'; exit 0 }
    if ($all -match 'SELECT contact_id') { Write-Output '11111111-1111-1111-1111-111111111111'; exit 0 }
    if ($all -match 'email_norm') { Write-Output 'target@example.com'; exit 0 }
    if ($all -match 'SELECT remote_id') { Write-Output 'sim-123'; exit 0 }
    if ($all -match 'notifications') { Write-Output '0'; exit 0 }
    if ($all -match 'prepare_enquiry|sync_contact|crm_links|follow_up_tasks|local_prepared_at|activity_events') { Write-Output '1'; exit 0 }
    Write-Output '0'
    exit 0
}
if ($all -match '__test__/contacts') {
    Write-Output '{"contacts":[{"id":"sim-123","email":"target@example.com"}],"count":1}'
    exit 0
}
if ($all -match '__test__/counts') {
    Write-Output '{"counts":{},"contacts":1}'
    exit 0
}
if ($all -match '^inspect') {
    switch ($env:A26_FAKE_INSPECT) {
        'exited'  { Write-Output 'exited'; exit 0 }
        'running' { Write-Output 'running'; exit 0 }
        'empty'   { exit 0 }
        'garbage' { Write-Output 'garbage-xyz'; exit 0 }
        'fail'    { [Console]::Error.WriteLine('fake inspect failure'); exit 1 }
        'missing' { [Console]::Error.WriteLine('Error: No such container: fake'); exit 125 }
        default   { Write-Output 'exited'; exit 0 }
    }
}
if ($all -match '^compose ps' -and $all -match '-q') { Write-Output 'b5930d717880'; exit 0 }
if ($all -match '^compose ps') {
    # State query without -q (the pre-fix script's verdict source):
    # mirror the same fake mode as docker inspect.
    switch ($env:A26_FAKE_INSPECT) {
        'exited'  { Write-Output 'exited'; exit 0 }
        'running' { Write-Output 'running'; exit 0 }
        'empty'   { exit 0 }
        'garbage' { Write-Output 'garbage-xyz'; exit 0 }
        'fail'    { [Console]::Error.WriteLine('fake ps failure'); exit 1 }
        'missing' { exit 0 }
        default   { Write-Output 'exited'; exit 0 }
    }
}
if ($all -match '^compose kill') { exit 0 }
if ($all -match '^compose up') {
    New-Item -ItemType File -Path (Join-Path $stateDir 'worker-restarted') -Force | Out-Null
    exit 0
}
exit 0

