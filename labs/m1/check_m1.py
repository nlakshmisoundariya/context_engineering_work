"""M1 exit check: is everything the lab asks for in place? Structure only, never "correct" answers.

    python labs/m1/check_m1.py

Prints PASS/FAIL per item and exits non-zero if any item fails:
1. context_spec/spec_v1.yaml loads with the kit loader
2. its component budgets fit within total_tokens - response_reserve
3. the four system-context files are no longer the placeholders
4. labs/m1/autopsy_attribution.md has at least five findings, each with one valid component and quoted evidence
5. labs/m1/runs/ has a real-model run report for the example spec and for spec_v1
6. labs/m1/decision_records.md has both decision records, filled in
7. your M1 work (context_spec/, labs/m1/) is committed in Git
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT_ROOT))

from novaassist.spec import SpecError, load_spec  # noqa: E402

COMPONENTS = {"system", "user_input", "history", "knowledge", "tools", "memory_state", "budget"}
MIN_FINDINGS = 5
MIN_SYSTEM_WORDS = 30
MIN_RECORD_WORDS = 15
# sha256 of the starter placeholders (text stripped, LF line endings)
PLACEHOLDERS = {
    "role.md": "ade55407b380b1b7f9d9eef202a11ce2d2286b0794c921fac2cfbaf4ff096226",
    "grounding.md": "e6b4bcf74e45ea2f2914813dbfd9fa44d75732f01024c757e8aca945353de7ce",
    "conflicts.md": "79793bcc904c97afd98183c0114bd61c59bcf11e33943dbcbd51ef66ff18e552",
    "tone.md": "ba9a9ee3b2f6d365360b9043fb03fbe9deee8c06f2c823c0bd2ab804fcb38e4e",
}
QUOTE = re.compile(r'"[^"]*\w+\W+\w+\W+\w+[^"]*"|“[^”]*\w+\W+\w+\W+\w+[^”]*”|`[^`]+`')
COMMENT = re.compile(r"<!--.*?-->", re.S)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()


def _words(text: str) -> int:
    return len(re.findall(r"\w+", COMMENT.sub("", text)))


def check_spec(ws: Path) -> tuple[bool, str, object]:
    path = ws / "context_spec" / "spec_v1.yaml"
    if not path.is_file():
        return False, "context_spec/spec_v1.yaml not found (copy example_spec.yaml and amend it)", None
    try:
        spec = load_spec(path)
    except SpecError as exc:
        return False, str(exc), None
    return True, f"spec_v1.yaml loads (spec_version {spec.version})", spec


def check_budgets(spec) -> tuple[bool, str]:
    if spec is None:
        return False, "skipped: spec_v1.yaml does not load"
    total = 0
    for name, comp in spec.data["components"].items():
        tb = comp["token_budget"]
        total += sum(tb.values()) if isinstance(tb, dict) else tb
    available = spec.total_tokens - spec.response_reserve
    return total <= available, f"component budgets {total} of {available} available tokens"


def check_system_files(ws: Path, spec) -> tuple[bool, str]:
    files = spec.component("system").get("files", []) if spec is not None else \
        [f"system/v1/{name}" for name in PLACEHOLDERS]
    base = spec.path.parent if spec is not None else ws / "context_spec"
    problems = []
    for name in PLACEHOLDERS:
        rel = next((f for f in files if Path(f).name == name), None)
        if rel is None:
            problems.append(f"{name} is not listed in components.system.files")
            continue
        path = base / rel
        if not path.is_file():
            problems.append(f"{rel} not found")
            continue
        text = _text(path)
        if hashlib.sha256(text.encode("utf-8")).hexdigest() == PLACEHOLDERS[name]:
            problems.append(f"{rel} is still the placeholder")
        elif _words(text) < MIN_SYSTEM_WORDS:
            problems.append(f"{rel} has fewer than {MIN_SYSTEM_WORDS} words outside comments")
    return not problems, "; ".join(problems) or "all four system files designed"


def findings(path: Path) -> tuple[list[str], list[str]]:
    """Valid finding rows and the problems with the others."""
    valid, problems = [], []
    for line in _text(path).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|") or len(cells) < 4 or not cells[0].isdigit():
            continue
        if not any(cells[1:]):
            continue  # an empty template row
        component = cells[1].strip("`*").lower().replace(" ", "_").replace("/", "_")
        if component not in COMPONENTS:
            problems.append(f"row {cells[0]}: component {cells[1]!r} is not one of {sorted(COMPONENTS)}")
        elif not QUOTE.search(cells[2]):
            problems.append(f"row {cells[0]}: no quoted evidence")
        else:
            valid.append(cells[0])
    return valid, problems


def check_attribution(ws: Path) -> tuple[bool, str]:
    path = ws / "labs" / "m1" / "autopsy_attribution.md"
    if not path.is_file():
        return False, "labs/m1/autopsy_attribution.md not found"
    valid, problems = findings(path)
    ok = len(valid) >= MIN_FINDINGS
    msg = f"{len(valid)} findings with a valid component and quoted evidence (need {MIN_FINDINGS})"
    return ok, msg + (f"; {'; '.join(problems)}" if problems else "")


def check_runs(ws: Path, allow_fake: bool) -> tuple[bool, str]:
    runs = ws / "labs" / "m1" / "runs"
    missing = []
    for stem in ("example_spec", "spec_v1"):
        reports = sorted(runs.glob(f"{stem}_*.md")) if runs.is_dir() else []
        if not allow_fake:
            reports = [r for r in reports if "(fake client" not in r.read_text(encoding="utf-8")]
        if not reports:
            missing.append(stem)
    if missing:
        hint = "" if allow_fake else " (fake-client reports do not count: run without --fake)"
        return False, f"no run report for {', '.join(missing)} in labs/m1/runs/{hint}"
    return True, "run reports present for example_spec and spec_v1"


def check_decisions(ws: Path) -> tuple[bool, str]:
    path = ws / "labs" / "m1" / "decision_records.md"
    if not path.is_file():
        return False, "labs/m1/decision_records.md not found (copy decision_record_template.md)"
    text = _text(path)
    template = _text(Path(__file__).resolve().parent / "decision_record_template.md")
    template_words = set(re.findall(r"\w+", template.lower()))
    problems = []
    for record in ("D-M1-01", "D-M1-02"):
        match = re.search(rf"^##\s*{record}\b(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        if not match:
            problems.append(f"{record} section missing")
            continue
        own = [w for w in re.findall(r"\w+", COMMENT.sub("", match.group(1)).lower()) if w not in template_words]
        if len(own) < MIN_RECORD_WORDS:
            problems.append(f"{record} is not filled in")
    return not problems, "; ".join(problems) or "D-M1-01 and D-M1-02 filled in"


def check_git(ws: Path) -> tuple[bool, str]:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "context_spec", "labs/m1"], cwd=ws,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"git not available: {exc}"
    if out.returncode != 0:
        if "not a git repository" in out.stderr:
            return False, "this workspace is not a Git repository - ask the trainer (or run git init once)"
        return False, f"git status failed: {out.stderr.strip()}"
    pending = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if pending:
        return False, f"{len(pending)} uncommitted change(s) under context_spec/ or labs/m1/ - run git add and git commit"
    return True, "M1 work committed"


def run_checks(ws: Path, allow_fake: bool = False) -> list[tuple[str, bool, str]]:
    ok, msg, spec = check_spec(ws)
    results = [("1 spec_v1 loads", ok, msg)]
    results.append(("2 budgets fit", *check_budgets(spec)))
    results.append(("3 system files designed", *check_system_files(ws, spec)))
    results.append(("4 autopsy attribution", *check_attribution(ws)))
    results.append(("5 run reports", *check_runs(ws, allow_fake)))
    results.append(("6 decision records", *check_decisions(ws)))
    results.append(("7 committed", *check_git(ws)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1 structural exit check.")
    parser.add_argument("--workspace", type=Path, default=KIT_ROOT)
    parser.add_argument("--allow-fake", action="store_true", help="count fake-client run reports (offline dry runs)")
    args = parser.parse_args(argv)
    results = run_checks(args.workspace, args.allow_fake)
    for name, ok, msg in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<26} {msg}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{'M1 exit check PASS' if not failed else f'M1 exit check FAIL ({failed} item(s))'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
