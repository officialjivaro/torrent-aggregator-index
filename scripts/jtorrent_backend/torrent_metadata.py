from __future__ import annotations

import hashlib
from typing import Any



def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        return {_decode(k): _decode(v) for k, v in value.items()}
    return value


def _get(mapping: dict[Any, Any], key: str, default: Any = None) -> Any:
    return mapping.get(key.encode(), mapping.get(key, default))


def parse_torrent_bytes(data: bytes) -> dict[str, Any]:
    """Return safe metadata extracted from a .torrent file.

    The infohash is SHA-1 over the bencoded `info` dictionary, which is the
    standard BitTorrent v1 infohash. Hybrid/v2-only torrents may need more
    handling later; this extracts v1 metadata when present.
    """
    try:
        import bencodepy
    except ImportError as exc:
        raise RuntimeError("bencodepy is required to parse .torrent metadata; run `python -m pip install -r requirements.txt`") from exc

    decoded = bencodepy.decode(data)
    if not isinstance(decoded, dict):
        raise ValueError("Torrent root is not a dictionary")
    info = _get(decoded, "info")
    if not isinstance(info, dict):
        raise ValueError("Torrent has no info dictionary")
    infohash = hashlib.sha1(bencodepy.encode(info)).hexdigest()
    name = _decode(_get(info, "name"))
    piece_length = _get(info, "piece length")

    trackers: list[str] = []
    announce = _decode(_get(decoded, "announce"))
    if isinstance(announce, str) and announce:
        trackers.append(announce)
    announce_list = _decode(_get(decoded, "announce-list", []))
    if isinstance(announce_list, list):
        for tier in announce_list:
            if isinstance(tier, list):
                for tracker in tier:
                    if isinstance(tracker, str) and tracker not in trackers:
                        trackers.append(tracker)
            elif isinstance(tier, str) and tier not in trackers:
                trackers.append(tier)

    files: list[dict[str, Any]] = []
    size_bytes: int | None = None
    single_length = _get(info, "length")
    if isinstance(single_length, int):
        size_bytes = single_length
        files.append({"path": name, "length": single_length})
    else:
        total = 0
        for f in _get(info, "files", []) or []:
            if not isinstance(f, dict):
                continue
            length = _get(f, "length")
            path_parts = _decode(_get(f, "path", []))
            if isinstance(path_parts, list):
                path = "/".join(str(p) for p in path_parts)
            else:
                path = str(path_parts or "")
            if isinstance(length, int):
                total += length
            files.append({"path": path, "length": length})
        size_bytes = total or None

    return {
        "infohash": infohash,
        "name": name,
        "piece_length": piece_length,
        "trackers": trackers,
        "files": files[:500],
        "size_bytes": size_bytes,
    }
