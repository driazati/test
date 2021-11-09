# Taken from: https://docs.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse

Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# Start the sshd service
Start-Service sshd

# OPTIONAL but recommended:
Set-Service -Name sshd -StartupType 'Automatic'

# Confirm the firewall rule is configured. It should be created automatically by setup.
Get-NetFirewallRule -Name *ssh*

# There should be a firewall rule named "OpenSSH-Server-In-TCP", which should be enabled
# If the firewall does not exist, create one
New-NetFirewallRule `
  -Name sshd `
  -DisplayName 'OpenSSH Server (sshd)' `
  -Enabled True `
  -Direction Inbound `
  -Protocol TCP `
  -Action Allow `
  -LocalPort 22

# Get Choco
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

choco install -y jq awscli vswhere nodejs.install curl git 7zip visualstudio2019community
# choco install git --params "/GitAndUnixToolsOnPath" -y
choco install git.install -y --params "'/GitAndUnixToolsOnPath'"
choco install windows-sdk-10-version-2004-all --version=10.0.19041.0 -y

Get-Command curl
Get-Command aws
Get-Command 7z
Get-Command jq
Get-Command vswhere
Get-Command bash.exe
Test-Path "C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\gflags.exe"


curl.exe -LO https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe


New-Item C:\ProgramData\ssh\administrators_authorized_keys
# Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCW6ntdasjZtFECygi0M124wnYunypu40b7qTjnhl2QMsNDaqV/0frS7/pzAfqu2LmGyxXr9vbA7blLhJysm7zsOqs7C4/9pKbtymQ1QEh5zoOoJxALurc2E9eBNsXLVk/n5bTaIhxcQASAG6bRzFS+3qbHOe2+vTL2IMO5Gt176U+iFupJRoIiSIYUKEkVNJK+p4DdKRvMxwMU7TENcATj8H0diFOJ41F61avgVNjeWZ+lesNwQHnIGrTPFvWqxcgUN4Z8C2ULFS7Ra/ZXdCYWwnwxsvOj5LpuVUqSrRMSTW0oM6PFutkI4MfHYpGuwOT0IOh6pUWkTcL/tRQXhALWHLaat70BypRrLKXKyOj8LdufUfZ4zoHxsWoD080FVNtFepdiyy0KEDbrObPwMamnTMjp1Fy96JnrxTiqJ8tM/3lovYHHelFSPQAyVpz1rIl6OAlPHaJjnbs5sge4SsQR3fzf/+jFITF33HibcybldhQLff3cRfe8oaEADR/0wMk= davidriazati@davidriazati-mbp'
$acl = Get-Acl C:\ProgramData\ssh\administrators_authorized_keys
$acl.SetAccessRuleProtection($true, $false)
$administratorsRule = New-Object system.security.accesscontrol.filesystemaccessrule("Administrators","FullControl","Allow")
$systemRule = New-Object system.security.accesscontrol.filesystemaccessrule("SYSTEM","FullControl","Allow")
$acl.SetAccessRule($administratorsRule)
$acl.SetAccessRule($systemRule)
$acl | Set-Acl


# <powershell>
# Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value '<PUBKEY>'
# </powershell>