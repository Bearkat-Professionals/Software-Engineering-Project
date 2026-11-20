r"""NFR-08 check: do two JSON exports have the same structure? (M7, #174)

The same export run on Windows and on Linux must give the same JSON: the same keys,
in the same order, with the same types. Only the values may differ.

    .\dev cmpexp windows-export.json linux-export.json
    python3 dev.py cmpexp windows-export.json linux-export.json

Prints every difference with its path (e.g. devices[].vendor) and exits 1 if there is
any, 0 if the structures match. A field that is null in one file and has a value in the
other isn't a difference: a device with no name is a value, not a structure. Lists are
compared by the shape of their items, since each machine sees different devices.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

NULL = "null"

def shape(value: Any) -> Any:
    """The structure of a JSON value: an object's keys in order with their shapes, a
    list's item shape (merged over every item), or a scalar's type name."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        merged: Any = None
        for item in value:
            merged = _merge(merged, shape(item))
        return [merged]
    if value is None:
        return NULL
    if isinstance(value, bool):                     # before int: True is an int in Python
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"

def compare_shapes(left: Any, right: Any, path: str = "") -> list[str]:
    """Every structural difference between two shapes, each as "path: what differs"."""
    where = path or "(top level)"
    if left is None or right is None or left == NULL or right == NULL:
        return []                                   # an empty list or a null: nothing to compare
    if isinstance(left, dict) and isinstance(right, dict):
        problems = []
        if list(left) != list(right):
            missing, extra = [k for k in left if k not in right], [k for k in right if k not in left]
            if missing:
                problems.append(f"{where}: only in the first file: {', '.join(missing)}")
            if extra:
                problems.append(f"{where}: only in the second file: {', '.join(extra)}")
            if not missing and not extra:
                problems.append(f"{where}: keys in a different order: {list(left)} vs {list(right)}")
        for key in left:
            if key in right:
                problems += compare_shapes(left[key], right[key], f"{path}.{key}" if path else key)
        return problems
    if isinstance(left, list) and isinstance(right, list):
        return compare_shapes(left[0], right[0], f"{path}[]")
    if _kind(left) != _kind(right):
        return [f"{where}: {_kind(left)} in the first file, {_kind(right)} in the second"]
    return []

def compare_files(first: Path, second: Path) -> list[str]:
    return compare_shapes(shape(_load(first)), shape(_load(second)))

def _merge(a: Any, b: Any) -> Any:
    """One shape for two list items: null gives way to the other; objects merge key by key."""
    if a is None or a == NULL:
        return b
    if b is None or b == NULL:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        merged = {key: _merge(a[key], b.get(key)) for key in a}
        merged.update({key: value for key, value in b.items() if key not in a})
        return merged
    if isinstance(a, list) and isinstance(b, list):
        return [_merge(a[0], b[0])]
    return a

def _kind(item_shape: Any) -> str:
    if isinstance(item_shape, dict):
        return "object"
    if isinstance(item_shape, list):
        return "list"
    return item_shape

def _load(path: Path) -> Any:
    with open(path, encoding="utf-8-sig") as file:   # -sig: a BOM from a Windows editor is fine
        return json.load(file)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that two JSON exports have the same structure (NFR-08).")
    parser.add_argument("first", type=Path, help="e.g. the export saved on Windows")
    parser.add_argument("second", type=Path, help="e.g. the export saved on Linux")
    args = parser.parse_args(argv)
    try:
        problems = compare_files(args.first, args.second)
    except (OSError, ValueError) as exc:
        print(f"Couldn't read the exports: {exc}")
        return 2
    if problems:
        print(f"{len(problems)} structural difference(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("Same structure: the same keys, in the same order, with the same types. Only values differ.")
    return 0

if __name__ == "__main__":
    sys.exit(main())