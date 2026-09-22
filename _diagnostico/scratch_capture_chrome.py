import subprocess

cmd = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    '--remote-debugging-port=9222',
    '--remote-allow-origins=*',
    r'--user-data-dir=C:\Users\Administrator\lira_profiles\conta_04',
    '--no-first-run',
    '--no-default-browser-check',
    '--enable-logging',
    '--v=1'
]

p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    stdout, stderr = p.communicate(timeout=5)
    print("Chrome exited with code:", p.returncode)
    print("STDOUT:", stdout.decode('cp1252', errors='ignore'))
    print("STDERR:", stderr.decode('cp1252', errors='ignore'))
except subprocess.TimeoutExpired:
    print("Chrome is still running! PID:", p.pid)
    p.kill()
