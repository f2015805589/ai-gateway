# Open firewall inbound rules for the AI gateway ports.
# Auto-elevates to Administrator (UAC prompt), then adds allow rules for
# TCP 3000 / 3001 / 8080 / 8888. Re-run is safe (rules are recreated).
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $args = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process powershell -Verb RunAs -ArgumentList $args
    exit
}
Write-Host 'Open firewall ports for AI Gateway ...'
foreach ($p in 3000, 3001, 8080, 8888) {
    & netsh advfirewall firewall delete rule name="AI-GW-$p" | Out-Null
    & netsh advfirewall firewall add rule name="AI-GW-$p" dir=in action=allow protocol=TCP localport=$p | Out-Null
    Write-Host "  TCP $p : allowed"
}
Write-Host 'Done. LAN devices can now reach http://<host-ip>:3000 etc.'
Read-Host 'Press Enter to exit'