"""CPU of a process over a window. Usage: python spike/cpu_measure.py <pid> [seconds]

Reports two figures. GetProcessTimes (psutil cpu_times) is tick-sampled at
15.625 ms on Windows, so a process that wakes briefly can read as zero.
QueryProcessCycleTime counts CPU cycles and does not have that blind spot;
cycles are converted to seconds with the processor's rated clock, so treat
that figure as an estimate. Percentages are of one core."""
import ctypes
import json
import subprocess
import sys
import time

import psutil

pid = int(sys.argv[1])
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
k32 = ctypes.WinDLL("kernel32")
h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION


def cycles() -> int:
    c = ctypes.c_ulonglong()
    k32.QueryProcessCycleTime(h, ctypes.byref(c))
    return c.value


mhz = int(subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Processor | Select -First 1).MaxClockSpeed"],
                         capture_output=True, text=True).stdout.strip())
p = psutil.Process(pid)
t0, c0, y0 = time.perf_counter(), p.cpu_times(), cycles()
time.sleep(secs)
t1, c1, y1 = time.perf_counter(), p.cpu_times(), cycles()
cpu = (c1.user - c0.user) + (c1.system - c0.system)
wall = t1 - t0
cyc_s = (y1 - y0) / (mhz * 1e6)
print(json.dumps({"pid": pid, "name": p.name(), "wall_s": round(wall, 2),
                  "ticks_cpu_s": round(cpu, 3), "ticks_pct_one_core": round(100 * cpu / wall, 3),
                  "cycles": y1 - y0, "rated_mhz": mhz, "cycles_cpu_s": round(cyc_s, 3),
                  "cycles_pct_one_core": round(100 * cyc_s / wall, 3),
                  "rss_mb": round(p.memory_info().rss / 2**20, 1), "threads": p.num_threads()}))
