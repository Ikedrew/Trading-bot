param(
 [Parameter(Mandatory=$true)][string]$Source,
 [Parameter(Mandatory=$true)][string]$EvidenceBundle,
 [Parameter(Mandatory=$true)][string]$PythonRuntime,
 [Parameter(Mandatory=$true)][string]$Wheelhouse,
 [Parameter(Mandatory=$true)][string]$Destination,
 [ValidateRange(2048,32768)][int]$MemoryMB=8192
)
$ErrorActionPreference='Stop'
throw 'Sandbox preparation disabled: selected laptop runs Windows Home. See docs/stage_a_end_to_end_qualification.md. Do not enable Sandbox.'
function CheckedDirectory($path) {
 $item=Get-Item -LiteralPath $path
 if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Not a plain directory: $path" }
 return $item.FullName
}
$Source=CheckedDirectory $Source; $EvidenceBundle=CheckedDirectory $EvidenceBundle
$PythonRuntime=CheckedDirectory $PythonRuntime; $Wheelhouse=CheckedDirectory $Wheelhouse
if ((Split-Path $Source -Leaf) -ne 'Trading-bot-stage-a') { throw 'Only the Stage A source checkout is accepted.' }
if (-not (Test-Path -LiteralPath (Join-Path $EvidenceBundle 'offline_objects.json'))) { throw 'Missing verified raw-object bundle: offline_objects.json. Historical results alone cannot qualify a coordinator.' }
if (-not (Test-Path -LiteralPath (Join-Path $PythonRuntime 'python.exe')) -or (Test-Path -LiteralPath (Join-Path $PythonRuntime 'pyvenv.cfg'))) { throw 'Supply a standalone installed Python runtime, not a checkout virtualenv.' }
if (Test-Path -LiteralPath $Destination) { throw 'Destination must not exist; no existing files will be overwritten.' }
$Destination=[IO.Path]::GetFullPath($Destination)
if ($Destination.Contains('"')) { throw 'Unsupported destination quoting.' }
New-Item -ItemType Directory -Path $Destination | Out-Null
$export=New-Item -ItemType Directory -Path (Join-Path $Destination 'source')
# Export code, not checkout data, profiles, credentials, .git, or virtualenvs.
foreach($tree in @('core','research_engine','utils','tools')) {
 $root=Join-Path $Source $tree
 if (-not (Test-Path -LiteralPath $root)) { continue }
 foreach($file in Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.py') {
  if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse-point source file rejected.' }
  $relative=$file.FullName.Substring($Source.Length+1)
  $target=Join-Path $export.FullName $relative
  New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
  Copy-Item -LiteralPath $file.FullName -Destination $target
 }
}
foreach($name in @('stage4_observation_requirement_matrix_20260929.json','stage4_data_versioning_policy_20260929.json','stage4_observation_dataset_audit_20260929.json')) {
 $from=Join-Path $Source "analysis\assurance\$name"
 if (-not (Test-Path -LiteralPath $from)) { throw "Required static policy absent: $name" }
 $target=Join-Path $export.FullName "analysis\assurance\$name"
 New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
 Copy-Item -LiteralPath $from -Destination $target
}
Copy-Item -LiteralPath (Join-Path $Source 'tools\qualification_guest.ps1') -Destination (Join-Path $Destination 'qualification_guest.ps1')
New-Item -ItemType Directory -Path (Join-Path $Destination 'results') | Out-Null
function Mapping($hostPath,$guestPath,$readOnly) {
 $h=[Security.SecurityElement]::Escape($hostPath); $g=[Security.SecurityElement]::Escape($guestPath)
 return "<MappedFolder><HostFolder>$h</HostFolder><SandboxFolder>$g</SandboxFolder><ReadOnly>$readOnly</ReadOnly></MappedFolder>"
}
$maps=(Mapping $export.FullName 'C:\qualification\src' 'true') + (Mapping $EvidenceBundle 'C:\qualification\evidence' 'true') + (Mapping $PythonRuntime 'C:\qualification\python' 'true') + (Mapping $Wheelhouse 'C:\qualification\wheels' 'true') + (Mapping $Destination 'C:\qualification\control' 'true') + (Mapping (Join-Path $Destination 'results') 'C:\qualification\results' 'false')
$xml="<Configuration><Networking>Disable</Networking><VGpu>Disable</VGpu><ClipboardRedirection>Disable</ClipboardRedirection><PrinterRedirection>Disable</PrinterRedirection><AudioInput>Disable</AudioInput><VideoInput>Disable</VideoInput><MemoryInMB>$MemoryMB</MemoryInMB><MappedFolders>$maps</MappedFolders><LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\qualification\control\qualification_guest.ps1</Command></LogonCommand></Configuration>"
[IO.File]::WriteAllText((Join-Path $Destination 'qualification.wsb'),$xml)
Get-ChildItem -LiteralPath $export.FullName -File -Recurse | ForEach-Object { [pscustomobject]@{Path=$_.FullName.Substring($export.FullName.Length+1); SHA256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} } | Export-Csv -NoTypeInformation -LiteralPath (Join-Path $Destination 'source_hashes.csv')
Write-Output "Prepared only: $Destination\qualification.wsb. No sandbox or Python process launched. Inspect mappings before opening it."
