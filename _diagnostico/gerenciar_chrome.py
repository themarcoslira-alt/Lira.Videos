import subprocess
import json
import sys

cmd = ['wmic', 'process', 'where', 'ProcessId=12100', 'get', 'CommandLine,ProcessId']
try:
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("WMIC Output:\n", res.stdout)
except Exception as e:
    print("Error:", e)
