from __future__ import annotations

import json
from pathlib import Path

import publish_gate
from publish_gate import evaluate

LIVE = {"release_id": "v4-live", "item_count": 200_000, "healthy_source_count": 12, "counts_by_scope": {"open-authorized": 80_000, "third-party-unverified": 120_000}}
HEALTHY = {"sources": [{"id": "ubuntu", "state": "healthy", "required": True}, {"id": "optional", "state": "failed", "required": False}]}


def candidate(items=210_000, healthy=12, open_count=90_000, third=120_000):
    return {"release_id": "v4-new", "item_count": items, "healthy_source_count": healthy, "counts_by_scope": {"open-authorized": open_count, "third-party-unverified": third}}


def gate(status, health=HEALTHY, live=LIVE):
    return evaluate(status, health, live, max_item_drop=0.10, max_scope_drop=0.15, max_healthy_drop=2)


def test_growth_passes_and_optional_failure_is_allowed():
    assert gate(candidate()) == []


def test_large_item_drop_fails():
    errors = gate(candidate(items=170_000, open_count=60_000, third=110_000))
    assert any("below live" in error and "Item count" in error for error in errors)


def test_scope_collapse_fails_even_when_total_grows():
    errors = gate(candidate(items=260_000, open_count=200_000, third=60_000))
    assert any("third-party-unverified" in error for error in errors)


def test_failed_or_empty_required_source_fails():
    health = {"sources": [{"id": "ubuntu", "state": "empty", "required": True}]}
    assert any("Required source ubuntu is empty" in error for error in gate(candidate(), health))


def test_healthy_source_drop_fails():
    assert any("Healthy sources fell" in error for error in gate(candidate(healthy=9)))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_unreadable_live_status_fails_closed(tmp_path: Path):
    write(tmp_path / "data/status.json", candidate())
    write(tmp_path / "data/source-health.json", HEALTHY)

    def broken_fetch(_url):
        raise ConnectionError("offline")

    assert publish_gate.main(["--public-dir", str(tmp_path)], fetch=broken_fetch) == 1
    assert publish_gate.main(["--public-dir", str(tmp_path), "--allow-missing-live"], fetch=broken_fetch) == 0


def test_main_writes_report(tmp_path: Path):
    write(tmp_path / "data/status.json", candidate())
    write(tmp_path / "data/source-health.json", HEALTHY)
    report = tmp_path / "gate.json"
    assert publish_gate.main(["--public-dir", str(tmp_path), "--report", str(report)], fetch=lambda _url: LIVE) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["passed"] is True
