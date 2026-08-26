#!/usr/bin/env python3
"""Final governance verification for the JTorrent static search backend."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    report_path = Path(args.output) if args.output else ROOT / "artifacts/backend-final-verification.json"
    audit_path = report_path.with_name("backend-governance-report.json")
    command = [
        sys.executable,
        str(ROOT / "scripts/audit_governance.py"),
        "--config", "config/sources.yml",
        "--policy", "config/search-policy.json",
        "--output", str(audit_path),
        "--quiet",
    ]
    if args.generated_dir:
        command.extend(["--generated-dir", args.generated_dir])
    try:
        run(command)
        workflow = (ROOT / ".github/workflows/update-index.yml").read_text(encoding="utf-8")
        required_fragments = [
            "persist-credentials: false",
            "audit_governance.py",
            "verify_final_phase.py",
            "backend-governance-${{ github.sha }}",
            "actions/upload-artifact@v7",
        ]
        missing = [fragment for fragment in required_fragments if fragment not in workflow]
        if missing:
            raise RuntimeError("Backend workflow is missing final governance controls: " + ", ".join(missing))
        governance = json.loads(audit_path.read_text(encoding="utf-8"))
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repository": "officialjivaro/torrent-aggregator-index",
            "governance": governance,
            "workflow_controls": required_fragments,
            "passed": True,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        generated = governance.get("generated_backend") or {}
        suffix = f", {generated.get('item_count', 0):,} generated items" if generated else ""
        print(f"Final backend governance verified{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
