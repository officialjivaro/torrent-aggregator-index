from __future__ import annotations
import base64
import io
import json
from pathlib import Path
import pytest
from botocore.exceptions import ClientError
import scripts.publish_release as publisher

POINTER = 'data/v4/current.json'
OLD = 'data/v4/releases/v4-old/manifest.json'
NEW = 'data/v4/releases/v4-test/manifest.json'

class FakeR2Client:
    def __init__(self):
        self.objects = {POINTER: b'{"release_id":"v4-old"}', OLD: b'{}', 'data/status.json': b'{"old":true}',
                        'data/v4/releases/v4-previous/manifest.json': b'{}'}
        self.metadata = {}
        self.uploads, self.deleted = [], []
        self.fail_key, self.corrupt_key = None, None
        self.cors_calls = 0
    def list_objects_v2(self, **kwargs):
        return {'Contents': [{'Key': key, 'Size': len(data)} for key,data in self.objects.items()], 'IsTruncated': False}
    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key])}
    def head_object(self, Bucket, Key):
        return {'ContentLength': len(self.objects[Key]) + (1 if Key == self.corrupt_key else 0), 'Metadata': self.metadata.get(Key, {})}
    def upload_file(self, path, bucket, key, ExtraArgs):
        self.uploads.append(key)
        if key == self.fail_key:
            raise OSError('Interrupted upload')
        self.objects[key] = Path(path).read_bytes()
        self.metadata[key] = ExtraArgs.get('Metadata', {})
    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body
    def delete_objects(self, **kwargs):
        for row in kwargs['Delete']['Objects']:
            key = row['Key']
            self.deleted.append(key)
            self.objects.pop(key, None)
    def put_bucket_cors(self, **kwargs):
        self.cors_calls += 1

@pytest.fixture
def release(monkeypatch, tmp_path):
    public = tmp_path / 'public'
    for key, data in {POINTER: {'release_id': 'v4-test'}, NEW: {}, 'data/status.json': {'new': True}}.items():
        path = public / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')
    fake = FakeR2Client()
    settings = {'publishing': {'r2_bucket': 'torrent-aggregator-index', 'r2_soft_limit_bytes': 1024**2, 'keep_r2_versions': 2}}
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(publisher, 'r2_client', lambda: fake)
    monkeypatch.setattr(publisher, 'load_settings', lambda: settings)
    # Full contract coverage lives in test_search_integrity and the streaming test.
    monkeypatch.setattr(publisher, 'validate_candidate', lambda public: None)
    return public, fake, settings

def test_publish_keeps_active_release_and_saves_rollback(release):
    public, fake, _ = release
    previous = fake.objects[POINTER]
    result = publisher.publish_r2(public)
    assert fake.uploads[-1] == POINTER
    assert OLD in fake.objects and NEW in fake.objects
    assert fake.deleted == ['data/v4/releases/v4-previous/manifest.json']
    assert fake.cors_calls == 0
    snapshot = json.loads(Path(result['rollback_snapshot']).read_text())
    assert base64.b64decode(snapshot['files'][POINTER]) == previous

def test_capacity_failure_never_deletes_or_uploads(release):
    public, fake, settings = release
    settings['publishing']['r2_soft_limit_bytes'] = 1
    with pytest.raises(RuntimeError, match='nothing changed'):
        publisher.publish_r2(public)
    assert not fake.deleted and not fake.uploads

def test_immutable_collision_is_rejected_before_any_upload(release):
    public, fake, _ = release
    fake.objects[NEW] = b'changed'
    with pytest.raises(RuntimeError, match='immutable'):
        publisher.publish_r2(public)
    assert not fake.uploads and not fake.deleted

@pytest.mark.parametrize('key', [NEW, 'data/status.json', POINTER])
def test_interrupted_upload_preserves_old_release_and_metadata(release, key):
    public, fake, _ = release
    before = fake.objects.copy()
    fake.fail_key = key
    with pytest.raises(OSError):
        publisher.publish_r2(public)
    assert all(fake.objects[k] == value for k, value in before.items())
    assert not fake.deleted

def test_verification_failure_never_changes_pointer(release):
    public, fake, _ = release
    before = fake.objects[POINTER]
    fake.corrupt_key = NEW
    with pytest.raises(RuntimeError, match='verification'):
        publisher.publish_r2(public)
    assert fake.objects[POINTER] == before and not fake.deleted

def test_explicit_cors_configuration_is_preserved(release):
    public, fake, settings = release
    settings['publishing']['r2_manage_cors'] = True
    publisher.publish_r2(public)
    assert fake.cors_calls == 1

def test_invalid_candidate_has_no_remote_effects(release, monkeypatch):
    public, fake, _ = release
    def reject(_):
        raise ValueError('Invalid candidate')
    monkeypatch.setattr(publisher, 'validate_candidate', reject)
    with pytest.raises(ValueError):
        publisher.publish_r2(public)
    assert not fake.deleted and not fake.uploads
