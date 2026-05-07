---
id: shared.shell-operations
genre: shared
applies_to:
  - all
load_mode: reference
status: active
schema_version: 2
introduced_in: 2026.04.0
updated: 2026-04-29
owners:
  - Intelag Engineering
supersedes: []
doc_version: 1.0.1
---
File and Folder Operation Rules

Platform detection:

- Linux/macOS: use bash
- Windows: use PowerShell
- If unknown, ask the user

Core principle: NEVER rewrite/regenerate file contents to move, rename, or copy a file. Use shell commands. Rewriting wastes tokens and risks content drift.

Moving files:

- bash: mv old new
- PowerShell: Move-Item -Path old -Destination new
- mv/Move-Item handles rename in the same command
- NEVER create_file at new path then delete old path

Copying files:

- bash: cp source dest
- PowerShell: Copy-Item -Path source -Destination dest
- After copy, apply only targeted edits to the copy via str_replace

Creating directories:

- bash: mkdir -p path
- PowerShell: New-Item -ItemType Directory -Path path -Force
- Always create target directory before moving files into it

Bulk moves:

- bash: mv dir/*.dart dest/ or for loop with mv
- PowerShell: Move-Item -Path dir/*.dart -Destination dest/ or foreach loop with Move-Item
- Never rewrite multiple files individually to move them

Updating imports after move:

- bash: find lib/ -name "*.dart" -exec sed -i 's|old_path|new_path|g' {} +
- macOS sed requires: sed -i '' (empty string argument)
- PowerShell: Get-ChildItem -Path lib/ -Filter *.dart -Recurse | ForEach-Object { (Get-Content $_.FullName -Raw) -replace 'old_path','new_path' | Set-Content $_.FullName -NoNewline }
- Or use str_replace on specific files where you know the import exists
- Only touch import lines, never rewrite entire files

Renaming classes/symbols after move:

- bash: find lib/ -name "*.dart" -exec sed -i 's/OldName/NewName/g' {} +
- PowerShell: Get-ChildItem -Path lib/ -Filter *.dart -Recurse | ForEach-Object { (Get-Content $_.FullName -Raw) -replace 'OldName','NewName' | Set-Content $_.FullName -NoNewline }
- Or use str_replace targeting only changed lines

Deleting files:

- bash: rm path
- PowerShell: Remove-Item -Path path
- Delete directories: rm -rf path / Remove-Item -Path path -Recurse -Force
- Always state the reason for deletion

Verifying after restructure:

- bash: find lib/ -name "*.dart" | sort
- PowerShell: Get-ChildItem -Path lib/ -Filter *.dart -Recurse | Select-Object FullName
- Check broken imports: grep -r "old_path" lib/ / Select-String -Path lib/**/*.dart -Pattern "old_path"
- Run: flutter analyze

Master rule: if you are about to output full contents of an existing file into create_file at a new path, STOP. Use mv/Move-Item instead. Only output file contents when creating a genuinely new file or making content changes via str_replace.

Plan before executing: for 3+ file operations, state the full plan with paths and commands before running anything. Let the user approve first.

Quick reference:

- Move/rename: mv / Move-Item
- Copy: cp / Copy-Item
- Create dir: mkdir -p / New-Item -ItemType Directory -Force
- Find-replace in files: sed / Get-ChildItem + -replace + Set-Content -NoNewline
- Delete file: rm / Remove-Item
- Delete dir: rm -rf / Remove-Item -Recurse -Force
- List files: find -name "*.dart" / Get-ChildItem -Filter*.dart -Recurse
- Search in files: grep -r / Select-String

## Section Break