# Regression harness for the A26 worker-stop verdict (no framework).
#
# Extracts the pure function Resolve-WorkerStopVerdict from
# a26-process-recovery.ps1 via the PowerShell AST and asserts its
# classification for query-failure / empty / garbage / live / stopped
# inputs. The function under test must never classify a failed or
# empty query as stopped: only an inspected 'exited' or 'dead' state
# with exit code 0 is stopped.
#
# Usage:
#   powershell -File scripts/test_a26_worker_state.ps1 [-ScriptPath <path>]
# Exits 0 when every case matches, 1 otherwise. Prints one PASS/FAIL
# line per case.
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot "a26-process-recovery.ps1")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ScriptPath)) {
    Write-Host "FAIL: script not found: $ScriptPath"
    exit 1
}
$resolvedScript = (Resolve-Path $ScriptPath).Path

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($resolvedScript, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors -and $parseErrors.Count -gt 0) {
    foreach ($parseError in $parseErrors) {
        Write-Host "FAIL: parse error in $resolvedScript : $($parseError.Message)"
    }
    exit 1
}

$funcAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Resolve-WorkerStopVerdict"
}, $true)

if ($null -eq $funcAst) {
    Write-Host "FAIL: Resolve-WorkerStopVerdict not found in $resolvedScript"
    exit 1
}

# Define only the extracted pure function in this scope.
Invoke-Expression $funcAst.Extent.Text

$cases = @(
    @{ Name = "exit nonzero"; ExitCode = 1; RawState = ""; Expected = "container-missing" }
    @{ Name = "empty output"; ExitCode = 0; RawState = ""; Expected = "failed:empty-state" }
    @{ Name = "garbage output"; ExitCode = 0; RawState = "garbage-xyz"; Expected = "failed:unparseable-state:garbage-xyz" }
    @{ Name = "running"; ExitCode = 0; RawState = "running"; Expected = "not-stopped:running" }
    @{ Name = "exited"; ExitCode = 0; RawState = "exited"; Expected = "stopped" }
    @{ Name = "dead"; ExitCode = 0; RawState = "dead"; Expected = "stopped" }
    @{ Name = "container-missing exit code"; ExitCode = 125; RawState = "Error: No such container: abc123"; Expected = "container-missing" }
    @{ Name = "restarting"; ExitCode = 0; RawState = "restarting"; Expected = "not-stopped:restarting" }
    @{ Name = "paused"; ExitCode = 0; RawState = "paused"; Expected = "not-stopped:paused" }
    @{ Name = "created"; ExitCode = 0; RawState = "created"; Expected = "not-stopped:created" }
)

$failures = 0
foreach ($case in $cases) {
    try {
        $actual = Resolve-WorkerStopVerdict -ExitCode $case.ExitCode -RawState $case.RawState
    } catch {
        $actual = "threw: $($_.Exception.Message)"
    }
    if ([string]$actual -eq [string]$case.Expected) {
        Write-Host "PASS: $($case.Name) (ExitCode=$($case.ExitCode), RawState='$($case.RawState)') -> $actual"
    } else {
        Write-Host "FAIL: $($case.Name) (ExitCode=$($case.ExitCode), RawState='$($case.RawState)') -> got '$actual', expected '$($case.Expected)'"
        $failures++
    }
}

if ($failures -gt 0) {
    Write-Host "FAIL: $failures of $($cases.Count) case(s) mismatched"
    exit 1
}
Write-Host "PASS: all $($cases.Count) case(s) matched"
exit 0
