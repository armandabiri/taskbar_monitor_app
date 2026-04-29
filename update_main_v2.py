import sys
import re
path = r'src\main.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update TEMP scope init
content = content.replace(
    'if self._temp_available:\n            self.scopes["temp"] = ScopeWidget("TEMP", COLOR_TEMP)',
    'if self._temp_available:\n            self.scopes["temp"] = ScopeWidget("TEMP", COLOR_TEMP)\n            self.scopes["temp"].sec_min = 110.0\n            self.scopes["temp"].sec_max = 180.0'
)

# 2. Cleanup CPU/RAM updates
# Using a broader match for the CPU temp section
cpu_pattern = re.compile(r'cpu_temp = get_cpu_temp\(\).*?self.scopes\["ram"\].update_value\(ram, f"\{int\(ram\)\}%"\)', re.DOTALL)
cpu_replacement = 'self.scopes["cpu"].update_value(cpu, f"{int(cpu)}%")\n            self.scopes["ram"].update_value(ram, f"{int(ram)}%")'
content = cpu_pattern.sub(cpu_replacement, content)

# 3. Update TEMP update logic
temp_pattern = re.compile(r'if "temp" in self.scopes:.*?self.scopes\["temp"\].update_value\(temp, f"\{int\(temp\)\}.C"\)', re.DOTALL)
temp_replacement = """if "temp" in self.scopes:
                    c_temp = get_cpu_temp()
                    if c_temp is None:
                        c_temp = gpu.temp_c
                    c_temp_f = c_temp * 9 / 5 + 32 if c_temp is not None else None
                    
                    r_temp = get_ram_temp()
                    r_temp_f = r_temp * 9 / 5 + 32 if r_temp is not None else None
                    
                    text = ""
                    if c_temp_f is not None:
                        text += f"CPU: {int(c_temp_f)}°F"
                    if r_temp_f is not None:
                        text += f" RAM: {int(r_temp_f)}°F"
                    
                    self.scopes["temp"].update_value(
                        value=c_temp_f if c_temp_f is not None else 0.0,
                        text=text.strip() if text else "N/A",
                        auto_scale=True,
                        secondary_value=r_temp_f
                    )"""
content = temp_pattern.sub(temp_replacement, content)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated main.py successfully")
