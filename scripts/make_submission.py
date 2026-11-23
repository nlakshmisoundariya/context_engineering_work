"""Zip your capstone workspace into ~/submissions/ for the trainer to collect.

    python scripts/make_submission.py              # or: make_submission

Includes your code, Context Spec, notebooks, Git history, traces and reports.
Excludes .env (secrets), Python caches, environments, indexes, vector stores, the kit-provided dataset,
backups and earlier submissions. Prints the zip path and size.
"""
from __future__ import annotations

import argparse
import fnmatch
import getpass
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

EXCLUDE = [".env", "*.env.local", "__pycache__", "*.pyc", ".ipynb_checkpoints", ".pytest_cache", ".venv", "venv",
           "env", "indexes", "*.faiss", "*.index", "qdrant_data", "submissions", "ce-l2-backups"]
EXCLUDE_PATHS = ["data/novatech"]  # kit-provided dataset: identical for everyone, restored by ce-reset
SIZE_WARNING_MB = 200


def excluded(rel: Path) -> bool:
    posix = rel.as_posix()
    if any(posix == p or posix.startswith(p + "/") for p in EXCLUDE_PATHS):
        return True
    return any(fnmatch.fnmatch(part, pattern) for part in rel.parts for pattern in EXCLUDE)


def make_submission(workspace: Path, out_dir: Path, label: str | None = None) -> Path:
    if not workspace.is_dir():
        raise SystemExit(f"workspace {workspace} not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{label or getpass.getuser()}-capstone-{stamp}.zip"
    target = out_dir / name
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(workspace.rglob("*")):
            rel = path.relative_to(workspace)
            if path.is_file() and not excluded(rel) and path.resolve() != target.resolve():
                zf.write(path, rel.as_posix())
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create your capstone submission zip.")
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("CE_WORKSPACE") or Path.home() / "ce-l2"))
    parser.add_argument("--out", type=Path, default=Path.home() / "submissions")
    parser.add_argument("--label", default=None, help="name prefix (default: your username)")
    args = parser.parse_args(argv)
    target = make_submission(args.workspace, args.out, args.label)
    size_mb = target.stat().st_size / 1024 / 1024
    with zipfile.ZipFile(target) as zf:
        count = len(zf.namelist())
    print(f"submission written: {target}  ({size_mb:.1f} MB, {count} files)")
    if size_mb > SIZE_WARNING_MB:
        print(f"warning: larger than {SIZE_WARNING_MB} MB - check for large files you do not need to submit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
