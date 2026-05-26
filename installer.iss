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
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create Desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[InstallDelete]
Type: files; Name: "{commondesktop}\PDF Stampila.lnk"
Type: files; Name: "{userdesktop}\PDF Stampila.lnk"
Type: files; Name: "{commondesktop}\StampilaPDF.lnk"
Type: files; Name: "{userdesktop}\StampilaPDF.lnk"

[Files]
Source: "dist\StampilaPDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\PDF Stampila"; Filename: "{app}\StampilaPDF.exe"; IconFilename: "{app}\StampilaPDF.exe"
Name: "{group}\Uninstall PDF Stampila"; Filename: "{uninstallexe}"
Name: "{userdesktop}\PDF Stampila"; Filename: "{app}\StampilaPDF.exe"; IconFilename: "{app}\StampilaPDF.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StampilaPDF.exe"; Description: "Launch PDF Stampila"; Flags: nowait postinstall skipifsilent
