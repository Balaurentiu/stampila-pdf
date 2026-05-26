[Setup]
AppName=PDF Stampila
AppVersion={#AppVersion}
AppPublisher=Balaurentiu
DefaultDirName={autopf}\StampilaPDF
DefaultGroupName=PDF Stampila
OutputDir=installer-output
OutputBaseFilename=StampilaPDF-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\StampilaPDF.exe
PrivilegesRequired=lowest

[Languages]
Name: "romanian"; MessagesFile: "compiler:Languages\Romanian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Creează iconiță pe Desktop"; GroupDescription: "Iconițe:"; Flags: unchecked

[Files]
Source: "dist\StampilaPDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\PDF Stampila"; Filename: "{app}\StampilaPDF.exe"
Name: "{group}\Dezinstalează PDF Stampila"; Filename: "{uninstallexe}"
Name: "{commondesktop}\PDF Stampila"; Filename: "{app}\StampilaPDF.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StampilaPDF.exe"; Description: "Lansează PDF Stampila"; Flags: nowait postinstall skipifsilent
