; DOK Scanner — Inno Setup 6 installer script (HLD §21).
; Build: compile with Inno Setup 6 after `pyinstaller docscanner.spec`.
; Deploy: copy DOKScanner_Setup.exe to USB; supports /VERYSILENT /NORESTART
; for Group Policy / batch deployment (HLD §21.2).

#define AppName "DOK Scanner"
#define AppVersion "1.0.0"
#define AppPublisher "DOK Solutions"
#define AppExeName "DOKScanner.exe"

[Setup]
AppId={{6F1B0A3E-Dok-Scan-2026-Batch0Capture}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DOKScanner
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; per-machine install; data lives per-user in %LOCALAPPDATA%\DOKScanner
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=DOKScanner_Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; SQLite DB and project configs in %LOCALAPPDATA% survive upgrades (HLD §21.2)
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Release bundle extras (present when built with OCR/barcode components):
Source: "..\dist\tesseract\*"; DestDir: "{app}\tesseract"; \
    Flags: ignoreversion recursesubdirs skipifsourcedoesntexist
Source: "..\vendor_sdks\*"; DestDir: "{app}\vendor_sdks"; \
    Flags: ignoreversion recursesubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent
