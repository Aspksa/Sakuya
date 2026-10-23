# Windows PowerShell 5.1, UTF-8 with BOM. No third-party tray package.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$data = $env:SAKUYA_DATA_DIR
$root = $env:SAKUYA_ROOT
$session = $env:SAKUYA_SESSION
$ownerPid = [int]$env:SAKUYA_OWNER_PID
$icon = $null
$timer = $null
$context = $null
$owner = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $messages = Get-Content -LiteralPath (Join-Path $root 'config\messages.ru.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $state = Get-Content -LiteralPath (Join-Path $data 'run\instance.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.session -ne $session -or $state.pid -ne $ownerPid -or $state.url -notmatch '^http://127\.0\.0\.1:[0-9]+$') {
        throw 'Tray instance identity mismatch'
    }
    $owner = [Diagnostics.Process]::GetProcessById($ownerPid)
    # Force acquisition of the process handle; HasExited then resists PID reuse.
    $null = $owner.Handle
    $context = New-Object Windows.Forms.ApplicationContext
    $menu = New-Object Windows.Forms.ContextMenuStrip
    $openItem = $menu.Items.Add([string]$messages.open)
    $openItem.add_Click({ Start-Process -FilePath $state.url })
    $dataItem = $menu.Items.Add([string]$messages.data)
    $dataItem.add_Click({ Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $data + '"') })
    $logItem = $menu.Items.Add([string]$messages.log)
    $logItem.add_Click({ Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + (Join-Path $data 'logs\sakuya.log') + '"') })
    [void]$menu.Items.Add((New-Object Windows.Forms.ToolStripSeparator))
    $exitItem = $menu.Items.Add([string]$messages.exit)
    $exitItem.add_Click({
        try {
            $request = [Net.HttpWebRequest]::Create($state.url + '/api/shutdown')
            $request.Proxy = $null
            $request.Method = 'POST'
            $request.Timeout = 2000
            $request.ContentLength = 0
            $request.Headers['Authorization'] = 'Bearer ' + $state.token
            $response = $request.GetResponse()
            $response.Dispose()
            $exitItem.Enabled = $false
            $context.ExitThread()
        } catch {
            [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Sakuya')
        }
    })
    $icon = New-Object Windows.Forms.NotifyIcon
    $icon.Icon = [Drawing.SystemIcons]::Application
    $icon.Text = 'Sakuya'
    $icon.ContextMenuStrip = $menu
    $icon.add_DoubleClick({ Start-Process -FilePath $state.url })
    $icon.Visible = $true
    $timer = New-Object Windows.Forms.Timer
    $timer.Interval = 500
    $timer.add_Tick({
        if ($owner.HasExited -or (Test-Path -LiteralPath (Join-Path $data ('run\tray-stop-' + $session)))) {
            $context.ExitThread()
        }
    })
    $timer.Start()
    # Publish readiness from inside the running UI message loop, not before it.
    $readyTimer = New-Object Windows.Forms.Timer
    $readyTimer.Interval = 100
    $readyTimer.add_Tick({
        $readyTimer.Stop()
        $json = @{session=$session; pid=$PID} | ConvertTo-Json -Compress
        $target = Join-Path $data 'run\tray-ready.json'
        $temp = $target + '.' + $PID + '.tmp'
        [IO.File]::WriteAllText($temp, $json, (New-Object Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $target) { [IO.File]::Delete($target) }
        [IO.File]::Move($temp, $target)
    })
    $readyTimer.Start()
    [Windows.Forms.Application]::Run($context)
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
} finally {
    if ($timer) { $timer.Stop(); $timer.Dispose() }
    if ($readyTimer) { $readyTimer.Dispose() }
    if ($icon) { $icon.Visible = $false; $icon.Dispose() }
    if ($menu) { $menu.Dispose() }
    if ($context) { $context.Dispose() }
    if ($owner) { $owner.Dispose() }
}
