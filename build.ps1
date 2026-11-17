# Crea in dist\ l'MSI di PresetCraftAppLicator e l'installazione guidata che lo contiene.
# Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv\Scripts\python.exe)) {
    py -3.13 -m venv .venv
}
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements-build.txt
if (-not (Test-Path assets\icon.ico)) { .venv\Scripts\python.exe tools\make_icon.py }

$version = (Select-String -Path src\version.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
Remove-Item build, dist -Recurse -Force -ErrorAction SilentlyContinue

# 1. MSI con l'app (Python incluso)
.venv\Scripts\python.exe setup.py bdist_msi
if ($LASTEXITCODE -ne 0) { throw "MSI build failed" }
$msi = "dist\PresetCraftAppLicator-$version.msi"

# 2. Installazione guidata: un solo .exe con dentro l'MSI, la licenza e l'icona
#    (PyInstaller legge i percorsi rispetto a --specpath: qui sono tutti assoluti)
$root = $PSScriptRoot
#    (python -m PyInstaller: pyinstaller.exe ricorda il percorso del .venv e si rompe se il progetto viene spostato)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "PresetCraftAppLicator-Setup-$version" --icon "$root\assets\icon.ico" `
    --paths "$root\src" --collect-data sv_ttk --exclude-module PIL `
    --add-data "$root\$msi;payload" --add-data "$root\LICENSE;." `
    --add-data "$root\assets\icon.ico;assets" --add-data "$root\assets\icon.png;assets" `
    --distpath "$root\dist" --workpath "$root\build\setup" --specpath "$root\build\setup" `
    "$root\src\setup_wizard.py"
if ($LASTEXITCODE -ne 0) { throw "setup build failed" }

Get-ChildItem dist\* -Include *.msi, *.exe | ForEach-Object {
    "{0}  {1:N1} MB  SHA256 {2}" -f $_.Name, ($_.Length / 1MB), (Get-FileHash $_.FullName).Hash
}
