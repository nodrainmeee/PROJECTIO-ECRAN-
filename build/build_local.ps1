# Construction locale de AndroMirror.exe sous Windows (PowerShell, Python 3.12 installé).
# Prérequis :
#  - dossier platform-tools\ à la racine du projet contenant adb.exe, AdbWinApi.dll, AdbWinUsbApi.dll
#    (à télécharger depuis https://developer.android.com/tools/releases/platform-tools) ;
#  - server_android\build\andromirror-server.jar : le serveur Android. Le plus simple : prenez l'artefact
#    « andromirror-server » du dernier build GitHub Actions et placez le .jar à cet endroit
#    (ou construisez-le avec un JDK 17 + Android SDK : bash server_android/build.sh).
# Utilisation, depuis la racine du projet :  powershell -ExecutionPolicy Bypass -File build\build_local.ps1
$ErrorActionPreference = "Stop"
if (-not (Test-Path "platform-tools\adb.exe")) { throw "platform-tools\adb.exe introuvable" }
if (-not (Test-Path "server_android\build\andromirror-server.jar")) { throw "server_android\build\andromirror-server.jar introuvable (voir l'en-tête du fichier)" }
python -m pip install -r requirements.txt -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Les tests ont échoué" }
Remove-Item Env:QT_QPA_PLATFORM
pyinstaller --noconfirm --onefile --windowed --name AndroMirror --icon assets/icon.ico `
  --add-data "assets;assets" --add-data "third_party;third_party" --add-data "platform-tools;platform-tools" `
  --add-data "server_android\build\andromirror-server.jar;server" --collect-all av main.py
Write-Host "Terminé : dist\AndroMirror.exe"
