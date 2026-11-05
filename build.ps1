# Ambar.exe derleme betiği
# Kullanım:  powershell -ExecutionPolicy Bypass -File build.ps1
# Çıktı:     dist\Ambar.exe  ve  dist\Ambar.exe.sha256
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1) Sürümü ambar.py'den oku
$surum = (Select-String -Path ambar.py -Pattern '^SURUM = "(.+)"').Matches[0].Groups[1].Value
$parca = ($surum.Split(".") + @("0", "0", "0", "0"))[0..3] -join ", "
Write-Host "Ambar v$surum derleniyor..."

# 2) adb (Android Platform-Tools) yoksa indir
if (-not (Test-Path "platform-tools\adb.exe")) {
    Write-Host "platform-tools indiriliyor..."
    $zip = Join-Path $env:TEMP "platform-tools.zip"
    Invoke-WebRequest "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive $zip -DestinationPath . -Force
    Remove-Item $zip
}

# 3) PyInstaller
python -m pip install --quiet --upgrade pyinstaller

# 4) exe "Özellikler > Ayrıntılar" bilgileri
$yil = (Get-Date).Year
New-Item -ItemType Directory -Force build | Out-Null
$versiyon = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parca), prodvers=($parca), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041F04B0', [
      StringStruct('CompanyName', 'Yunus Kaya'),
      StringStruct('FileDescription', 'Ambar - Android Veri Alma Aracı'),
      StringStruct('FileVersion', '$surum'),
      StringStruct('InternalName', 'Ambar'),
      StringStruct('LegalCopyright', '© $yil Yunus Kaya - yunuskaya.com.tr'),
      StringStruct('OriginalFilename', 'Ambar.exe'),
      StringStruct('ProductName', 'Ambar'),
      StringStruct('ProductVersion', '$surum')])]),
    VarFileInfo([VarStruct('Translation', [1055, 1200])])
  ]
)
"@
# BOM'suz UTF-8 (PowerShell 5.1'in Set-Content'i BOM ekler, PyInstaller'ı bozar)
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "build\version_info.txt"), $versiyon, (New-Object Text.UTF8Encoding $false))

# 5) Derle (adb içine gömülü, tek dosya, konsolsuz)
# Not: --specpath verildiğinde göreli yollar o klasöre göre çözülür; bu yüzden mutlak yol kullanılır
$K = $PSScriptRoot
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name Ambar --icon "$K\icon.ico" --version-file "$K\build\version_info.txt" `
    --add-data "$K\platform-tools\adb.exe;platform-tools" `
    --add-data "$K\platform-tools\AdbWinApi.dll;platform-tools" `
    --add-data "$K\platform-tools\AdbWinUsbApi.dll;platform-tools" `
    --add-data "$K\icon.ico;." --add-data "$K\icon.png;." `
    --distpath "$K\dist" --workpath "$K\build\work" --specpath "$K\build" `
    "$K\ambar_gui.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller başarısız" }

# 6) SHA-256
$hash = (Get-FileHash dist\Ambar.exe -Algorithm SHA256).Hash.ToLower()
"$hash  Ambar.exe" | Set-Content dist\Ambar.exe.sha256 -Encoding ascii
Write-Host "`nTAMAM: dist\Ambar.exe  ($([math]::Round((Get-Item dist\Ambar.exe).Length/1MB,1)) MB)"
Write-Host "SHA-256: $hash"
