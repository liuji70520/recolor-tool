; RecolorTool 安装包脚本（Inno Setup 6）
; 构建顺序：先 .\build_exe.ps1 产出 dist\RecolorTool，再 .\build_installer.ps1

#define AppName "一键换色 RecolorTool"
#define AppVersion "2.1.0"
#define AppPublisher "liuji70520"
#define AppExeName "RecolorTool.exe"
#define DistDir "dist\RecolorTool"

[Setup]
AppId={{B7E21D3C-9A3E-4F5D-8C1A-5E6F7A8B9C0D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/liuji70520/recolor-tool
AppSupportURL=https://github.com/liuji70520/recolor-tool/issues
DefaultDirName={localappdata}\Programs\RecolorTool
DefaultGroupName={#AppName}
; 按用户安装：不弹 UAC、不需要管理员
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist-installer
OutputBaseFilename=RecolorTool-Setup-v{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Code]
// 检测 WebView2 运行时（独立窗口依赖它；Windows 11 自带，大部分 Win10 也有）
function WebView2Installed: Boolean;
var
  V: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) or
    RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V);
end;

function InitializeSetup: Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if not WebView2Installed then
  begin
    if MsgBox('未检测到 WebView2 运行时（程序窗口依赖它）。' + #13#10 +
              'Windows 11 一般已自带。点“是”打开微软官网下载安装，装完再回来继续。',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/',
                '', '', SW_SHOW, ewNoWait, ErrorCode);
    end;
  end;
end;
