; ssh-client-manager.nsi — NSIS installer script for SSH Client Manager
;
; Prerequisites:
;   1. Build the PyInstaller bundle first:  ./packaging/windows/build-exe.sh
;   2. Install NSIS:  https://nsis.sourceforge.io/
;   3. Run:  makensis packaging/windows/ssh-client-manager.nsi
;
; Output: dist/SSHClientManager-Setup.exe

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ── App metadata ──────────────────────────────────────────────────────────────
!define APPNAME      "SSH Client Manager"
!define APPID        "ssh-client-manager"
!define APPEXE       "SSHClientManager.exe"
!define APPVERSION   "1.0.0"
!define PUBLISHER    "SSH Client Manager Contributors"
!define HOMEPAGE     "https://github.com/ssh-client-manager"
!define UNINSTKEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"

Name "${APPNAME} ${APPVERSION}"
OutFile "dist\SSHClientManager-${APPVERSION}-Setup.exe"
InstallDir "$LOCALAPPDATA\${APPID}"
InstallDirRegKey HKCU "${UNINSTKEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma

; ── MUI settings ──────────────────────────────────────────────────────────────
!define MUI_ABORTWARNING
!define MUI_ICON "packaging\windows\ssh-client-manager.ico"
!define MUI_UNICON "packaging\windows\ssh-client-manager.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APPEXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APPNAME}"

; ── Pages ─────────────────────────────────────────────────────────────────────
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ── Install section ───────────────────────────────────────────────────────────
Section "Install"
    SetOutPath "$INSTDIR"

    ; Copy all files from the PyInstaller dist directory
    File /r "dist\SSHClientManager\*.*"

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Start Menu shortcuts
    CreateDirectory "$SMPROGRAMS\${APPNAME}"
    CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" \
        "$INSTDIR\${APPEXE}" "" "$INSTDIR\${APPEXE}" 0
    CreateShortCut "$SMPROGRAMS\${APPNAME}\Uninstall.lnk" \
        "$INSTDIR\Uninstall.exe"

    ; Desktop shortcut
    CreateShortCut "$DESKTOP\${APPNAME}.lnk" \
        "$INSTDIR\${APPEXE}" "" "$INSTDIR\${APPEXE}" 0

    ; Registry entries for Add/Remove Programs
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayName"     "${APPNAME}"
    WriteRegStr HKCU "${UNINSTKEY}" "UninstallString"  "$\"$INSTDIR\Uninstall.exe$\""
    WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" "$\"$INSTDIR\Uninstall.exe$\" /S"
    WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation"  "$INSTDIR"
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon"      "$INSTDIR\${APPEXE}"
    WriteRegStr HKCU "${UNINSTKEY}" "Publisher"         "${PUBLISHER}"
    WriteRegStr HKCU "${UNINSTKEY}" "URLInfoAbout"      "${HOMEPAGE}"
    WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion"    "${APPVERSION}"
    WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1

    ; Estimate installed size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

; ── Uninstall section ─────────────────────────────────────────────────────────
Section "Uninstall"
    ; Remove Start Menu shortcuts
    RMDir /r "$SMPROGRAMS\${APPNAME}"

    ; Remove Desktop shortcut
    Delete "$DESKTOP\${APPNAME}.lnk"

    ; Remove registry entries
    DeleteRegKey HKCU "${UNINSTKEY}"

    ; Remove install directory
    RMDir /r "$INSTDIR"
SectionEnd
