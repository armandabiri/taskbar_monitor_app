# Standard library imports
import os
import pathlib
import sys


def align_yaml_comments(file_path: str, target_col: int = 60) -> None:
    """Aligns inline comments in a YAML file to a target column."""
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Align comments to target_col
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if "#" in stripped:
            pos = stripped.find("#")
            # Check if it's an inline comment (not at start and preceded by non-space)
            if pos > 0 and not stripped[:pos].isspace():
                # Get the content before '#' and strip trailing whitespace
                content_before = stripped[:pos].rstrip()

                # Calculate required spaces to reach target_col
                # Column is 0-indexed in code, so column 60 means index 60
                spaces_needed = target_col - len(content_before)
                if spaces_needed < 1:
                    spaces_needed = 1 # At least one space

                new_line = content_before + " " * spaces_needed + stripped[pos:]
                lines[i] = new_line + "\n"

    # Write back to file
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

def find_yaml_files(root_dir: str) -> list[str]:
    """Finds all .yaml and .yml files in the given directory and subdirectories."""
    files: list[str] = []
    for ext in ["*.yaml", "*.yml"]:
        for file_path in pathlib.Path(root_dir).rglob(ext):
            files.append(str(file_path))
    return files

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python align_yaml_comments.py <file_or_directory> [target_column]")
        sys.exit(1)

    input_path = sys.argv[1]
    target_column = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    if os.path.isfile(input_path):
        align_yaml_comments(input_path, target_column)
    elif os.path.isdir(input_path):
        yaml_files = find_yaml_files(input_path)
        if not yaml_files:
            print("No YAML files found in the directory.")
        else:
            for yf in yaml_files:
                print(f"Processing {yf} (Target Col: {target_column})")
                align_yaml_comments(yf, target_column)
            print("All YAML files processed.")
    else:
        print("Path is neither a file nor a directory.")
        sys.exit(1)
