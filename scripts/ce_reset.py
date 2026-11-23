"""Restore your workspace to a session checkpoint (installed on the servers as ``ce-reset``).

    ce-reset --list                  # checkpoints available
    ce-reset --to S03 --kit-only     # refresh kit-managed folders (tools, scripts, data, new labs); your code is untouched
    ce-reset --to S03                # full reset: every folder in the checkpoint, including novaassist/ and context_spec/

Before anything is replaced, the current version of every affected path is copied to
~/ce-l2-backups/<timestamp>-before-S03/. Never touched: .env, .git, submissions/.
Runs as you; no sudo needed.

Locations: --workspace (or CE_WORKSPACE, default ~/ce-l2) and --checkpoints (or CE_CHECKPOINT_DIR,
default /opt/ce-l2/checkpoints, holding ce-l2-checkpoint-S<nn>.tar.gz).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

KIT_MANAGED = {"tools", "scripts", "data", "requirements.txt", ".env.example", "README.md"}
NEVER_TOUCH = {".env", ".git", "submissions"}
STATE_FILE = ".ce-l2-state.json"


class ResetError(RuntimeError):
    pass


def default_workspace() -> Path:
    return Path(os.environ.get("CE_WORKSPACE") or Path.home() / "ce-l2")


def default_checkpoints() -> Path:
    return Path(os.environ.get("CE_CHECKPOINT_DIR") or "/opt/ce-l2/checkpoints")


def available(checkpoints: Path) -> list[str]:
    return sorted(m.group(1) for p in checkpoints.glob("ce-l2-checkpoint-S*.tar.gz")
                  if (m := re.match(r"ce-l2-checkpoint-(S\d\d)\.tar\.gz$", p.name)))


def extract(archive: Path, session: str, into: Path) -> Path:
    """Extract safely: every member must live under '<session>/' with no '..' or absolute paths."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != session or ".." in parts or member.name.startswith("/") \
                    or not (member.isfile() or member.isdir()):
                raise ResetError(f"unsafe or unexpected entry in {archive.name}: {member.name}")
        tar.extractall(into, filter="data")
    return into / session


def plan(root: Path, kit_only: bool, workspace: Path) -> list[tuple[str, str]]:
    """List of (relative path, action) where action is 'replace' or 'add'."""
    actions = []
    for entry in sorted(p.name for p in root.iterdir() if p.name != "CHECKPOINT.json"):
        if entry in NEVER_TOUCH:
            continue
        if not kit_only:
            actions.append((entry, "replace"))
        elif entry in KIT_MANAGED:
            actions.append((entry, "replace"))
        elif entry == "labs":  # kit-only: add lab folders you do not have yet; never overwrite your lab work
            for lab in sorted(p.name for p in (root / "labs").iterdir()):
                if not (workspace / "labs" / lab).exists():
                    actions.append((f"labs/{lab}", "add"))
    return actions


def reset(session: str, workspace: Path, checkpoints: Path, kit_only: bool = False, dry_run: bool = False,
          backup_root: Path | None = None) -> dict:
    archive = checkpoints / f"ce-l2-checkpoint-{session}.tar.gz"
    if not archive.exists():
        raise ResetError(f"checkpoint {session} not found in {checkpoints} (available: {', '.join(available(checkpoints)) or 'none'})")
    workspace.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = (backup_root or Path.home() / "ce-l2-backups") / f"{stamp}-before-{session}"
    with tempfile.TemporaryDirectory() as tmp:
        root = extract(archive, session, Path(tmp))
        actions = plan(root, kit_only, workspace)
        if dry_run:
            return {"session": session, "backup": None, "actions": actions}
        for rel, _ in actions:  # 1. back up everything we are about to replace
            current = workspace / rel
            if current.exists():
                target = backup / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                (shutil.copytree if current.is_dir() else shutil.copy2)(current, target)
        for rel, _ in actions:  # 2. replace
            current, fresh = workspace / rel, root / rel
            if current.is_dir():
                shutil.rmtree(current)
            elif current.exists():
                current.unlink()
            current.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if fresh.is_dir() else shutil.copy2)(fresh, current)
        info = json.loads((root / "CHECKPOINT.json").read_text(encoding="utf-8"))
    state = {"checkpoint": session, "mode": "kit-only" if kit_only else "full",
             "dataset_version": info.get("dataset_version"), "restored_at": stamp,
             "backup": str(backup) if backup.exists() else None}
    (workspace / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {"session": session, "backup": state["backup"], "actions": actions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore your workspace to a session checkpoint.")
    parser.add_argument("--to", dest="session", help="checkpoint, e.g. S03")
    parser.add_argument("--kit-only", action="store_true", help="refresh kit-managed folders only; keep your code")
    parser.add_argument("--list", action="store_true", help="list available checkpoints")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--checkpoints", type=Path, default=None)
    args = parser.parse_args(argv)
    workspace = args.workspace or default_workspace()
    checkpoints = args.checkpoints or default_checkpoints()
    if args.list:
        print("available checkpoints:", ", ".join(available(checkpoints)) or "none")
        return 0
    if not args.session or not re.fullmatch(r"S\d\d", args.session):
        parser.error("use --to S<nn>, e.g. --to S03")
    try:
        result = reset(args.session, workspace, checkpoints, args.kit_only, args.dry_run)
    except ResetError as exc:
        print(f"ce-reset: {exc}")
        return 1
    verb = "would change" if args.dry_run else "restored"
    for rel, action in result["actions"]:
        print(f"  {action:<7} {rel}")
    print(f"{verb} {len(result['actions'])} paths from {args.session} in {workspace}")
    if result["backup"]:
        print(f"your previous files are in {result['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
