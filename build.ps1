# Build nestor.exe (PyInstaller, one folder, windowed) from this repository.
#
# Requirements: Windows 10/11 x64 and Python 3.13 x64. Then, in this folder:
#   py -3.13 -m venv .venv
#   .venv\Scripts\python -m pip install -r requirements-build.txt
#   powershell -ExecutionPolicy Bypass -File build.ps1
#
# Result: dist\NESTOR\nestor.exe with dist\NESTOR\_internal\ next to it.
# measure.py is a developer tool and is excluded from the build.
param([string]$Python = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
if (-not (Test-Path $Python)) { throw "Python not found: $Python (create .venv first, see README.md)" }
$out = Join-Path $src 'dist'
$work = Join-Path $src 'build'
$pyArgs = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'NESTOR',
            '--distpath', $out, '--workpath', $work, '--specpath', $work, '--paths', $src,
            '--exclude-module', 'measure', '--exclude-module', 'paths',
            '--add-data', "$src\items_ru.json;.",
            '--add-data', "$src\items_en.json;.",
            '--add-data', "$src\sparemag_weapons.json;.",
            (Join-Path $src 'nestor.py'))
# PyInstaller writes INFO lines to stderr; do not treat them as errors
$ErrorActionPreference = 'Continue'
& $Python @pyArgs *> (Join-Path $src 'build.log')
$rc = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($rc -ne 0) { Get-Content (Join-Path $src 'build.log') -Tail 20; throw "PyInstaller exit code $rc" }
Rename-Item (Join-Path $out 'NESTOR\NESTOR.exe') 'nestor.exe' -Force
"built: " + (Join-Path $out 'NESTOR\nestor.exe')
