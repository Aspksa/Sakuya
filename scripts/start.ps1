param(
    [ValidateSet('normal', 'diagnostic', 'quiet')][string]$Mode = 'normal',
    [switch]$Autostart,
    [ValidateSet('none', 'enable', 'disable')][string]$Startup = 'none'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$data = Join-Path $env:LOCALAPPDATA 'Sakuya'
if ($env:SAKUYA_DATA_DIR) {
    if (-not [IO.Path]::IsPathRooted($env:SAKUYA_DATA_DIR)) { throw 'SAKUYA_DATA_DIR must be absolute' }
    $data = [IO.Path]::GetFullPath($env:SAKUYA_DATA_DIR)
}
$env:SAKUYA_DATA_DIR = $data
$env:SAKUYA_ROOT = $root
$logDir = Join-Path $data 'logs'
$logPath = Join-Path $logDir 'bootstrap.log'
$messages = $null
$mutex = $null
$ownsMutex = $false
$script:activeStage = $null
$script:canRewrite = $false
$script:advice = ''
$env:SAKUYA_LAUNCH_STARTED_AT = ([double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000)).ToString('R', [Globalization.CultureInfo]::InvariantCulture)

function Write-Log([string]$Text, [switch]$Detail, [string]$Color = 'Gray', [switch]$FileOnly) {
    if (-not $FileOnly -and (-not $Detail -or $Mode -eq 'diagnostic')) { Write-Host $Text -ForegroundColor $Color }
    try { [IO.File]::AppendAllText($logPath, ((Get-Date -Format o) + ' ' + $Text + "`r`n"), [Text.Encoding]::UTF8) } catch { }
}

function Start-Stage([string]$Name) {
    $script:activeStage = [pscustomobject]@{Name=$Name; Watch=[Diagnostics.Stopwatch]::StartNew()}
    $line = $messages.console.running -f $Name
    if ($script:canRewrite) { Write-Host $line -ForegroundColor Cyan -NoNewline }
    else { Write-Host $line -ForegroundColor Cyan }
}

function Finish-Stage([ValidateSet('success','warning','error')][string]$Status = 'success') {
    if (-not $script:activeStage) { return }
    $stage = $script:activeStage
    $stage.Watch.Stop()
    $symbol = [char]0x2713
    $color = 'Green'
    if ($Status -eq 'warning') { $symbol = [char]0x26A0; $color = 'Yellow' }
    if ($Status -eq 'error') { $symbol = [char]0x2715; $color = 'Red' }
    $elapsed = $stage.Watch.Elapsed.TotalSeconds.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture)
    $line = $messages.console.finished -f $symbol, $stage.Name, $elapsed
    if ($script:canRewrite) {
        try {
            [Console]::Write("`r" + (' ' * ([Math]::Max(1, [Console]::WindowWidth - 1))) + "`r")
        } catch { $script:canRewrite = $false; Write-Host '' }
    }
    Write-Host $line -ForegroundColor $color
    Write-Log $line -Detail -FileOnly
    $script:activeStage = $null
}

function Show-StartupError([Exception]$Failure) {
    Finish-Stage -Status error
    $advice = $script:advice
    if ($Failure -is [UnauthorizedAccessException]) { $advice = $messages.console.help_permission }
    Write-Log $messages.console.failure -Color Red
    Write-Log ($messages.console.reason -f $Failure.Message) -Color Red
    Write-Log ($messages.console.action -f $advice) -Color Yellow
    Write-Log ($messages.console.log -f $logPath) -Color DarkYellow
}

function Quote-Argument([string]$Value) {
    # Windows CommandLineToArgvW rules, including trailing backslashes.
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $slashes + 1)))
            [void]$builder.Append('"')
        } else {
            [void]$builder.Append(('\' * $slashes))
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    [void]$builder.Append(('\' * (2 * $slashes)))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Test-Python([string]$Executable, [string[]]$Prefix = @()) {
    if (-not $Executable -or $Executable -match '\\WindowsApps\\') { return $null }
    $probe = "import sys,json,ssl,socket,http.server,urllib.request,hashlib,zipfile,ctypes,msvcrt,logging.handlers,threading; assert (3,11) <= sys.version_info[:2] < (3,16), sys.version; print(json.dumps({'exe':sys.executable,'version':sys.version.split()[0]}))"
    $arguments = @($Prefix) + @('-X', 'utf8', '-c', $probe)
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $Executable
    $psi.Arguments = (($arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $psi
    try {
        [void]$process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            Write-Log ($messages.python_timeout -f $Executable) -Detail
            return $null
        }
        $outputText = $outputTask.Result
        $errorText = $errorTask.Result
        if ($process.ExitCode -ne 0) {
            Write-Log ($messages.python_probe_failed -f $Executable, $errorText) -Detail
            return $null
        }
        $result = $outputText.Trim() | ConvertFrom-Json
        if (-not (Test-Path -LiteralPath $result.exe -PathType Leaf)) { return $null }
        Write-Log ($messages.python_ok -f $result.version, $result.exe) -Detail
        return $result.exe
    } catch {
        Write-Log ($messages.python_probe_failed -f $Executable, $_.Exception.Message) -Detail
        return $null
    } finally { $process.Dispose() }
}

function Find-Python {
    $candidates = @((Join-Path $root 'runtime\python\python.exe'),
                    (Join-Path $root 'python\python.exe'),
                    (Join-Path $root '.venv\Scripts\python.exe'))
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $valid = Test-Python $candidate
            if ($valid) { return $valid }
        }
    }
    $py = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($py) { $valid = Test-Python $py.Source @('-3'); if ($valid) { return $valid } }
    $python = Get-Command 'python.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($python) { $valid = Test-Python $python.Source; if ($valid) { return $valid } }
    $bases = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:SystemDrive) { $bases += ($env:SystemDrive + '\') }
    foreach ($base in $bases) {
        if ($base -and (Test-Path -LiteralPath $base)) {
            $folders = Get-ChildItem -LiteralPath $base -Directory -Filter 'Python*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending
            foreach ($folder in $folders) {
                $candidate = Join-Path $folder.FullName 'python.exe'
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $valid = Test-Python $candidate
                    if ($valid) { return $valid }
                }
            }
        }
    }
    return $null
}

function Install-LocalPython {
    if ($Autostart) { throw $messages.python_failed }
    Write-Log $messages.python_missing
    $answer = Read-Host
    if ($answer -notmatch '^(?i:y|yes|д|да)$') { throw $messages.python_failed }
    $manifest = Get-Content -LiteralPath (Join-Path $root 'config\python-runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $arch = '32'
    if ([Environment]::Is64BitOperatingSystem) { $arch = '64' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { $arch = 'arm64' }
    $package = $manifest.architectures.$arch
    $uri = [Uri]$package.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'www.python.org' -or $uri.AbsolutePath -notlike '/ftp/python/*' -or $package.sha256 -notmatch '^[a-f0-9]{64}$') {
        throw 'Invalid pinned Python manifest'
    }
    $runtime = Join-Path $root 'runtime'
    [void][IO.Directory]::CreateDirectory($runtime)
    $stage = Join-Path $runtime ('prepare-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($stage)
    $archive = Join-Path $stage 'python.zip'
    $unpacked = Join-Path $stage 'unpacked'
    try {
        Start-Stage $messages.console.stage_install
        $script:advice = $messages.console.help_network
        Write-Log $messages.download -Detail
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $uri.AbsoluteUri -OutFile $archive -TimeoutSec 90 -MaximumRedirection 0
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $package.sha256) {
            $script:advice = $messages.console.help_hash
            throw $messages.console.hash_mismatch
        }
        $script:advice = $messages.console.help_python
        # Literal extraction supports [, ], ! and Cyrillic paths too.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
        $checked = Test-Python (Join-Path $unpacked 'python.exe')
        if (-not $checked) { throw $messages.python_failed }
        $target = Join-Path $runtime 'python'
        $backup = Join-Path $runtime 'python.previous'
        if (Test-Path -LiteralPath $target) {
            # Only an invalid target reaches here. Preserve it until replacement is ready.
            if (Test-Path -LiteralPath $backup) { $backup += '-' + [Guid]::NewGuid().ToString('N') }
            [IO.Directory]::Move($target, $backup)
        }
        try { [IO.Directory]::Move($unpacked, $target) }
        catch {
            if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $target)) { [IO.Directory]::Move($backup, $target) }
            throw
        }
        return (Join-Path $target 'python.exe')
    } finally { if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force } }
}

try {
    $messages = Get-Content -LiteralPath (Join-Path $root 'config\messages.ru.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $script:advice = $messages.console.help_prepare
    try { $script:canRewrite = $Mode -ne 'diagnostic' -and -not [Console]::IsOutputRedirected -and [Console]::WindowWidth -gt 30 } catch { }
    Write-Log $messages.banner -Color DarkYellow
    Start-Stage $messages.console.stage_prepare
    [void][IO.Directory]::CreateDirectory($logDir)
    # Serialize environment preparation only; the Python supervisor owns the lifetime lock.
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\aspksa.sakuya.prepare.' + $digest))
    try { $ownsMutex = $mutex.WaitOne(180000) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Environment preparation timed out; see bootstrap.log' }
    # Recover a runtime directory rename interrupted before promotion.
    $runtimeTarget = Join-Path $root 'runtime\python'
    $runtimeBackup = Join-Path $root 'runtime\python.previous'
    if (-not (Test-Path -LiteralPath $runtimeTarget) -and (Test-Path -LiteralPath $runtimeBackup)) {
        [IO.Directory]::Move($runtimeBackup, $runtimeTarget)
    }
    Finish-Stage
    $script:advice = $messages.console.help_python
    Start-Stage $messages.console.stage_python
    $pythonExe = Find-Python
    if (-not $pythonExe) {
        Finish-Stage -Status warning
        $pythonExe = Install-LocalPython
    }
    Finish-Stage
    if ($Startup -ne 'none') {
        & $pythonExe -X utf8 (Join-Path $root 'bootstrap\startup.py') $Startup
        if ($LASTEXITCODE -ne 0) { throw 'Autostart configuration failed' }
        exit 0
    }
    $mutex.ReleaseMutex()
    $ownsMutex = $false
    $arguments = @('-X', 'utf8', (Join-Path $root 'bootstrap\launch.py'), '--mode', $Mode)
    if ($Autostart) { $arguments += '--autostart' }
    & $pythonExe @arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Log ($messages.error + '. ' + $messages.log + ': ' + $logPath)
        if ($Autostart) { [void](Read-Host $messages.console.press_enter) }
    }
    exit $code
} catch {
    if ($messages) { Show-StartupError $_.Exception }
    else { Write-Host ('Sakuya startup failed: ' + $_.Exception.Message + '; Log: ' + $logPath) }
    if ($Autostart) { [void](Read-Host $messages.console.press_enter) }
    exit 1
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
