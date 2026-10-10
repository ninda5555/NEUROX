$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.bat first.' }
$startupFolder = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupFolder 'NEUROX Monitor.lnk'
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonPath
$shortcut.Arguments = '-m src.service'
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = 'NEUROX local market observation and paper evidence'
$shortcut.Save()
Write-Output "NEUROX will start after this Windows user signs in. Shortcut: $shortcutPath"
Write-Output 'It cannot run while the laptop is asleep or switched off.'
