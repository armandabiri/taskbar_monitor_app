import sys
sys.path.append('src')
import logging
logging.basicConfig(level=logging.DEBUG)
from services.system_info import get_pdh_cpu_temp, _init_pdh_temp
_init_pdh_temp()
print(get_pdh_cpu_temp())
