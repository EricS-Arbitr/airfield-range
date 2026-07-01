# Disable_UAC Role

## Description
Disables User Account Control (UAC) on Windows systems by modifying registry settings to remove elevation prompts and consent dialogs. This role supports cyber range scenarios where UAC would interfere with automated attack simulations or administrative tasks.

## Variable Definition Location
This role requires no variables - it applies standard registry modifications to disable UAC.

## Required Variables
None - this role uses only predefined registry settings.

## Registry Modifications

The role modifies the following registry keys at:
`HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`

| Registry Key | Value | Type | Description |
|--------------|-------|------|-------------|
| EnableLUA | 0 | DWORD | Disables User Account Control entirely |
| ConsentPromptBehaviorAdmin | 0 | DWORD | Elevates without prompting for administrators |

## Implementation Details

1. **Disable UAC** - Sets EnableLUA to 0, completely disabling UAC functionality
2. **Disable Admin Consent** - Sets ConsentPromptBehaviorAdmin to 0, allowing automatic elevation
