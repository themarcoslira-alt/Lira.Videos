import subprocess

cmd = 'Get-CimInstance Win32_Process -Filter "Name=\'chrome.exe\'" | Select-Object ProcessId, CommandLine'
out = subprocess.check_output(['powershell', '-NoProfile', '-Command', cmd]).decode('cp1252', errors='ignore')
lines = [l.strip() for l in out.splitlines() if l.strip()]
print(f"Total Chrome lines: {len(lines)}")
for l in lines:
    print(l)
