; vvlog Windows インストーラー(Inno Setup 6)。ユーザー単位(管理者権限なし)で %LOCALAPPDATA%\Programs\vvlog へ入れる。
; 中身は scripts/build_dist.py が dist-win\vvlog に並べたもの。コード署名はしない(ADR-0010): 初回は SmartScreen の警告が出る。
; ビルド: iscc /DAppVersion=0.1.1 installer\vvlog.iss  → dist-win\vvlog-0.1.1-windows-setup.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1E5E34-5B0B-4E8D-9B7A-7A0C5B9B2F11}
AppName=vvlog
AppVersion={#AppVersion}
AppPublisher=ng_2620
DefaultDirName={localappdata}\Programs\vvlog
DefaultGroupName=vvlog
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-win
OutputBaseFilename=vvlog-{#AppVersion}-windows-setup
SetupIconFile=..\dist-win\vvlog\vvlog.ico
UninstallDisplayIcon={app}\vvlog.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作る"; Flags: unchecked

[Files]
Source: "..\dist-win\vvlog\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\vvlog"; Filename: "{app}\vvlog.cmd"; IconFilename: "{app}\vvlog.ico"; WorkingDir: "{app}"
Name: "{autodesktop}\vvlog"; Filename: "{app}\vvlog.cmd"; IconFilename: "{app}\vvlog.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\vvlog.cmd"; Description: "vvlog を起動する"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; 実行中に作られた Python キャッシュだけ消す。ユーザーデータ(%LOCALAPPDATA%\vvlog の重み・環境・プロジェクト)は下で聞く
Type: filesandordirs; Name: "{app}\server\__pycache__"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\vvlog');
    if DirExists(Data) and (not UninstallSilent) then
      if MsgBox('解析用の環境・重み(数 GB)・設定が残っています:' + #13#10 + Data + #13#10#13#10 +
                '一緒に削除しますか?(プロジェクトの動画/書き出しは消えません)', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
