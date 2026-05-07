from __future__ import annotations

import json
from pathlib import Path
from frontmatter import iter_markdown_files, parse_frontmatter

ROOT = Path(__file__).resolve().parents[1]
index = {}
for path, rel in iter_markdown_files():
    data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    if data and data.get("id"):
        index[data["id"]] = {"path": rel, "genre": data.get("genre"), "status": data.get("status")}
output = ROOT / "id-index.json"
output.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"wrote {output.relative_to(ROOT)} with {len(index)} ids")
