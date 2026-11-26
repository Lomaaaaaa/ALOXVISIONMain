; Inno Setup 6 script of ALOX Vision (Python): installs the PyInstaller folder AloxVisionPy next to LabVIEW.
;
;   ISCC.exe /DAppVersion=0.3.0 /DDistDir=<...>\AloxVisionPy /DOutputDir=<...> packaging\windows\alox.iss
;
; (build.ps1 passes the three values; the defaults below are those of build.ps1.) Needs Inno Setup 6.3 or later.
;
; SIDE BY SIDE WITH LABVIEW. The LabVIEW application (camera server C:\Program Files\alox\AloxCamServer.exe, the Vision
; Core application next to it, its alox_setup.ini, translations, settings D:\alox\settings, data D:\alox\02-Vision) is
; never modified, moved or deleted: this setup has its own AppId, its own folder C:\Program Files\Aloxtec\AloxVisionPy,
; its own Start-menu group "Alox Vision (Python)", and writes no registry key, environment variable, service or file
; association. The optional page "LabVIEW installation" only READS the LabVIEW application folder (two kinds of files
; copied, see [Files]). Only one of the two applications may run at a time (they share the stages, cameras and ports);
; the uninstaller removes this application only (the rollback: LabVIEW is ready to start again).
;
; WHAT GOES WHERE ({app} = C:\Program Files\Aloxtec\AloxVisionPy, i.e. <exe> of the application):
;   {app}\alox.exe, alox-ui.exe, *_check.exe, _internal\   the build (replaced at every install; _internal\ is cleaned).
;   {app}\alox_setup.ini          The machine's [folders]: NEVER written by this setup and never removed by the
;                                 uninstaller, so an update keeps it. The template is installed as
;                                 {app}\alox_setup.ini.example (copy it to alox_setup.ini and edit, or copy LabVIEW's
;                                 alox_setup.ini to share its folders). Without it the LabVIEW defaults D:\alox\... apply.
;   {app}\alox_default.ini        The defaults copied to <settings>\alox.ini at the first start. Copied from the LabVIEW
;                                 application folder (page "LabVIEW installation") when it is not there yet, else by
;                                 hand. Kept by the uninstaller.
;   {app}\translations\*.lang     LabVIEW's <application>\translations\*.lang (alox_en.lang, system_en.lang, mesa_en.lang,
;                                 tilt_en.lang, bowing_en.lang, ...; the LabVIEW location is inferred, check it on the
;                                 machine). Copied from the LabVIEW folder like alox_default.ini, else by hand.
;   {app}\licence_public_key.txt  The public key of tools\licence_generator.py keygen; installed when the build has one
;                                 (build.ps1 -LicencePublicKeyFile). Only used when alox.ini [general] licence_BOOL =
;                                 TRUE (off by default).
;   {app}\licence.alox            The machine's licence file, issued for this PC (tools\licence_generator.py issue):
;                                 copied there by hand, never by this setup; kept by the uninstaller.
;   IDS GenTL producer            Not installed here: IDS peak (with the "Kernel (GEVK)" GigE Vision transport layer)
;                                 installs the .cti files under C:\Program Files\IDS\ids_peak\...\ and sets
;                                 GENICAM_GENTL64_PATH, which the application scans. To load one producer only, add
;                                 --genicam-producer "C:\Program Files\IDS\ids_peak\...\ids_gevgentlk.cti" to the
;                                 Parameters of the "Alox Vision" shortcut (find it: dir /s /b "C:\Program Files\IDS\*.cti").
;   Passwords                     Never part of the build or of this setup: no alox.ini, no [user.password] section.
;   Per-user files                %LOCALAPPDATA%\Aloxtec\AloxVisionPy\logs (log of alox-ui.exe), ...\demo (simulation
;                                 demo), Documents\AloxVisionPy\reports (check-tool reports): kept by the uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "dist\AloxVisionPy"
#endif
#ifndef OutputDir
  #define OutputDir "dist\installer"
#endif
#define AppName "ALOX Vision (Python)"
#define Group "Alox Vision (Python)"

[Setup]
; The AppId identifies this application for updates and uninstallation. Never change it, never reuse LabVIEW's.
AppId={{CB459C99-028B-41CE-9091-13E11DCD8217}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Aloxtec
DefaultDirName={commonpf64}\Aloxtec\AloxVisionPy
; Fixed folder (the documented one); an administrator can still give another with /DIR="..." on the command line.
DisableDirPage=yes
DefaultGroupName={#Group}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=AloxVisionPy-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\alox.exe
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
InfoAfterFile=after_install.txt
; Code signing (open question): with a certificate, define a sign tool in the IDE (Tools > Configure Sign Tools), e.g.
;   signtool=signtool.exe sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a $f
; and uncomment:
; SignTool=signtool
; SignedUninstaller=yes

[Dirs]
Name: "{app}\translations"

[InstallDelete]
; An update replaces the whole bundle: no module of an older version may stay in _internal.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The PyInstaller folder (every executable and _internal\). Machine files are excluded in case the build folder has
; some (a test run): they are never shipped.
Source: "{#DistDir}\*"; DestDir: "{app}"; Excludes: "alox_setup.ini,alox_default.ini,licence.alox,licence_public_key.txt,translations"; Flags: ignoreversion recursesubdirs createallsubdirs
; The licence public key, when the build has one.
Source: "{#DistDir}\licence_public_key.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "README.md"; DestDir: "{app}"; DestName: "README-installation.md"; Flags: ignoreversion
; From the LabVIEW application folder (page "LabVIEW installation"; read only, only when the files are not here yet).
; Never alox.ini, never a password.
Source: "{code:LabviewDir}\translations\*.lang"; DestDir: "{app}\translations"; Flags: external skipifsourcedoesntexist onlyifdoesntexist; Check: CopyFromLabview
Source: "{code:LabviewDir}\alox_default.ini"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist uninsneveruninstall; Check: CopyFromLabview

[Icons]
Name: "{group}\Alox Vision"; Filename: "{app}\alox-ui.exe"; WorkingDir: "{app}"; Comment: "ALOX Vision (Python) with the operator window, on the machine's hardware"
Name: "{group}\Alox Vision (simulation demo)"; Filename: "{app}\alox-ui.exe"; Parameters: "--demo"; WorkingDir: "{app}"; Comment: "Simulated machine and wafer, no hardware, no supervisor API"
Name: "{group}\Alox Vision server (console)"; Filename: "{app}\alox.exe"; WorkingDir: "{app}"; Comment: "ALOX Vision (Python) without window: devices, core and supervisor API, log in the console"
; The check tools: a Command Prompt in the installation folder showing the tool's help (the reports go to
; Documents\AloxVisionPy\reports, or --out). Run them with LabVIEW stopped.
Name: "{group}\Check tools\Command prompt (all tools)"; Filename: "{cmd}"; Parameters: "/k alox.exe --version"; WorkingDir: "{app}"
Name: "{group}\Check tools\Z stage check"; Filename: "{cmd}"; Parameters: "/k z_stage_check.exe --help"; WorkingDir: "{app}"
Name: "{group}\Check tools\XY stage check"; Filename: "{cmd}"; Parameters: "/k xy_stage_check.exe --help"; WorkingDir: "{app}"
Name: "{group}\Check tools\Monochromator check"; Filename: "{cmd}"; Parameters: "/k wl_check.exe --help"; WorkingDir: "{app}"
Name: "{group}\Check tools\Camera check"; Filename: "{cmd}"; Parameters: "/k camera_check.exe --help"; WorkingDir: "{app}"
Name: "{group}\Check tools\Thermal probe check"; Filename: "{cmd}"; Parameters: "/k thermal_check.exe --help"; WorkingDir: "{app}"
Name: "{group}\Check tools\Fast autofocus latency check"; Filename: "{cmd}"; Parameters: "/k af_latency_check.exe --help"; WorkingDir: "{app}"

[Run]
Filename: "{app}\alox-ui.exe"; Parameters: "--demo"; Description: "Start the simulation demo"; Flags: postinstall nowait skipifsilent unchecked runasoriginaluser

[Code]
var
  LabviewPage: TInputDirWizardPage;

function GuessLabviewDir: String;
begin
  Result := ExpandConstant('{commonpf64}\alox');
  if not DirExists(Result) then
    Result := '';
end;

procedure InitializeWizard;
begin
  LabviewPage := CreateInputDirPage(wpSelectDir,
    'LabVIEW installation (read only)',
    'Copy the translations and alox_default.ini of the LabVIEW application?',
    'Setup can copy translations\*.lang and alox_default.ini from the folder of the LabVIEW Vision Core application. ' +
    'That folder is only read: nothing of the LabVIEW installation is changed. Files already present in ' +
    'C:\Program Files\Aloxtec\AloxVisionPy are kept. Passwords and alox.ini are never copied.' + #13#10#13#10 +
    'Leave the field empty to copy nothing (copy the files by hand later, see README-installation.md).',
    False, '');
  LabviewPage.Add('LabVIEW application folder (the folder of its .exe, with translations\ and alox_default.ini):');
  LabviewPage.Values[0] := GetPreviousData('LabviewDir', GuessLabviewDir);
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'LabviewDir', LabviewPage.Values[0]);
end;

function LabviewDir(Param: String): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(LabviewPage.Values[0]));
end;

function CopyFromLabview: Boolean;
begin
  Result := (LabviewDir('') <> '') and DirExists(LabviewDir(''));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir, App: String;
begin
  Result := True;
  if CurPageID = LabviewPage.ID then
  begin
    Dir := LabviewDir('');
    App := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
    if Dir = '' then
      Exit;
    if not DirExists(Dir) then
    begin
      MsgBox('The folder "' + Dir + '" does not exist. Correct it, or leave the field empty.', mbError, MB_OK);
      Result := False;
    end
    else if (CompareText(Dir, App) = 0) or (Pos(Uppercase(AddBackslash(App)), Uppercase(AddBackslash(Dir))) = 1) then
    begin
      MsgBox('This is the folder of the Python application itself. Give the LabVIEW application folder, or leave ' +
        'the field empty.', mbError, MB_OK);
      Result := False;
    end
    else if Pos(Uppercase(AddBackslash(Dir)), Uppercase(AddBackslash(App))) = 1 then
    begin
      MsgBox('The Python application would be installed inside the LabVIEW folder "' + Dir + '". Install it in ' +
        'its own folder (the default C:\Program Files\Aloxtec\AloxVisionPy).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;
