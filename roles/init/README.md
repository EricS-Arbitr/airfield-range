# Init Role

## Description
Ensures Windows systems are online and reachable before proceeding with configuration tasks. The role waits for WinRM connectivity to be established and verifies the connection with a ping test, providing a reliable starting point for subsequent automation tasks.

## Variable Definition Location
This role requires no variables - it uses built-in Ansible connection parameters.

## Required Variables
None - this role uses only the standard Ansible connection parameters already configured for Windows hosts.
