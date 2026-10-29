param([switch]$Worker, [switch]$Inspect, [switch]$ApproveDemoStart, [switch]$Foreground)

$ErrorActionPreference = 'Stop'
$runtime = 'C:\TradingBotRuntime\original-aws-restored'
$app = Join-Path $runtime 'app'
$data = 'C:\TradingBotRuntimeData\original-aws-restored'
$py = 'C:\TradingBotRuntime\venv\Scripts\python.exe'
$protected = 'C:\TradingBotRuntimeData\protected\s6_demo.dpapi.clixml'
$kill = Join-Path $data 'logs\kill_switch.flag'
$taskName = 'OriginalAWSRestored'
$s4TaskName = 'VantageS4Observe'

function Plain([Security.SecureString]$value) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
function ScannerProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -match '(?i)[\\/]main\.py(?:"|\s|$)'
    })
}
function Wait-NoScanner([int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        if (@(ScannerProcesses).Count -eq 0) { return }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw 'SCANNER_PROCESS_STILL_RUNNING'
}

foreach ($path in @($py,$protected,(Join-Path $app 'main.py'),(Join-Path $app 'contabo_preflight.py'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "RESTORED_RUNTIME_FILE_MISSING:$path" }
}
$c = Import-Clixml -LiteralPath $protected
if ($c.Schema -ne 's6-demo-v1' -or
    $c.Owner -ne [Security.Principal.WindowsIdentity]::GetCurrent().Name -or
    -not $c.Login -or -not $c.Server -or -not $c.TerminalPath -or
    -not $c.SymbolMap -or -not $c.IDriveAccessKey -or -not $c.IDriveSecretKey) {
    throw 'RESTORED_PROTECTED_CONFIG_INVALID'
}

if ($Worker -or $Inspect -or $Foreground) {
    if (($Worker -and $Inspect) -or ($Foreground -and ($Worker -or $Inspect))) {
        throw 'INVALID_RESTORED_ARGUMENTS'
    }
    if ($Foreground -and -not $ApproveDemoStart) {
        throw 'EXPLICIT_DEMO_START_APPROVAL_REQUIRED'
    }
    if ($Foreground -and -not (Test-Path -LiteralPath $kill -PathType Leaf)) {
        throw 'RESTORED_KILL_SWITCH_MISSING'
    }
    if (($Worker -or $Foreground) -and @(ScannerProcesses).Count -ne 0) {
        throw 'DUPLICATE_SCANNER_BLOCKED'
    }
    $env:PYTHONPATH = $app
    $env:PYTHONIOENCODING = 'utf-8'
    $env:TRADING_PROFILE = ''
    $env:MULTI_ACCOUNT_FANOUT_ENABLED = 'false'
    $env:MT5_RUNTIME_TERMINAL_PATH = $c.TerminalPath
    $env:MT5_RUNTIME_TERMINAL_PORTABLE = 'true'
    $env:RESTORED_VANTAGE_LOGIN = Plain $c.Login
    $env:RESTORED_VANTAGE_SERVER = Plain $c.Server
    $symbolMap = @{}
($c.SymbolMap | ConvertFrom-Json).PSObject.Properties | ForEach-Object {
    $symbolMap[$_.Name] = $_.Value
}
foreach ($symbol in @('EURUSD','GBPUSD','USDJPY','USDCHF','USDCAD','AUDUSD','NZDUSD')) {
    if (-not $symbolMap.ContainsKey($symbol)) {
        $symbolMap[$symbol] = $symbol
    }
}
$env:RESTORED_VANTAGE_SYMBOL_MAP = ConvertTo-Json -InputObject $symbolMap -Compress
    $env:MT5_METAQUOTES_ENABLED = 'false'
    $env:MT5_ADMIRALS_ENABLED = 'false'
    $env:MT5_VANTAGE_ENABLED = 'true'
    $env:MT5_VANTAGE_ROLE = 'baseline'
    $env:MT5_VANTAGE_LOGIN = $env:RESTORED_VANTAGE_LOGIN
    $env:MT5_VANTAGE_SERVER = $env:RESTORED_VANTAGE_SERVER
    $env:MT5_VANTAGE_TERMINAL_PATH = $c.TerminalPath
    $env:MT5_VANTAGE_PORTABLE = 'true'
    $env:MT5_VANTAGE_SYMBOL_MAP = $env:RESTORED_VANTAGE_SYMBOL_MAP
    $env:AWS_S3_BUCKET = 'trading-bot-v10-data'
    $env:AWS_S3_ENDPOINT_URL = 'https://s3.eu-west-3.idrivee2.com'
    $env:AWS_REGION = 'eu-west-3'
    $env:AWS_ACCESS_KEY_ID = Plain $c.IDriveAccessKey
    $env:AWS_SECRET_ACCESS_KEY = Plain $c.IDriveSecretKey
    $env:CANONICAL_DELIVERY_OUTBOX_PATH = Join-Path $data 'logs\canonical_delivery_outbox.sqlite3'
    $env:DRY_RUN = 'false'
$env:EXECUTION_ENABLED = 'true'
$env:TRADE_MANAGEMENT_ENABLED = 'true'
$env:POSITION_CLOSE_ENABLED = 'true'
$env:MULTI_SYMBOL_SCANNER_ENABLED = 'true'
$env:MAX_OPEN_POSITIONS = '1'
    $env:MAX_TOTAL_OPEN_POSITIONS = '3'
    $env:MAX_TOTAL_RISK_EXPOSURE_PCT = '3.0'
    $env:DAILY_LOSS_LIMIT_PERCENT = '4.0'
    try {
        Set-Location -LiteralPath $data
        & $py -B (Join-Path $app 'contabo_preflight.py') *> (Join-Path $data 'preflight.log')
        if ($LASTEXITCODE -ne 0) { throw 'RESTORED_DEMO_PREFLIGHT_FAILED' }
        if ($Inspect) {
            Get-Content -LiteralPath (Join-Path $data 'preflight.log') -Tail 1
            return
        }
        if ($Foreground) {
            $heartbeat = Join-Path $data 'logs\heartbeat.json'
            $stdout = Join-Path $data 'scanner.log'
            $stderr = Join-Path $data 'scanner.stderr.log'
            $launchEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $proc = $null
            try {
                $proc = Start-Process -FilePath $py `
                    -ArgumentList @('-u', '-B', (Join-Path $app 'main.py')) `
                    -WorkingDirectory $data `
                    -RedirectStandardOutput $stdout `
                    -RedirectStandardError $stderr `
                    -PassThru

                $deadline = (Get-Date).AddSeconds(180)
                $healthy = $false
                do {
                    Start-Sleep -Seconds 2
                    $proc.Refresh()
                    if ($proc.HasExited) {
                        throw "RESTORED_SCANNER_EXITED_EARLY:$($proc.ExitCode)"
                    }
                    if ((Test-Path -LiteralPath $heartbeat -PathType Leaf) -and
                        (Test-Path -LiteralPath $kill -PathType Leaf)) {
                        try {
                            $hb = Get-Content -LiteralPath $heartbeat -Raw | ConvertFrom-Json
                            $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$hb.timestamp
                            if ($hb.status -eq 'alive' -and
                                $hb.mt5_state -eq 'CONNECTED' -and
                                [int]$hb.symbols -eq 10 -and
                                [int]$hb.cycle_id -gt 0 -and
                                [double]$hb.timestamp -ge $launchEpoch -and
                                $age -ge 0 -and $age -le 60) {
                                $healthy = $true
                                break
                            }
                        } catch {
                            # Heartbeat may be mid-write; retry until timeout.
                        }
                    }
                } while ((Get-Date) -lt $deadline)

                if (-not $healthy) {
                    throw 'RESTORED_KILL_GATED_START_NOT_PROVEN'
                }

                Remove-Item -LiteralPath $kill -ErrorAction Stop
                Write-Output 'ORIGINAL_AWS_RESTORED_FOREGROUND_ACTIVE symbols=10'
                Write-Output "Live output: $stdout"
                Write-Output "Error output: $stderr"
                Write-Output 'Press Ctrl+C to stop the foreground session.'
                if (Test-Path -LiteralPath $stdout) {
                    try {
                        Get-Content -LiteralPath $stdout -Wait
                    } finally {
                        # Block new entries before stopping the scanner.
                        try {
                            if (-not (Test-Path -LiteralPath $kill)) {
                                New-Item -ItemType File -Path $kill -Force | Out-Null
                            }
                        } catch {
                            Write-Error "Could not restore kill switch: $($_.Exception.Message)"
                        }

                        # Stop the child scanner even if log viewing is interrupted.
                        if ($proc) {
                            $proc.Refresh()
                            if (-not $proc.HasExited) {
                                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                                try {
                                    Wait-Process -Id $proc.Id -Timeout 10 -ErrorAction SilentlyContinue
                                } catch {}
                            }
                        }
                    }
                } else {
                    throw 'RESTORED_SCANNER_LOG_MISSING'
                }
            } catch {
                if ($proc) {
                    $proc.Refresh()
                    if (-not $proc.HasExited) {
                        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                    }
                }
                if (-not (Test-Path -LiteralPath $kill)) {
                    New-Item -ItemType File -Path $kill -Force | Out-Null
                }
                throw
            }
        } else {
            & $py -u -B (Join-Path $app 'main.py') *>> (Join-Path $data 'scanner.log')
        }
        if ($LASTEXITCODE -ne 0) { throw "RESTORED_SCANNER_EXIT_$LASTEXITCODE" }
    } finally {
        Remove-Item Env:AWS_ACCESS_KEY_ID,Env:AWS_SECRET_ACCESS_KEY,Env:RESTORED_VANTAGE_LOGIN,Env:MT5_VANTAGE_LOGIN -ErrorAction SilentlyContinue
    }
    return
}

if (-not $ApproveDemoStart) { throw 'EXPLICIT_DEMO_START_APPROVAL_REQUIRED' }
if (-not (Test-Path -LiteralPath $kill -PathType Leaf)) { throw 'RESTORED_KILL_SWITCH_MISSING' }
$s4 = Get-ScheduledTask -TaskName $s4TaskName -ErrorAction Stop
if ($s4.Actions.Count -ne 1 -or $s4.Actions[0].Arguments -notmatch 'run_s4_observe\.ps1.*-Worker' -or
    $s4.Principal.LogonType -ne 'Interactive') {
    throw 'S4_TASK_IDENTITY_OR_ACTION_MISMATCH'
}
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw 'RESTORED_TASK_ALREADY_EXISTS'
}
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
try {
    $taskSid = ([Security.Principal.NTAccount]::new($s4.Principal.UserId)).Translate(
        [Security.Principal.SecurityIdentifier]).Value
} catch {
    $taskSid = $s4.Principal.UserId
}
if ($taskSid -ne $currentSid) { throw 'PROTECTED_CONFIG_TASK_PRINCIPAL_MISMATCH' }
$registered = $false
$s4Disabled = $false
try {
    Disable-ScheduledTask -TaskName $s4TaskName | Out-Null
    $s4Disabled = $true
    if ((Get-ScheduledTask -TaskName $s4TaskName).State -eq 'Running') {
        Stop-ScheduledTask -TaskName $s4TaskName -ErrorAction Stop
    }
    Wait-NoScanner 60
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\TradingBotRuntime\original-aws-restored\Start-OriginalAWSRestored.ps1" -Worker' `
        -WorkingDirectory $data
    $trigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $s4.Settings -Principal $s4.Principal -ErrorAction Stop | Out-Null
    $registered = $true
    $launchEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(180)
    $healthy = $false
    do {
        Start-Sleep -Seconds 2
        $processes = @(ScannerProcesses)
        $restored = @($processes | Where-Object { $_.CommandLine -match 'original-aws-restored\\app\\main\.py' })
        $heartbeat = Join-Path $data 'logs\heartbeat.json'
        if ($processes.Count -eq 1 -and $restored.Count -eq 1 -and
            (Test-Path -LiteralPath $heartbeat -PathType Leaf) -and
            (Test-Path -LiteralPath $kill -PathType Leaf)) {
            $hb = Get-Content -LiteralPath $heartbeat -Raw | ConvertFrom-Json
            $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$hb.timestamp
            if ($hb.status -eq 'alive' -and $hb.mt5_state -eq 'CONNECTED' -and
                [int]$hb.symbols -eq 10 -and [int]$hb.cycle_id -gt 0 -and
                [double]$hb.timestamp -ge $launchEpoch -and
                $age -ge 0 -and $age -le 60) {
                $healthy = $true
                break
            }
        }
    } while ((Get-Date) -lt $deadline)
    if (-not $healthy) { throw 'RESTORED_KILL_GATED_START_NOT_PROVEN' }
    Remove-Item -LiteralPath $kill -ErrorAction Stop
    Write-Output "ORIGINAL_AWS_RESTORED_ACTIVE symbols=10 task=$taskName"
} catch {
    $reason = $_.Exception.Message
    if (-not (Test-Path -LiteralPath $kill)) { New-Item -ItemType File -Path $kill -Force | Out-Null }
    if ($registered) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    if ($s4Disabled) { Enable-ScheduledTask -TaskName $s4TaskName | Out-Null }
    throw "ORIGINAL_AWS_START_BLOCKED:$reason"
}

