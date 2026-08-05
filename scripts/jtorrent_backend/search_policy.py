from __future__ import annotations

import copy
from typing import Any


def apply_policy(config: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a copied source config with the public-search policy applied.

    The full configured source registry remains in ``config/sources.yml``.  This
    policy layer disables selected sources only for the generated public index
    and records an explicit reason for each exclusion.
    """

    result = copy.deepcopy(config)
    disabled = policy.get("disabledSources") or {}
    disabled_records: list[dict[str, Any]] = []
    for source in result.get("sources", []):
        source_id = source.get("id")
        if source_id not in disabled:
            continue
        source["enabled"] = False
        source["required"] = False
        disabled_records.append({
            "id": source_id,
            "name": source.get("name"),
            "state": "disabled-by-policy",
            "reason": disabled[source_id],
            "category": source.get("category"),
        })

    settings = result.setdefault("settings", {})
    output = settings.setdefault("output", {})
    output.update(policy.get("output") or {})
    return result, disabled_records
