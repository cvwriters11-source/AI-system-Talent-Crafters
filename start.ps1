$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
  throw "Python 3.12 was not found. Install it, then run this script again."
}
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  & $py -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe server.py
