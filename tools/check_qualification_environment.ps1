param()
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
$computer = Get-CimInstance Win32_ComputerSystem
[pscustomobject]@{Caption=$os.Caption; Version=$os.Version; Architecture=$os.OSArchitecture;
 RAM_GB=[math]::Round($computer.TotalPhysicalMemory/1GB,1); HypervisorPresent=$computer.HypervisorPresent}
Write-Output 'Windows Sandbox is not the selected isolation mechanism. Do not enable it on Windows Home.'
Write-Output 'Candidate: disposable VirtualBox guest, all network adapters absent, no host shared folders or device passthrough.'
Write-Output 'Installation and a smoke boot still need manual verification. No host security feature should be disabled.'
Get-ItemProperty 'HKLM:\SOFTWARE\Oracle\VirtualBox' -ErrorAction SilentlyContinue |
 Select-Object Version,InstallDir
Write-Output 'Read-only check: no features, virtual machines, Python processes, or research jobs started.'
