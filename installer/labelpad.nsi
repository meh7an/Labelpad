; installer/labelpad.nsi
; NSIS installer script for Labelpad — per-user install (no elevation).
; DIST_PATH is passed in at build time via /DDIST_PATH="<absolute path>"
; VERSION   is passed in at build time via /DVERSION="v1.2.0" (the git tag)
; Example: makensis /DDIST_PATH="C:\...\dist\Labelpad" /DVERSION="v1.2.0" installer\labelpad.nsi

!define APP_NAME    "Labelpad"
!ifdef VERSION
  !searchreplace APP_VERSION "${VERSION}" "v" ""
!else
  !define APP_VERSION "0.0.0"
!endif
!define APP_EXE     "Labelpad.exe"
!define PUBLISHER   "Labelpad"
!define OUTPUT_FILE "Labelpad_v${APP_VERSION}_setup.exe"
!define REG_KEY     "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

; ProgId used for the .dcmpack file association.
; Kept separate from APP_NAME so renaming the app never breaks existing
; shell associations on already-installed machines.
!define PROGID "Labelpad.dcmpack"

Name            "${APP_NAME}"
OutFile         "..\installer_output\${OUTPUT_FILE}"

; Per-user install: user-writable location so the in-app updater can swap
; files without elevation. Registry lives in HKCU for the same reason.
InstallDir      "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor   lzma

!define MUI_ICON "..\assets\icon.ico"
!define MUI_UNICON "..\assets\icon.ico"

!include "MUI2.nsh"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Labelpad"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; -----------------------------------------------------------------------
; Legacy migration
;
; Versions up to v1.2.0 installed machine-wide into Program Files (HKLM,
; admin). Those installs cannot be self-updated, so offer a one-time
; elevated removal before the per-user install proceeds. Declining keeps
; both installs side by side.
; -----------------------------------------------------------------------

Function .onInit
  ReadRegStr $R0 HKLM "${REG_KEY}" "UninstallString"
  ReadRegStr $R1 HKLM "Software\${APP_NAME}" "InstallDir"
  StrCmp $R0 "" done
  StrCmp $R1 "" done
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "A previous system-wide installation of ${APP_NAME} was found.$\r$\n$\r$\nRemove it before installing the new per-user version? Windows will ask for administrator approval." \
    IDNO done
  ; Run the old (admin) uninstaller silently and in place so we can wait on it.
  ExecShellWait "" "$R1\Uninstall.exe" "/S _?=$R1"
  ; In-place mode leaves Uninstall.exe and the directory behind — clean up elevated.
  ExecShellWait "runas" "$SYSDIR\cmd.exe" '/c rd /s /q "$R1"' SW_HIDE
done:
FunctionEnd

; -----------------------------------------------------------------------
; Install
; -----------------------------------------------------------------------

Section "Labelpad"

  SetOutPath "$INSTDIR"
  File /r "${DIST_PATH}\*.*"

  ; Registry: install location
  WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"

  ; Registry: Add/Remove Programs (per-user)
  WriteRegStr   HKCU "${REG_KEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr   HKCU "${REG_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr   HKCU "${REG_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKCU "${REG_KEY}" "Publisher"       "${PUBLISHER}"
  WriteRegStr   HKCU "${REG_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${REG_KEY}" "NoModify"        1
  WriteRegDWORD HKCU "${REG_KEY}" "NoRepair"        1

  ; Shortcuts (current-user Start Menu and Desktop — the default context
  ; under RequestExecutionLevel user)
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
    "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" \
    "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" \
    "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0

  ; -----------------------------------------------------------------------
  ; File association: .dcmpack
  ;
  ; Per-user associations live under HKCU\Software\Classes — no admin
  ; rights needed. The ProgId key (Labelpad.dcmpack) is the indirection
  ; layer between the extension and the actual shell commands, which lets
  ; us update the executable path on upgrade without touching the
  ; .dcmpack key.
  ; -----------------------------------------------------------------------

  ; Map extension to our ProgId
  WriteRegStr HKCU "Software\Classes\.dcmpack"              ""             "${PROGID}"
  WriteRegStr HKCU "Software\Classes\.dcmpack"              "Content Type" "application/x-dcmpack"

  ; ProgId: human-readable description + icon
  WriteRegStr HKCU "Software\Classes\${PROGID}"             ""             "Labelpad DICOM Pack"
  WriteRegStr HKCU "Software\Classes\${PROGID}\DefaultIcon" ""             "$INSTDIR\${APP_EXE},0"

  ; Shell verb: open
  WriteRegStr HKCU "Software\Classes\${PROGID}\shell"               ""  "open"
  WriteRegStr HKCU "Software\Classes\${PROGID}\shell\open"          ""  "Open with Labelpad"
  WriteRegStr HKCU "Software\Classes\${PROGID}\shell\open\command"  ""  '"$INSTDIR\${APP_EXE}" "%1"'

  ; Notify Explorer of the change so icons and associations refresh
  ; immediately — no reboot required.
  System::Call 'Shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

  WriteUninstaller "$INSTDIR\Uninstall.exe"

SectionEnd

; -----------------------------------------------------------------------
; Uninstall
; -----------------------------------------------------------------------

Section "Uninstall"

  RMDir /r "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKCU "${REG_KEY}"
  DeleteRegKey HKCU "Software\${APP_NAME}"

  ; File association cleanup — remove both the extension pointer and
  ; the full ProgId subtree.  If another app has since claimed .dcmpack,
  ; only remove it if it still points to our ProgId.
  ReadRegStr $0 HKCU "Software\Classes\.dcmpack" ""
  StrCmp $0 "${PROGID}" 0 +2
    DeleteRegKey HKCU "Software\Classes\.dcmpack"
  DeleteRegKey HKCU "Software\Classes\${PROGID}"

  ; Refresh Explorer after cleanup
  System::Call 'Shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

SectionEnd
