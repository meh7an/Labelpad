; installer/labelpad.nsi
; NSIS installer script for Labelpad
; DIST_PATH is passed in at build time via /DDIST_PATH="<absolute path>"
; Example: makensis /DDIST_PATH="C:\...\dist\Labelpad" installer\labelpad.nsi

!define APP_NAME    "Labelpad"
!define APP_VERSION "1.0.0"
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
InstallDir      "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel admin
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
; Install
; -----------------------------------------------------------------------

Section "Labelpad"

  SetOutPath "$INSTDIR"
  File /r "${DIST_PATH}\*.*"

  ; Registry: install location
  WriteRegStr HKLM "Software\${APP_NAME}" "InstallDir" "$INSTDIR"

  ; Registry: Add/Remove Programs
  WriteRegStr   HKLM "${REG_KEY}" "DisplayName"     "${APP_NAME}"
  WriteRegStr   HKLM "${REG_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr   HKLM "${REG_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKLM "${REG_KEY}" "Publisher"       "${PUBLISHER}"
  WriteRegStr   HKLM "${REG_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKLM "${REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKLM "${REG_KEY}" "NoModify"        1
  WriteRegDWORD HKLM "${REG_KEY}" "NoRepair"        1

  ; Shortcuts
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
  ; Writing to HKCR requires admin rights (already requested above).
  ; The ProgId key (Labelpad.dcmpack) is the indirection layer between
  ; the extension and the actual shell commands, which lets us update
  ; the executable path on upgrade without touching the .dcmpack key.
  ; -----------------------------------------------------------------------

  ; Map extension to our ProgId
  WriteRegStr HKCR ".dcmpack"              ""            "${PROGID}"
  WriteRegStr HKCR ".dcmpack"              "Content Type" "application/x-dcmpack"

  ; ProgId: human-readable description + icon
  WriteRegStr HKCR "${PROGID}"             ""            "Labelpad DICOM Pack"
  WriteRegStr HKCR "${PROGID}\DefaultIcon" ""            "$INSTDIR\${APP_EXE},0"

  ; Shell verb: open
  WriteRegStr HKCR "${PROGID}\shell"               ""  "open"
  WriteRegStr HKCR "${PROGID}\shell\open"          ""  "Open with Labelpad"
  WriteRegStr HKCR "${PROGID}\shell\open\command"  ""  '"$INSTDIR\${APP_EXE}" "%1"'

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

  DeleteRegKey HKLM "${REG_KEY}"
  DeleteRegKey HKLM "Software\${APP_NAME}"

  ; File association cleanup — remove both the extension pointer and
  ; the full ProgId subtree.  If another app has since claimed .dcmpack,
  ; only remove it if it still points to our ProgId.
  ReadRegStr $0 HKCR ".dcmpack" ""
  StrCmp $0 "${PROGID}" 0 +2
    DeleteRegKey HKCR ".dcmpack"
  DeleteRegKey HKCR "${PROGID}"

  ; Refresh Explorer after cleanup
  System::Call 'Shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

SectionEnd