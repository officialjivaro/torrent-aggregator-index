"""Cross-check every document and posting before a v4 release can be served."""
from __future__ import annotations

import json
from array import array
from collections import Counter
from pathlib import Path


SCOPES = {"open-authorized", "third-party-unverified"}


def required_scope(value: object) -> str:
    if not isinstance(value, str) or value not in SCOPES:
        raise ValueError("Missing or unknown policy_scope; refusing an implicitly authorized record")
    return value


def validate_scopes(public: Path, manifest: dict, root: dict, status: dict, source_scopes: dict | None = None) -> None:
    public = public.resolve()
    release_prefix = f"data/v4/releases/{manifest['release_id']}/"

    def read(relative: str):
        path = (public / relative).resolve()
        if not relative.startswith(release_prefix) or not path.is_relative_to(public):
            raise ValueError("Index file escapes the current release")
        return json.loads(path.read_text(encoding="utf-8"))

    scopes = manifest.get("scopes", [])
    if len(set(scopes)) != len(scopes) or not set(scopes).issubset(SCOPES):
        raise ValueError("Manifest has unknown or duplicate scopes")
    categories, sources = manifest.get("categories", []), manifest.get("sources", [])
    category_map = {value: index for index, value in enumerate(categories)}
    source_map = {value: index for index, value in enumerate(sources)}
    scope_map = {value: index for index, value in enumerate(scopes)}
    # Compact integer arrays keep verification practical for large catalogs.
    identities = array('i')
    counts: Counter[str] = Counter()
    for shard in manifest.get("doc_shards", []):
        docs = read(shard["path"])
        start = len(identities) // 3
        if not docs or shard.get("start_doc_id") != start or shard.get("count") != len(docs):
            raise ValueError("Document shard count/range mismatch")
        for doc in docs:
            if doc.get("doc_id") != len(identities) // 3:
                raise ValueError("Document IDs must be unique, contiguous, and ordered")
            scope = required_scope(doc.get("policy_scope"))
            source = doc.get("source_id") or "unknown"
            if (source_scopes or {}).get(source) == "third-party-unverified" and scope != "third-party-unverified":
                raise ValueError(f"Third-party source {source} incorrectly marked authorized")
            try:
                identities.extend((category_map[doc.get("category") or "uncategorized"], source_map[source], scope_map[scope]))
            except KeyError as exc:
                raise ValueError("Document classification absent from manifest") from exc
            counts[scope] += 1
        if shard.get("end_doc_id") != len(identities) // 3 - 1:
            raise ValueError("Document shard end range mismatch")
    count = len(identities) // 3
    if set(counts) != set(scopes):
        raise ValueError("Manifest scopes disagree with its documents")
    for label, metadata in (("manifest", manifest), ("root", root), ("status", status)):
        if metadata.get("item_count") != count or metadata.get("counts_by_scope") != dict(counts):
            raise ValueError(f"{label} item/scope counts disagree with documents")
    if manifest.get("doc_count") != count:
        raise ValueError("Manifest doc_count disagrees with documents")

    def check_rows(rows: list) -> None:
        for row in rows:
            if not isinstance(row, list) or len(row) != 5 or any(type(value) is not int for value in row):
                raise ValueError("Invalid posting tuple")
            doc_id = row[0]
            if not 0 <= doc_id < count or list(identities[doc_id * 3:doc_id * 3 + 3]) != row[1:4]:
                raise ValueError("Posting category/source/scope disagrees with document")

    for bucket in manifest.get("token_buckets", []):
        for descriptor in read(bucket["path"]).get("tokens", {}).values():
            total = 0
            if "postings" in descriptor:
                if descriptor.get("chunks"):
                    raise ValueError("Token mixes inline and chunked postings")
                rows = descriptor["postings"]
                check_rows(rows)
                total = len(rows)
            else:
                for chunk in descriptor.get("chunks", []):
                    rows = read(chunk["path"])
                    check_rows(rows)
                    if chunk.get("count") != len(rows):
                        raise ValueError("Posting chunk count mismatch")
                    total += len(rows)
            if descriptor.get("count") != total:
                raise ValueError("Token posting count mismatch")
