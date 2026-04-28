import time
import subprocess

t0 = time.time()
out = subprocess.check_output(['wmic', 'PATH', 'Win32_PerfFormattedData_Counters_ThermalZoneInformation', 'get', 'Temperature'], text=True, creationflags=subprocess.CREATE_NO_WINDOW)
print("Time:", time.time() - t0)
print(out)
