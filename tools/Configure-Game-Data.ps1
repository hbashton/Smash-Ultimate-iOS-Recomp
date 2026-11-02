param(
    [string]$RomfsPath,
    [long]$Offset = 0,
    [string]$FontsDirectory,
    [string]$AocConfig,
    [switch]$NonInteractive,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $installRoot = Split-Path -Parent $PSScriptRoot
    if (@(Get-Process -Name ssbu,ssbu_new -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'Close every SSBU game window before changing data configuration.'
    }
    if (-not $RomfsPath) {
        if ($NonInteractive) { throw 'RomfsPath is required.' }
        $RomfsPath = Read-Host 'Path to your patched, unencrypted romfs.bin (or embedded RomFS file)'
        $offsetInput = Read-Host 'RomFS byte offset [0 for raw romfs.bin]'
        if ($offsetInput) { $Offset = [long]::Parse($offsetInput) }
    }
    if (-not $FontsDirectory) {
        if ($NonInteractive) { throw 'FontsDirectory is required.' }
        $FontsDirectory = Read-Host 'Folder containing your six firmware .bfttf fonts'
    }
    if (-not $AocConfig -and -not $NonInteractive) {
        $AocConfig = Read-Host 'Optional path to your existing aoc.cfg [Enter to leave DLC configuration unchanged]'
    }
    $RomfsPath = $RomfsPath.Trim().Trim('"')
    $FontsDirectory = $FontsDirectory.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $RomfsPath -PathType Leaf)) { throw 'RomFS input file not found.' }
    if (-not (Test-Path -LiteralPath $FontsDirectory -PathType Container)) { throw 'Font directory not found.' }
    $RomfsPath = (Resolve-Path -LiteralPath $RomfsPath).Path
    $FontsDirectory = (Resolve-Path -LiteralPath $FontsDirectory).Path
    $pathEncoding = [Text.Encoding]::Default
    if ($pathEncoding.GetString($pathEncoding.GetBytes($RomfsPath)) -cne $RomfsPath) {
        throw 'The runtime cannot represent this RomFS path. Use a path supported by the Windows system code page.'
    }
    $stream = [IO.File]::OpenRead($RomfsPath)
    try {
        if ($Offset -lt 0 -or $Offset -gt ($stream.Length - 80)) { throw 'RomFS byte offset is outside the file.' }
        [void]$stream.Seek($Offset, [IO.SeekOrigin]::Begin)
        $reader = [IO.BinaryReader]::new($stream)
        [byte[]]$header = $reader.ReadBytes(80)
        if ($header.Length -ne 80 -or [BitConverter]::ToUInt64($header, 0) -ne 80) {
            throw 'No unencrypted RomFS header at this offset. Prepare the patched image before running setup.'
        }
        [uint64]$available = $stream.Length - $Offset
        foreach ($field in @(1, 3, 5, 7)) {
            [uint64]$start = [BitConverter]::ToUInt64($header, 8 * $field)
            [uint64]$size = [BitConverter]::ToUInt64($header, 8 * ($field + 1))
            if ($start -gt $available -or $size -gt ($available - $start)) { throw 'RomFS metadata is outside the file.' }
        }
        if ([BitConverter]::ToUInt64($header, 72) -gt $available) { throw 'Invalid RomFS data offset.' }
    } finally {
        $stream.Dispose()
    }
    $fonts = @(
        'nintendo_udsg-r_std_003.bfttf', 'nintendo_udsg-r_org_zh-cn_003.bfttf',
        'nintendo_udsg-r_ext_zh-cn_003.bfttf', 'nintendo_udjxh-db_zh-tw_003.bfttf',
        'nintendo_udsg-r_ko_003.bfttf', 'nintendo_ext_003.bfttf'
    )
    foreach ($name in $fonts) {
        $font = Join-Path $FontsDirectory $name
        if (-not (Test-Path -LiteralPath $font -PathType Leaf)) { throw ('Missing required font: ' + $name) }
        if ((Get-Item -LiteralPath $font).Length -lt 8) { throw ('Truncated font file: ' + $name) }
    }
    if ($AocConfig) {
        $AocConfig = $AocConfig.Trim().Trim('"')
        if (-not (Test-Path -LiteralPath $AocConfig -PathType Leaf)) { throw 'Optional aoc.cfg file not found.' }
        $AocConfig = (Resolve-Path -LiteralPath $AocConfig).Path
    }
    if ($DryRun) {
        Write-Host 'RomFS header, offset and six font files validated. Dry run: nothing written.'
        exit 0
    }
    $moduleDir = Join-Path $installRoot 'dump\modules'
    $fontDir = Join-Path $installRoot 'dump\fonts'
    [void][IO.Directory]::CreateDirectory($moduleDir)
    [void][IO.Directory]::CreateDirectory($fontDir)
    $stamp = (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '-' + [guid]::NewGuid().ToString('N')
    foreach ($name in $fonts) {
        $source = Join-Path $FontsDirectory $name
        $target = Join-Path $fontDir $name
        if ([IO.Path]::GetFullPath($source) -ieq [IO.Path]::GetFullPath($target)) { continue }
        if (Test-Path -LiteralPath $target) { [IO.File]::Copy($target, ($target + '.before-setup-' + $stamp), $false) }
        [IO.File]::Copy($source, $target, $true)
    }
    if ($AocConfig) {
        $aocTarget = Join-Path $moduleDir 'aoc.cfg'
        if ($AocConfig -ine $aocTarget) {
            if (Test-Path -LiteralPath $aocTarget) { [IO.File]::Copy($aocTarget, ($aocTarget + '.before-setup-' + $stamp), $false) }
            [IO.File]::Copy($AocConfig, $aocTarget, $true)
        }
    }
    $cfg = Join-Path $moduleDir 'romfs.cfg'
    $temporary = $cfg + '.' + $stamp + '.tmp'
    [IO.File]::WriteAllText($temporary, ($RomfsPath + "`r`n" + $Offset + "`r`n"), $pathEncoding)
    if (Test-Path -LiteralPath $cfg) {
        [IO.File]::Replace($temporary, $cfg, ($cfg + '.before-setup-' + $stamp))
    } else {
        [IO.File]::Move($temporary, $cfg)
    }
    Write-Host 'Game-data setup complete. Run Play SSBU.bat.'
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
