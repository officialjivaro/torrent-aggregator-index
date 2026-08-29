from __future__ import annotations

import json
from pathlib import Path

import scripts.publish_release as publisher


class FakeR2Client:
    def __init__(self, objects: dict[str, int] | None = None) -> None:
        self.cors_calls = 0
        self.uploads: list[str] = []
        self.objects = dict(objects or {})
        self.deleted: list[str] = []

    def list_objects_v2(self, **_kwargs):
        return {
            "Contents": [{"Key": key, "Size": size} for key, size in sorted(self.objects.items())],
            "IsTruncated": False,
        }

    def put_bucket_cors(self, **_kwargs) -> None:
        self.cors_calls += 1

    def upload_file(self, path: str, _bucket: str, key: str, **_kwargs) -> None:
        self.uploads.append(key)
        self.objects[key] = Path(path).stat().st_size

    def delete_objects(self, **kwargs) -> None:
        for item in kwargs["Delete"]["Objects"]:
            key = item["Key"]
            self.deleted.append(key)
            self.objects.pop(key, None)


def public_fixture(tmp_path: Path) -> Path:
    public = tmp_path / "public"
    pointer = public / "data" / "v4" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"release_id": "v4-test"}), encoding="utf-8")
    release = public / "data" / "v4" / "releases" / "v4-test" / "manifest.json"
    release.parent.mkdir(parents=True)
    release.write_text("{}", encoding="utf-8")
    return public


def run_publish(monkeypatch, tmp_path: Path, *, manage_cors: bool) -> FakeR2Client:
    fake = FakeR2Client()
    monkeypatch.setattr(publisher, "r2_client", lambda: fake)
    monkeypatch.setattr(
        publisher,
        "load_settings",
        lambda: {"publishing": {
            "r2_bucket": "torrent-aggregator-index",
            "r2_manage_cors": manage_cors,
            "r2_soft_limit_bytes": 1024 * 1024,
            "keep_r2_versions": 3,
        }},
    )
    publisher.publish_r2(public_fixture(tmp_path))
    return fake


def test_r2_publish_skips_cors_for_object_scoped_credentials(monkeypatch, tmp_path: Path) -> None:
    fake = run_publish(monkeypatch, tmp_path, manage_cors=False)
    assert fake.cors_calls == 0
    assert fake.uploads[-1] == "data/v4/current.json"


def test_r2_publish_can_manage_cors_when_explicitly_enabled(monkeypatch, tmp_path: Path) -> None:
    fake = run_publish(monkeypatch, tmp_path, manage_cors=True)
    assert fake.cors_calls == 1


def test_r2_retention_keeps_current_and_one_previous_release(monkeypatch, tmp_path: Path) -> None:
    fake = FakeR2Client({
        "data/v4/releases/v4-old-1/manifest.json": 2,
        "data/v4/releases/v4-old-2/manifest.json": 2,
    })
    monkeypatch.setattr(publisher, "r2_client", lambda: fake)
    monkeypatch.setattr(
        publisher,
        "load_settings",
        lambda: {"publishing": {
            "r2_bucket": "torrent-aggregator-index",
            "r2_manage_cors": False,
            "r2_soft_limit_bytes": 1024 * 1024,
            "keep_r2_versions": 2,
        }},
    )
    publisher.publish_r2(public_fixture(tmp_path))
    release_ids = {
        key.split("/")[3] for key in fake.objects
        if key.startswith("data/v4/releases/")
    }
    assert release_ids == {"v4-old-2", "v4-test"}
