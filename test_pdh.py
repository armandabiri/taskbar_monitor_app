import ctypes
from ctypes import wintypes
import time

pdh = ctypes.windll.pdh

query = wintypes.HANDLE()
pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))

counter = wintypes.HANDLE()
# NOTE: localized strings might cause issues if Windows is not English. 
# Win32_PerfRawData_Counters_ThermalZoneInformation might be better
path = r"\Thermal Zone Information(*)\Temperature"
res = pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter))
if res != 0:
    print("AddCounter failed:", hex(res))

t0 = time.time()
pdh.PdhCollectQueryData(query)
print("CollectTime:", time.time() - t0)

class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", wintypes.DWORD),
        ("doubleValue", ctypes.c_double)
    ]

# 0x200 = PDH_FMT_DOUBLE
val = PDH_FMT_COUNTERVALUE()
res = pdh.PdhGetFormattedCounterValue(counter, 0x200, None, ctypes.byref(val))
if res != 0:
    print("GetValue failed:", hex(res))
else:
    print("Temp (K):", val.doubleValue)
