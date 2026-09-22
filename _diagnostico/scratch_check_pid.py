import subprocess

cmd = 'Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq 11192 } | Select-Object -ExpandProperty CommandLine'
out = subprocess.check_output(['powershell', '-NoProfile', '-Command', cmd])
print("PID 11192:", out.decode('utf-8', errors='ignore'))
