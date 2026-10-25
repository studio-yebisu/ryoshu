; Compile only with scripts/build_windows_package.py and a verified source ZIP.
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif

[Setup]
AppId={{665552B0-0919-4E95-A791-2C0EDFD7EB1E}
AppName=Ryoshu
AppVersion={#AppVersion}
AppPublisher=Studio Yebisu
AppPublisherURL=https://github.com/studio-yebisu/ryoshu
AppSupportURL=https://github.com/studio-yebisu/ryoshu/issues
DefaultDirName={localappdata}\Programs\StudioYebisu\Ryoshu
DefaultGroupName=Ryoshu
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
DisableProgramGroupPage=yes
DisableWelcomePage=no
WizardStyle=modern
LicenseFile={#SourceDir}\LICENSE
InfoBeforeFile={#SourceDir}\packaging\windows\before-install.txt
OutputDir={#OutputDir}
OutputBaseFilename=Ryoshu-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayName=Ryoshu
UninstallDisplayIcon={sys}\shell32.dll,1
VersionInfoDescription=Ryoshu Windows installer
VersionInfoCompany=Studio Yebisu
VersionInfoVersion={#AppVersion}

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにRyoshuのショートカットを作る"; GroupDescription: "ショートカット"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\packaging\windows\can-modify.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\Ryoshu"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\launch.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 1
Name: "{group}\Ryoshuを終了"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\stop.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 27
Name: "{group}\Ryoshuの導入ガイド"; Filename: "{app}\packaging\windows\welcome.html"; WorkingDir: "{app}"
Name: "{autodesktop}\Ryoshu"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\launch.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 1; Tasks: desktopicon

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\launch.ps1"""; WorkingDir: "{app}"; Description: "Ryoshuを開く（初回は準備が必要です）"; Flags: postinstall nowait skipifsilent

[Code]
function LedgerRoot: String;
begin
  Result := AddBackslash(GetEnv('USERPROFILE')) + 'StudioYebisu\ReceiptLedger';
end;

function UnsafeDestination: Boolean;
var
  Destination, Ledger: String;
begin
  Destination := AddBackslash(Lowercase(ExpandFileName(WizardDirValue)));
  Ledger := AddBackslash(Lowercase(ExpandFileName(LedgerRoot)));
  { Never mix managed application files with existing ledger/runtime data. }
  Result := (Pos(Ledger, Destination) = 1) or (Pos(Destination, Ledger) = 1);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and UnsafeDestination then begin
    MsgBox('帳簿の保存先とは別のフォルダーを選んでください。既定のインストール先をおすすめします。', mbError, MB_OK);
    Result := False;
  end;
end;

function CanModify(Script: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -File ' + AddQuotes(Script) + ' -AppRoot ' + AddQuotes(ExpandConstant('{app}')),
    '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if UnsafeDestination then
    Result := '帳簿の保存先とは別のインストール先を選んでください。'
  else if FileExists(ExpandConstant('{app}\start.ps1')) then begin
    ExtractTemporaryFile('can-modify.ps1');
    if not CanModify(ExpandConstant('{tmp}\can-modify.ps1')) then
      Result := 'このフォルダーのRyoshuが起動中、または起動元を確認できません。読み取りの終了を待ち、元のアプリの「Ryoshuを終了」または「停止.cmd」で終了してから、もう一度実行してください。帳簿は変更していません。';
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := CanModify(ExpandConstant('{app}\packaging\windows\can-modify.ps1'));
  if not Result then
    MsgBox('Ryoshuを終了してからアンインストールしてください。帳簿と原本は削除しません。', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('アプリを削除しました。帳簿・原本・復元用の情報とAIモデルは残しています。', mbInformation, MB_OK);
end;
