# UE Role

## Description
Configures User Experience (UE) settings on Windows workstations to optimize them for cyber range operations. The role disables lock screens, screen savers, inactivity timeouts, and other security features that would interrupt automated scenarios or training exercises.

## Variable Definition Location
This role requires no variables - it applies a standard set of user experience optimizations.

## Required Variables
None - this role uses only predefined registry settings.

## Registry Modifications

The role creates and modifies the following registry settings:

### Registry Hives Created
- `HKU:\.Default\Software\Microsoft\Windows\CurrentVersion\Policies\System`
- `HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\System`
- `HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization`
- `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`

### Settings Applied

| Registry Path | Setting | Value | Purpose |
|---------------|---------|-------|---------|
| HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization | NoLockScreen | 1 | Disables lock screen |
| HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon | ForceUnlockLogon | 1 | Forces unlock at logon |
| HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon | DisableLockWorkstation | 1 | Prevents workstation locking |
| HKCU:\Control Panel\Desktop | ScreenSaveActive | 0 | Disables screen saver |
| HKCU:\Control Panel\Desktop | ScreenSaveTimeOut | 0 | Sets screen saver timeout to 0 |
| HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System | InactivityTimeoutSecs | 0 | Disables inactivity timeout |
| HKU:\.Default\Control Panel\Desktop | ScreenSaveActive | 0 | Disables screen saver for default user |
| HKU:\.Default\Control Panel\Desktop | ScreenSaveTimeOut | 0 | Sets timeout to 0 for default user |

## Notes
- This role is designed for cyber range workstations where continuous operation is required
- All settings reduce security in favor of uninterrupted operation
- Typically applied to hosts in the "ue" inventory group
- Often used in conjunction with autologin role for fully automated workstations
- These settings should never be applied to production systems
- Changes ensure workstations remain accessible for training scenarios and automated testingRetryClaude can make mistakes. Please double-check responses.
