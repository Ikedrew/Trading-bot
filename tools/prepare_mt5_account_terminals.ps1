param(
    [string]$DestinationRoot = 'C:\MT5Accounts',
    [switch]$StartPrepared
)
$ErrorActionPreference = 'Stop'
$sourceExe = 'C:\Program Files\MetaTrader 5\terminal64.exe'
$sourceConfig = 'C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\config'
$accounts = @(
    @{ Id='METAQUOTES'; Login=5055599469; Server='MetaQuotes-Demo' },
    @{ Id='ADMIRALS'; Login=42890920; Server='AdmiralsGroup-Demo' },
    @{ Id='VANTAGE'; Login=11041444; Server='VantageGlobalPrimeLLP-Demo' }
)
$rootPath = [IO.Path]::GetFullPath($DestinationRoot)
if ($rootPath -ne 'C:\MT5Accounts') { throw 'Only the reviewed C:\MT5Accounts destination is supported.' }
if ((Get-AuthenticodeSignature -LiteralPath $sourceExe).Status -ne 'Valid') { throw 'Source signature is not valid.' }
if (Test-Path -LiteralPath $rootPath) { throw 'Destination already exists; refusing to replace or merge terminal data.' }
$null = New-Item -ItemType Directory -Path $rootPath
# Encrypted saved-account databases remain on this VM under a private ACL.
# They are copied opaquely, never decoded, printed or placed in the repository.
$acl = Get-Acl -LiteralPath $rootPath
$acl.SetAccessRuleProtection($true, $false)
foreach ($sidText in @('S-1-5-18','S-1-5-32-544')) {
    $sid = [Security.Principal.SecurityIdentifier]::new($sidText)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $rootPath -AclObject $acl
$sourceHash = (Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash
$manifest = @()
foreach ($account in $accounts) {
    $dir = [IO.Path]::GetFullPath((Join-Path $rootPath $account.Id))
    if (-not $dir.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid destination.' }
    $null = New-Item -ItemType Directory -Path (Join-Path $dir 'config') -Force
    $exe = Join-Path $dir 'terminal64.exe'
    Copy-Item -LiteralPath $sourceExe -Destination $exe
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $sourceHash) { throw 'Executable copy hash mismatch.' }
    foreach ($name in @('accounts.dat','servers.dat')) {
        Copy-Item -LiteralPath (Join-Path $sourceConfig $name) -Destination (Join-Path $dir ('config\' + $name))
    }
    $bootstrap = Join-Path $dir 'bootstrap.ini'
    @"
[Common]
Login=$($account.Login)
Server=$($account.Server)
KeepPrivate=1
NewsEnable=0
[Experts]
Enabled=0
AllowLiveTrading=0
AllowDllImport=0
Account=1
Profile=1
"@ | Set-Content -LiteralPath $bootstrap -Encoding Unicode
    $manifest += [PSCustomObject]@{
        account_id=$account.Id; login=$account.Login; server=$account.Server
        terminal_path=$exe; terminal_data_path=$dir; portable=$true
        executable_sha256=$sourceHash; prepared=$true
    }
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $rootPath 'manifest.json') -Encoding UTF8
if ($StartPrepared) {
    foreach ($row in $manifest) {
        $bootstrap = Join-Path $row.terminal_data_path 'bootstrap.ini'
        Start-Process -FilePath $row.terminal_path -ArgumentList @('/portable', ('/config:' + $bootstrap)) -WorkingDirectory $row.terminal_data_path -WindowStyle Hidden
    }
}
$manifest | ConvertTo-Json -Depth 4
