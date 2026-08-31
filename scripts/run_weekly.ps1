$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonExe = "C:\Users\addic\AppData\Local\Programs\Python\Python312\python.exe"
$script = Join-Path $root "scripts\youtube_trends.py"

$logDir = Join-Path $root "output\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("run_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

& $pythonExe $script *> $logFile
