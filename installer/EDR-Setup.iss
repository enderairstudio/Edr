; Default installer: EDR app + launcher only (uses Python already on the PC).
; Smallest/cleanest VirusTotal profile. Use EDR-Setup-Full.iss for offline Python.

#define MyAppName "EDR Project Sharer"
#define MyAppVersion "0.5.5"
#define MyAppPublisher "Ender Air Studio"
#define MyAppURL "https://github.com/enderairstudio/Edr"

[Setup]
AppId={{A7B3E4F2-9C1D-4E8A-B5F6-0123456789AB}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\EDR
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; Output: dist\EDR-Setup\EDR-Setup.exe (copied to dist\EDR-Setup.exe by build.ps1)
OutputDir=..\dist\EDR-Setup
OutputBaseFilename=EDR-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\edr.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion=0.5.5.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) {#MyAppPublisher}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addpath"; Description: "Add EDR to my user PATH (new terminals can run edr)"; GroupDescription: "Options:"; Flags: checkedonce

[Files]
Source: "..\dist\edr\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EDR Project Sharer"; Filename: "{app}\edr.exe"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function GetTickCount: DWORD;
  external 'GetTickCount@kernel32 stdcall';

var
  InstallPhaseStarted: DWORD;
  LastInstallStatusStage: Integer;

const
  INSTALL_MIN_MS = 6000;
  INSTALL_TICK_MS = 40;

procedure SetInstallStatusStage(Stage: Integer);
var
  Msg: string;
begin
  if Stage = LastInstallStatusStage then
    Exit;
  LastInstallStatusStage := Stage;
  case Stage of
    0: Msg := 'Preparing installation…';
    1: Msg := 'Removing previous EDR version…';
    2: Msg := 'Copying application files…';
    3: Msg := 'Installing EDR CLI (handler, share, relay)…';
    4: Msg := 'Installing EDR Guard…';
    5: Msg := 'Copying launcher (edr.exe)…';
    6: Msg := 'Copying scripts (edr.cmd, edr.ps1)…';
    7: Msg := 'Writing uninstaller…';
    8: Msg := 'Registering EDR with Windows…';
    9: Msg := 'Updating PATH…';
    10: Msg := 'Finishing installation…';
  else
    Msg := 'Installing EDR Project Sharer…';
  end;
  WizardForm.StatusLabel.Caption := Msg;
end;

procedure UpdateInstallStatusFromPercent(Percent: Integer);
begin
  if Percent < 8 then
    SetInstallStatusStage(0)
  else if Percent < 18 then
    SetInstallStatusStage(2)
  else if Percent < 38 then
    SetInstallStatusStage(3)
  else if Percent < 52 then
    SetInstallStatusStage(4)
  else if Percent < 68 then
    SetInstallStatusStage(5)
  else if Percent < 82 then
    SetInstallStatusStage(6)
  else if Percent < 94 then
    SetInstallStatusStage(7)
  else
    SetInstallStatusStage(10);
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Percent: Integer;
begin
  if MaxProgress > 0 then
  begin
    Percent := (CurProgress * 100) div MaxProgress;
    UpdateInstallStatusFromPercent(Percent);
    Sleep(INSTALL_TICK_MS);
  end;
end;

function IsEdrPath(const S: string): Boolean;
var
  U: string;
begin
  U := UpperCase(S);
  Result := (Pos('\EDR\', U) > 0) or (Pos('\EDR-SETUP\', U) > 0) or
    (Copy(U, Length(U) - 3, 4) = '\EDR') or (Pos('\EDR.EXE', U) > 0) or
    (Pos('\NODE_MODULES\@ENDERAIR\EDR', U) > 0);
end;

procedure RemoveNpmLegacyEdr();
var
  Code: Integer;
begin
  if Exec(ExpandConstant('{cmd}'), '/c npm uninstall -g @enderair/edr --loglevel=error 2>nul', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    ;
end;

procedure RemovePathEntry(const Entry: string);
var
  OrigPath, Part, NewPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
    Exit;
  NewPath := '';
  while OrigPath <> '' do
  begin
    P := Pos(';', OrigPath);
    if P > 0 then
    begin
      Part := Copy(OrigPath, 1, P - 1);
      Delete(OrigPath, 1, P);
    end
    else
    begin
      Part := OrigPath;
      OrigPath := '';
    end;
    if (Part <> '') and (CompareText(Part, Entry) <> 0) and (not IsEdrPath(Part)) then
    begin
      if NewPath <> '' then NewPath := NewPath + ';';
      NewPath := NewPath + Part;
    end;
  end;
  RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
end;

function GetUninstallString(const RegKey: string): string;
begin
  Result := '';
  if RegQueryStringValue(HKEY_CURRENT_USER, RegKey, 'UninstallString', Result) then
    Exit;
  RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey, 'UninstallString', Result);
end;

function SilentUninstall(const UninstallString: string): Boolean;
var
  Cmd, Params, Line: string;
  P: Integer;
  ErrorCode: Integer;
begin
  Result := False;
  if UninstallString = '' then Exit;
  Line := RemoveQuotes(UninstallString);
  P := Pos(' ', Line);
  if P > 0 then
  begin
    Cmd := Copy(Line, 1, P - 1);
    Params := Copy(Line, P + 1, Length(Line) - P);
  end
  else
  begin
    Cmd := Line;
    Params := '';
  end;
  if Params <> '' then Params := Params + ' ';
  Params := Params + '/SILENT /NORESTART /SUPPRESSMSGBOXES';
  if Exec(Cmd, Params, '', SW_HIDE, ewWaitUntilTerminated, ErrorCode) then
    Result := True;
end;

function GetPriorInstallDir(): string;
var
  RegKey: string;
begin
  Result := '';
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{A7B3E4F2-9C1D-4E8A-B5F6-0123456789AB}_is1';
  if RegQueryStringValue(HKEY_CURRENT_USER, RegKey, 'InstallLocation', Result) then
    Exit;
  RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey, 'InstallLocation', Result);
end;

procedure RemoveLegacyEdrFolders();
var
  Paths: array[0..4] of string;
  I: Integer;
begin
  Paths[0] := ExpandConstant('{localappdata}\EDR');
  Paths[1] := ExpandConstant('{localappdata}\EDR\EDR-Setup');
  Paths[2] := ExpandConstant('{userappdata}\EDR');
  Paths[3] := GetPriorInstallDir();
  Paths[4] := '';
  for I := 0 to 3 do
  begin
    if (Paths[I] <> '') and DirExists(Paths[I]) then
      DelTree(Paths[I], True, True, True);
  end;
end;

function PythonAvailable(): Boolean;
var
  Code: Integer;
begin
  if Exec(ExpandConstant('{cmd}'), '/c py -3 -c "import sys"', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
  begin
    Result := True;
    Exit;
  end;
  if Exec(ExpandConstant('{cmd}'), '/c python -c "import sys"', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
  begin
    Result := True;
    Exit;
  end;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  UninstallString: string;
  RegKey: string;
begin
  RemoveNpmLegacyEdr();
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{A7B3E4F2-9C1D-4E8A-B5F6-0123456789AB}_is1';
  UninstallString := GetUninstallString(RegKey);
  if UninstallString <> '' then
    SilentUninstall(UninstallString);
  RemoveLegacyEdrFolders();
  RemovePathEntry('');
  Result := '';
end;

function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not WizardIsTaskSelected('addpath') then
  begin
    Result := False;
    Exit;
  end;
  if not RegQueryStringValue(HKEY_CURRENT_USER,
    'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    Exit;
  end;
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    if not PythonAvailable() then
    begin
      if MsgBox(
        'Python 3 was not detected on this PC.' + #13#10 + #13#10 +
        'EDR needs Python 3.11+ (https://www.python.org/downloads/).' + #13#10 +
        'You can also run: winget install Python.Python.3.11' + #13#10 + #13#10 +
        'Continue installing EDR anyway?',
        mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  OrigPath, NewPath, AppDir, Part: string;
  P: Integer;
  Elapsed: DWORD;
begin
  if CurStep = ssInstall then
  begin
    InstallPhaseStarted := GetTickCount();
    LastInstallStatusStage := -1;
    SetInstallStatusStage(1);
  end;

  if CurStep = ssPostInstall then
  begin
    Elapsed := GetTickCount() - InstallPhaseStarted;
    while (InstallPhaseStarted <> 0) and (Elapsed < INSTALL_MIN_MS) do
    begin
      if Elapsed < (INSTALL_MIN_MS div 3) then
        SetInstallStatusStage(8)
      else if Elapsed < ((INSTALL_MIN_MS * 2) div 3) then
        SetInstallStatusStage(9)
      else
        SetInstallStatusStage(10);
      Sleep(100);
      Elapsed := GetTickCount() - InstallPhaseStarted;
    end;
    AppDir := ExpandConstant('{app}');
    RemovePathEntry('');
    if WizardIsTaskSelected('addpath') then
    begin
      OrigPath := '';
      RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath);
      NewPath := AppDir;
      while OrigPath <> '' do
      begin
        P := Pos(';', OrigPath);
        if P > 0 then
        begin
          Part := Copy(OrigPath, 1, P - 1);
          Delete(OrigPath, 1, P);
        end
        else
        begin
          Part := OrigPath;
          OrigPath := '';
        end;
        if (Part <> '') and (CompareText(Part, AppDir) <> 0) then
        begin
          NewPath := NewPath + ';' + Part;
        end;
      end;
      RegWriteStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', NewPath);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpInstalling then
  begin
    LastInstallStatusStage := -1;
    SetInstallStatusStage(0);
  end;
  if CurPageID = wpReady then
  begin
    WizardForm.StatusLabel.Caption :=
      'Any previous EDR install will be removed, then the new version will be installed.';
  end;
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption :=
      'EDR was installed (old npm @enderair/edr removed if present).' + #13#10 + #13#10 +
      'Close ALL open terminals, open a new one, then run:' + #13#10 + #13#10 +
      '  edr version' + #13#10 +
      '  edr doctor' + #13#10 + #13#10 +
      'If you still see old help, run: npm uninstall -g @enderair/edr';
  end;
end;
