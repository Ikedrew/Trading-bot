param(
    [string]$Python = 'python',
    [string]$StateRoot = 'data/research/continuous',
    [double]$TickIntervalSeconds = 60,
    [int]$MaxRestarts = 3,
    [int]$RestartDelaySeconds = 15,
    [string]$LogDirectory = 'logs/research_cadence',
    [string]$ResearchAwsProfile = ''
)

$ErrorActionPreference = 'Stop'
if ($TickIntervalSeconds -le 0 -or $MaxRestarts -lt 0 -or $RestartDelaySeconds -lt 0) {
    throw 'Invalid cadence supervisor interval or restart limit.'
}
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
function Resolve-FromRepo([string]$value) {
    if ([IO.Path]::IsPathRooted($value)) { return [IO.Path]::GetFullPath($value) }
    return [IO.Path]::GetFullPath((Join-Path $repoRoot $value))
}
$resolvedState = Resolve-FromRepo $StateRoot
$resolvedLogs = Resolve-FromRepo $LogDirectory
New-Item -ItemType Directory -Path $resolvedLogs -Force | Out-Null
if ($ResearchAwsProfile) { $env:RESEARCH_AWS_PROFILE = $ResearchAwsProfile }

Push-Location $repoRoot
try {
    for ($attempt = 0; $attempt -le $MaxRestarts; $attempt++) {
        $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        $stdout = Join-Path $resolvedLogs ("cadence-$stamp-$attempt.stdout.log")
        $stderr = Join-Path $resolvedLogs ("cadence-$stamp-$attempt.stderr.log")
        & $Python -m research_engine.v10.continuous.research_cadence `
            --state-root $resolvedState --tick-interval-seconds $TickIntervalSeconds `
            1>> $stdout 2>> $stderr
        $code = $LASTEXITCODE
        if ($code -eq 0) { exit 0 }
        if ($code -eq 2) { exit 2 } # Invalid config or another cadence service owns the state.
        if ($attempt -eq $MaxRestarts) { exit $code }
        Start-Sleep -Seconds $RestartDelaySeconds
    }
} finally {
    Pop-Location
}
