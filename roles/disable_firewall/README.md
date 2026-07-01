# Disable_Firewall Role

## Description
Disables Windows Firewall across all network profiles (Domain, Private, and Public) to support cyber range activities and remove network filtering that could interfere with attack simulations or training scenarios.

## Variable Definition Location
This role requires no variables - it applies a standard configuration to disable Windows Firewall.

## Required Variables
None - this role uses only predefined firewall settings.

## Firewall Profiles Disabled

The role disables the following Windows Firewall profiles:

| Profile | Description |
|---------|-------------|
| Domain | Applied when connected to a domain network |
| Private | Applied when connected to a private/home network |
| Public | Applied when connected to a public network |
