$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
# Validate original schemas before packaging.
python -c "from check764p.xml_input import schema; schema('schemas')"
if ($LASTEXITCODE -ne 0) { throw 'Original input schemas are missing or invalid.' }
if (!(Test-Path 'check764p/app.py')) { throw 'Application entry point missing.' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Check764P --paths . --add-data 'schemas;schemas' check764p/app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
