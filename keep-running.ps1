# Keeps the local dashboard running. If it stops, this starts it again.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "data\keep-alive.log"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null

$mutex = New-Object System.Threading.Mutex($false, "Local\TalentCraftersKeepRunning")
if (-not $mutex.WaitOne(0, $false)) { exit 0 }

function Write-Log([string]$message) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
  Add-Content -Path $log -Value $line
}

function Test-Dashboard {
  $client = $null
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $wait = $client.BeginConnect("127.0.0.1", 8765, $null, $null)
    $opened = $wait.AsyncWaitHandle.WaitOne(1500, $false) -and $client.Connected
    return [bool]$opened
  } catch {
    return $false
  } finally {
    if ($client) { $client.Close() }
  }
}

Write-Log "Watching the dashboard."
try {
  while ($true) {
    if (Test-Dashboard) {
      Start-Sleep -Seconds 15
      continue
    }
    if (-not (Test-Path $python)) {
      Write-Log "Python was not found. The dashboard will be tried again."
      Start-Sleep -Seconds 30
      continue
    }
    Write-Log "Dashboard is down. Starting it."
    $proc = Start-Process -FilePath $python -ArgumentList "server.py" -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline -and -not $proc.HasExited) {
      if (Test-Dashboard) { break }
      Start-Sleep -Seconds 1
    }
    if ($proc.HasExited) {
      Write-Log "Dashboard process ended with code $($proc.ExitCode). Starting again shortly."
      Start-Sleep -Seconds 5
      continue
    }
    Write-Log "Dashboard is up."
    Wait-Process -Id $proc.Id
    Write-Log "Dashboard process ended. Starting again shortly."
    Start-Sleep -Seconds 5
  }
} finally {
  $mutex.ReleaseMutex() | Out-Null
  $mutex.Dispose()
}
