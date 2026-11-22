# Breachcut installer (run by Install.bat). Sets up Python, Pillow and FFmpeg, copies Breachcut to
# %LOCALAPPDATA%\Breachcut, builds its artwork, adds Desktop + Start menu shortcuts and opens it.
$ErrorActionPreference = 'Continue'
$Here = $PSScriptRoot
$Dest = Join-Path $env:LOCALAPPDATA 'Breachcut'
$Old = Join-Path $env:LOCALAPPDATA 'FedClipper'

Write-Host ''
Write-Host '  ======================================' -ForegroundColor Magenta
Write-Host '    BREACHCUT  -  installing...' -ForegroundColor Magenta
Write-Host '  ======================================' -ForegroundColor Magenta
Write-Host ''

function Fail($msg) {
    Write-Host ''
    Write-Host "  $msg" -ForegroundColor Red
    exit 1
}

function Find-Python {
    foreach ($launcher in @('py', (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'))) {
        try {
            $p = & $launcher -3 -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p.Trim())) { return $p.Trim() }
        } catch {}
    }
    $dirs = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
    foreach ($d in $dirs) {
        $exe = Join-Path $d.FullName 'python.exe'
        if (Test-Path $exe) { return $exe }
    }
    return $null
}

$hasWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)

# ---------- 1. Python ----------
$py = Find-Python
if (-not $py) {
    if (-not $hasWinget) { Fail 'Python is missing. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run Install.bat again.' }
    Write-Host '  - Installing Python (one time, takes a minute)...'
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements | Out-Null
    $py = Find-Python
}
if (-not $py) { Fail 'Could not set up Python. Install it from https://www.python.org/downloads/ and run Install.bat again.' }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
Write-Host '  - Python ready'

# ---------- 2. Pillow ----------
& $py -m pip install --user --upgrade --quiet --disable-pip-version-check pillow 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Couldn't install Pillow. Check your internet connection and run Install.bat again." }
Write-Host '  - Image support ready'
& $py -m pip install --user --upgrade --quiet --disable-pip-version-check PyAudioWPatch 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host '  - Game sound capture ready' }

# ---------- 3. App files ----------
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if ((Test-Path (Join-Path $Old 'settings.json')) -and -not (Test-Path (Join-Path $Dest 'settings.json'))) {
    foreach ($f in 'settings.json', 'processed.json', 'edits.json') {
        if (Test-Path (Join-Path $Old $f)) { Copy-Item (Join-Path $Old $f) $Dest -Force }
    }
    foreach ($d in 'thumbs', 'ffmpeg', 'assets') {
        if (Test-Path (Join-Path $Old $d)) { Copy-Item (Join-Path $Old $d) $Dest -Recurse -Force }
    }
    Write-Host '  - Moved your Fed Clipper settings and history over'
}
Copy-Item (Join-Path $Here 'app\*') $Dest -Recurse -Force
Get-ChildItem $Dest -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
try {
    $ver = (Get-Content (Join-Path $Here 'version.json') -Raw | ConvertFrom-Json).version
    if ($ver) { Set-Content -Path (Join-Path $Dest 'version.txt') -Value $ver -NoNewline -Encoding ASCII }
} catch {}
Write-Host "  - App installed to $Dest"

# ---------- 4. Artwork ----------
& $py (Join-Path $Dest 'make_assets.py') (Join-Path $Dest 'assets') | Out-Null
Write-Host '  - Artwork ready'

# ---------- 5. FFmpeg ----------
$ffDir = Join-Path $Dest 'ffmpeg\bin'
if (-not (Test-Path (Join-Path $ffDir 'ffmpeg.exe')) -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if ($hasWinget) {
        Write-Host '  - Installing FFmpeg video engine (one time)...'
        winget install -e --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements | Out-Null
        $found = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($found) {
            New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
            foreach ($t in 'ffmpeg.exe', 'ffprobe.exe', 'ffplay.exe') {
                $src = Join-Path $found.DirectoryName $t
                if (Test-Path $src) { Copy-Item $src $ffDir -Force }
            }
        }
    } else {
        Write-Host '  ! FFmpeg could not be installed automatically (winget is missing).' -ForegroundColor Yellow
    }
}
Write-Host '  - Video engine ready'

# ---------- 6. Shortcuts + launch ----------
& (Join-Path $Dest 'make_shortcut.ps1') -Target $pyw -Script (Join-Path $Dest 'breachcut.py') -Dir $Dest `
    -Icon (Join-Path $Dest 'assets\breachcut.ico')
Write-Host ''
Write-Host '  All done! Opening Breachcut...' -ForegroundColor Green
Write-Host '  From now on just double-click "Breachcut" on your Desktop.'
Write-Host ''
Start-Process -FilePath $pyw -ArgumentList ('"' + (Join-Path $Dest 'breachcut.py') + '"') -WorkingDirectory $Dest
Start-Sleep -Seconds 5
exit 0
