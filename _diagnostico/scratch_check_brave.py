import subprocess

cmd = 'Get-CimInstance Win32_Process -Filter "Name=\'brave.exe\'" | Select-Object ProcessId, CommandLine'
out = subprocess.check_output(['powershell', '-NoProfile', '-Command', cmd]).decode('cp1252', errors='ignore')
print(out)
