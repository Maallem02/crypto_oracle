"""Is the backend (python on port 8000) inside a Windows job object?"""
import ctypes
import os
import re
import subprocess
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
k32.OpenProcess.restype = wintypes.HANDLE
k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]

sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
raw = subprocess.run([os.path.join(sys32, "netstat.exe"), "-ano", "-p", "TCP"],
                     capture_output=True).stdout.decode("ascii", "replace")
pids = {int(m.group(1)) for m in re.finditer(r":8000\s+\S+\s+LISTENING\s+(\d+)", raw)}
# parent chain via wmic-free approach: tasklist is enough to name them
out = subprocess.run([os.path.join(sys32, "tasklist.exe"), "/FO", "CSV", "/NH"],
                     capture_output=True).stdout.decode("mbcs", "replace")
names = {int(r.split('","')[1]): r.split('","')[0].strip('"') for r in out.splitlines() if r.count('","') >= 2}


def in_job(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return f"cannot open (err {ctypes.get_last_error()})"
    b = wintypes.BOOL()
    ok = k32.IsProcessInJob(h, None, ctypes.byref(b))
    k32.CloseHandle(h)
    return bool(b.value) if ok else f"query failed (err {ctypes.get_last_error()})"


print("listening on 8000:", {p: names.get(p) for p in pids})
for p in pids:
    print(f"  pid {p} ({names.get(p)}): in a job object = {in_job(p)}")
for p, n in names.items():
    if n.lower() in ("python.exe", "pythonw.exe"):
        print(f"  all python: pid {p} {n}: in job = {in_job(p)}")
print(f"this checker itself (started from Claude's shell): in job = {in_job(os.getpid())}")
