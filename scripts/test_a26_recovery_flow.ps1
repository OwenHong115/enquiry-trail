# Behaviour regression harness for the A26 recovery script's
# worker-stop safety gate (no framework, no real Docker).
#
# Runs scripts/a26-process-recovery.ps1 as a child process with a
# fake `docker` executable (scripts/fake-docker-a26.ps1) first in
# PATH. The fake answers compose/psql/inspect calls from files in a
# temp state directory and records observable side effects:
# whether the target lease UPDATE ran and whether the worker was
# restarted. For every unverifiable stop state the script must
# exit non-zero, write a FAIL record, leave the target lease
# untouched and never restart the worker. For a confirmed 'exited'
# state the full recovery must complete with a PASS record.
#
# Usage:
#   powershell -File scripts/test_a26_recovery_flow.ps1 [-TargetScript <path>]
# Exits 0 when every case matches, 1 otherwise.
param(
    [string]$TargetScript = (Join-Path $PSScriptRoot "a26-process-recovery.ps1")
)

$ErrorActionPreference = "Stop"
$hostExe = (Get-Process -Id $PID).Path
$fakeBody = Join-Path $PSScriptRoot "fake-docker-a26.ps1"
if (-not (Test-Path $TargetScript)) { Write-Host "FAIL: target script not found: $TargetScript"; exit 1 }
if (-not (Test-Path $fakeBody)) { Write-Host "FAIL: fake docker body not found: $fakeBody"; exit 1 }

$work = Join-Path ([IO.Path]::GetTempPath()) ("a26-flow-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
$binDir = Join-Path $work "bin"
$stateDir = Join-Path $work "state"
New-Item -ItemType Directory -Path $binDir, $stateDir -Force | Out-Null

# One docker shim for this platform; it only forwards to the fake body.
if ($env:OS -eq "Windows_NT") {
    Set-Content -Path (Join-Path $binDir "docker.cmd") -Value "@powershell -NoProfile -File `"$fakeBody`" %*" -Encoding ASCII
} else {
    $shim = Join-Path $binDir "docker"
    Set-Content -Path $shim -Value "#!/bin/sh`nexec pwsh -NoProfile -File `"$fakeBody`" `"`$@`"" -Encoding ASCII
    & chmod +x $shim
}

$enquiryId = "22222222-2222-2222-2222-222222222222"
$failures = 0

# Tiny local HTTP stand-in for the intake API (login + form create),
# so the target script takes its real enquiry-creation path. It only
# listens on 127.0.0.1 for this harness run and is stopped in finally.
$listenerScript = Join-Path $work "fake-api.ps1"
@'
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:18131/")
$listener.Start()
while ($listener.IsListening) {
    try { $ctx = $listener.GetContext() } catch { break }
    $path = $ctx.Request.Url.AbsolutePath
    if ($path -eq "/api/v1/auth/login") {
        $body = '{"csrf_token":"fake-csrf"}'
    } elseif ($path -eq "/api/v1/intake/form") {
        $body = '{"enquiry_id":"22222222-2222-2222-2222-222222222222"}'
    } else {
        $ctx.Response.StatusCode = 404
        $ctx.Response.Close()
        continue
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($body)
    $ctx.Response.ContentType = "application/json"
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
}
'@ | Set-Content $listenerScript -Encoding ASCII
$listenerJob = Start-Job -FilePath $listenerScript
Start-Sleep -Seconds 2

function Run-Case([string]$Mode, [bool]$ExpectPass) {
    foreach ($flag in @("lease-modified", "worker-restarted", "lock-released", "lock-holder-started")) {
        Remove-Item (Join-Path $stateDir $flag) -ErrorAction SilentlyContinue
    }
    $record = Join-Path $work "record-$Mode.md"
    $env:A26_FAKE_STATE = $stateDir
    $env:A26_FAKE_INSPECT = $Mode
    $env:DATABASE_URL = "postgresql+psycopg://fake:fake@db:5432/fake"
    $env:A26_OPERATOR_USERNAME = "fake-operator"
    $env:SEED_OPERATOR_PASSWORD = "fake-password"
    $env:PATH = "$binDir$([IO.Path]::PathSeparator)$env:PATH"
    & $hostExe -NoProfile -File $TargetScript -ApiBaseUrl "http://127.0.0.1:18131" -RecordPath $record | Out-Null
    $exitCode = $LASTEXITCODE
    $recordText = if (Test-Path $record) { Get-Content $record -Raw } else { "" }
    $leaseModified = Test-Path (Join-Path $stateDir "lease-modified")
    $restarted = Test-Path (Join-Path $stateDir "worker-restarted")
    $problems = @()
    if ($ExpectPass) {
        if ($exitCode -ne 0) { $problems += "exit=$exitCode (expected 0)" }
        if ($recordText -notmatch "Verdict: PASS") { $problems += "record is not PASS" }
        if (-not $leaseModified) { $problems += "lease was not expired on the confirmed-stop path" }
        if (-not $restarted) { $problems += "worker was not restarted on the confirmed-stop path" }
    } else {
        if ($exitCode -eq 0) { $problems += "exit=0 (expected non-zero)" }
        if ($recordText -notmatch "Verdict: FAIL") { $problems += "record is not FAIL" }
        if ($recordText -notmatch "worker stop could not be confirmed|worker_stopped_after_kill") { $problems += "record does not name the unconfirmed stop" }
        if ($leaseModified) { $problems += "target lease was modified although the stop was unconfirmed" }
        if ($restarted) { $problems += "worker was restarted although the stop was unconfirmed" }
    }
    if ($problems.Count -eq 0) {
        Write-Host "PASS: case '$Mode' (exit=$exitCode, leaseModified=$leaseModified, restarted=$restarted)"
    } else {
        Write-Host "FAIL: case '$Mode' (exit=$exitCode, leaseModified=$leaseModified, restarted=$restarted): $($problems -join '; ')"
        $script:failures++
    }
}

try {
    Run-Case "fail" $false
    Run-Case "empty" $false
    Run-Case "garbage" $false
    Run-Case "running" $false
    Run-Case "missing" $false
    Run-Case "exited" $true
} finally {
    if ($listenerJob) { Stop-Job $listenerJob -ErrorAction SilentlyContinue; Remove-Job $listenerJob -Force -ErrorAction SilentlyContinue }
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures -gt 0) {
    Write-Host "FAIL: $failures case(s) mismatched"
    exit 1
}
Write-Host "PASS: all flow cases matched"
exit 0
