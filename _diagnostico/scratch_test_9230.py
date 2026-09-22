import subprocess, urllib.request, time

cmd = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    '--remote-debugging-port=9230',
    '--remote-allow-origins=*',
    r'--user-data-dir=C:\Users\Administrator\lira_profiles\conta_04',
    '--no-first-run',
    '--no-default-browser-check'
]

p = subprocess.Popen(cmd)
time.sleep(2)
try:
    with urllib.request.urlopen("http://127.0.0.1:9230/json/version", timeout=2) as r:
        print("Status on 9230:", r.status)
        print("Response:", r.read().decode())
finally:
    p.kill()
