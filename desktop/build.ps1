# Builds the Windows app: dist\R6MatchStats (the app), dist\R6MatchStats-Setup.exe
# (the installer), dist\R6MatchStats-Windows.zip (the portable version) and
# dist\R6MatchStats-Browser.html (the browser edition).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File desktop\build.ps1
# Needs Python 3.12+ (uses .venv if present) and Go 1.23+.
# Inno Setup 6 is installed for the installer with winget or choco if missing.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$go = Get-Command go -ErrorAction SilentlyContinue
if (-not $go) {
    throw "Go 1.23+ is required to build the bundled parser from current source. Install Go from https://go.dev/dl/."
}
# Never package an existing parser binary: it may predate the source and silently omit stats fixes.
# -trimpath keeps this PC's folder paths out of the exe. Don't strip it (-ldflags "-s -w"):
# antivirus programs tend to flag stripped Go programs.
go build -trimpath -o r6-dissect.exe .
if ($LASTEXITCODE) { throw "go build failed" }

$python = if (Test-Path .venv\Scripts\python.exe) { ".venv\Scripts\python.exe" } else { "python" }
& $python -m pip install --quiet -r scripts\requirements.txt -r desktop\requirements-build.txt
if ($LASTEXITCODE) { throw "pip install failed" }

# the GitHub repo that the app's "check for a newer version" link points to
$repo = $env:GITHUB_REPOSITORY
if (-not $repo) { $repo = (git remote get-url origin) -replace '^.*github\.com[:/]', '' -replace '\.git$', '' }
New-Item -ItemType Directory -Force build | Out-Null
Set-Content -Path build\repo.txt -Value $repo -Encoding ascii
# the app's version: the numbers in $env:R6_VERSION (the release workflow passes the tag,
# e.g. v1.2.0 or app-v1.2.0), else the one in app_info.py
$version = [regex]::Match("$env:R6_VERSION", '\d+(\.\d+){0,3}').Value
if (-not $version) {
    $version = (Select-String -Path scripts\app_info.py -Pattern '"([\d.]+)"\s*$' | Where-Object { $_.Line -match 'APP_VERSION' }).Matches[0].Groups[1].Value
}
Set-Content -Path build\version.txt -Value $version -Encoding ascii
# the notice the installer shows before installing (the same text as the app's About section)
& $python -c "import sys; sys.path.insert(0, 'scripts'); import app_info; print(app_info.NOTICE + '\n\n' + app_info.HOW_IT_WORKS)" |
    Set-Content -Path build\notice.txt -Encoding utf8

& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller desktop\R6MatchStats.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

# Code signing, with the certificate in R6_SIGN_PFX (desktop\sign.ps1): the app's own programs are
# signed now, before their SHA-256s go into the file list below, and Inno Setup signs the
# installer and uninstaller. The Python and Windows files PyInstaller bundles come signed already.
$sign = [bool]$env:R6_SIGN_PFX
Remove-Item dist\SIGNATURE.txt -ErrorAction SilentlyContinue
if ($sign) {
    $env:R6_SIGN_URL = "https://github.com/$repo"
    $programs = @("dist\R6MatchStats\R6MatchStats.exe") +
        @(Get-ChildItem dist\R6MatchStats -Recurse -Filter r6-dissect.exe | ForEach-Object FullName)
    if ($programs.Count -lt 2) { throw "r6-dissect.exe wasn't found in dist\R6MatchStats to sign" }
    & "$PSScriptRoot\sign.ps1" @programs
} else {
    Write-Host "Not signing: set R6_SIGN_PFX (and R6_SIGN_PFX_PASSWORD) to sign the app with a code-signing certificate."
}

# the list of every file and its SHA-256, which the app checks each time it starts (desktop/integrity.py)
& $python desktop\integrity.py create dist\R6MatchStats
if ($LASTEXITCODE) { throw "writing the app's file list failed" }

# the portable zip. Python's zipfile, with retries: antivirus can briefly lock the files PyInstaller just wrote
foreach ($attempt in 1..3) {
    & $python -c "import shutil; shutil.make_archive('dist/R6MatchStats-Windows', 'zip', 'dist', 'R6MatchStats')"
    if (-not $LASTEXITCODE) { break }
    Start-Sleep -Seconds 5
}
if ($LASTEXITCODE) { throw "zipping dist\R6MatchStats failed" }

# the installer
function Find-Iscc {
    $found = Get-Command iscc -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    foreach ($dir in "${env:ProgramFiles(x86)}\Inno Setup 6", "$env:ProgramFiles\Inno Setup 6", "$env:LOCALAPPDATA\Programs\Inno Setup 6") {
        if (Test-Path "$dir\ISCC.exe") { return "$dir\ISCC.exe" }
    }
}
$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "Installing Inno Setup 6..."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements | Out-Null
    } elseif (Get-Command choco -ErrorAction SilentlyContinue) {
        choco install innosetup -y --no-progress | Out-Null
    }
    $iscc = Find-Iscc
    if (-not $iscc) { throw "Install Inno Setup 6 (https://jrsoftware.org/isdl.php) to build the installer." }
}
$isccArgs = @("/Q", "/DAppVersion=$version", "/DAppRepo=$repo")
if ($sign) {
    # Inno Setup's "r6sign" tool (installer.iss): $q is a quote, $f the file it's signing
    $isccArgs += "/DSign", ('/Sr6sign=powershell.exe -NoProfile -ExecutionPolicy Bypass -File $q' + "$PSScriptRoot\sign.ps1" + '$q $f')
}
& $iscc @isccArgs desktop\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

# who signed it, published with the release so the download page can say (app_info.latest_release)
if ($sign) {
    $signer = (Get-AuthenticodeSignature dist\R6MatchStats-Setup.exe).SignerCertificate
    if (-not $signer) { throw "The installer came out unsigned" }
    $name = $signer.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
    Set-Content -Path dist\SIGNATURE.txt -Encoding utf8 -Value "Signed by: $name", "Thumbprint: $($signer.Thumbprint)"
}

# the browser edition (browser/build.py): the app as one HTML page that reads replays in the browser
& $python browser\build.py --out build\browser --version $version
if ($LASTEXITCODE) { throw "building the browser edition failed" }
Copy-Item build\browser\R6MatchStats.html dist\R6MatchStats-Browser.html

# checksums people can compare their download against (Get-FileHash shows the same value)
$sums = foreach ($file in "R6MatchStats-Setup.exe", "R6MatchStats-Windows.zip", "R6MatchStats-Browser.html") {
    "$((Get-FileHash "dist\$file" -Algorithm SHA256).Hash.ToLower())  $file"
}
Set-Content -Path dist\SHA256SUMS.txt -Value $sums -Encoding ascii

Write-Host "Built R6 Match Stats $version`:"
Write-Host "  dist\R6MatchStats-Setup.exe   (installer)"
Write-Host "  dist\R6MatchStats-Windows.zip (portable)"
Write-Host "  dist\R6MatchStats-Browser.html (browser edition)"
Write-Host "  dist\SHA256SUMS.txt           (checksums of all three)"
if ($sign) { Write-Host "  dist\SIGNATURE.txt           (signed by $name)" } else { Write-Host "  (not code-signed)" }
