; Installateur AndroMirror (Inno Setup 6). Optionnel : la CI le construit si on le demande.
; Compilation locale : ISCC.exe build\AndroMirror.iss   (après avoir construit dist\AndroMirror.exe)

#define AppName "AndroMirror"
#define AppVersion "0.2.0"
#define AppExe "AndroMirror.exe"

[Setup]
AppId={{B6A1C0E2-5D2F-4C57-9C1A-3E7D0A9F4A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AndroMirror
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=AndroMirror-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
InfoBeforeFile=..\third_party\AVIS-LICENCES.txt

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\third_party\AVIS-LICENCES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

; Les réglages et le journal (%APPDATA%\AndroMirror) sont volontairement conservés à la désinstallation.
