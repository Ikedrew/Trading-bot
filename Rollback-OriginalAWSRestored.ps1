$ErrorActionPreference = 'Stop'
$taskName = 'OriginalAWSRestored'
$s4TaskName = 'VantageS4Observe'
$kill = 'C:\TradingBotRuntimeData\original-aws-restored\logs\kill_switch.flag'
New-Item -ItemType File -Path $kill -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
Disable-ScheduledTask -TaskName $taskName | Out-Null
if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
}
$deadline = (Get-Date).AddSeconds(60)
do {
    $live = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match 'C:\\TradingBotRuntime\\original-aws-restored\\app\\main\.py'
    })
    if ($live.Count -eq 0) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
if ($live.Count -ne 0) { throw 'RESTORED_SCANNER_STILL_RUNNING' }
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
$s4 = Get-ScheduledTask -TaskName $s4TaskName -ErrorAction Stop
if ($s4.Actions.Count -ne 1 -or $s4.Actions[0].Arguments -notmatch 'run_s4_observe\.ps1.*-Worker') {
    throw 'S4_ROLLBACK_TASK_MISMATCH'
}
Enable-ScheduledTask -TaskName $s4TaskName | Out-Null
Start-ScheduledTask -TaskName $s4TaskName
Write-Output 'ORIGINAL_AWS_ROLLED_BACK_S4_RESTORED'
