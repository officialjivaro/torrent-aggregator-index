from __future__ import annotations

import copy
from typing import Any


ALLOWED_SOURCE_TYPES = {
    "direct_list",
    "html_torrent_links",
    "rss_feed",
    "internet_archive_advancedsearch",
}
DEFAULT_ALLOWED_PUBLIC_STATUSES = {
    "open-source",
    "open-data",
    "public-domain",
    "creative-commons",
}


class PolicyError(ValueError):
    """Raised when the public-search policy is missing or unsafe."""


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_policy_contract(config: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Validate the source registry and fail-closed public-index policy.

    The complete configured registry stays in ``config/sources.yml``.  Sources
    with uncertain collection-level authorization may remain documented there,
    but they must be explicitly disabled by policy before a public build can run.
    """

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config, dict):
        raise PolicyError("Source configuration must be a mapping.")
    if not isinstance(policy, dict):
        raise PolicyError("Search policy must be a JSON object.")

    if policy.get("contractVersion") != 3:
        errors.append("Search policy contractVersion must be 3.")
    if policy.get("defaultIndexScope") != "open-official-research":
        errors.append("Search policy defaultIndexScope must be 'open-official-research'.")

    governance = policy.get("governance") or {}
    if not isinstance(governance, dict):
        errors.append("Search policy governance must be an object.")
        governance = {}
    if governance.get("failClosed") is not True:
        errors.append("Search policy governance.failClosed must be true.")

    disabled = policy.get("disabledSources") or {}
    if not isinstance(disabled, dict):
        errors.append("Search policy disabledSources must be an object.")
        disabled = {}
    for source_id, reason in disabled.items():
        if not _nonempty_string(source_id) or not _nonempty_string(reason):
            errors.append("Every disabled source must have a non-empty ID and reason.")

    sources = config.get("sources") or []
    if not isinstance(sources, list) or not sources:
        errors.append("Source configuration must contain a non-empty sources list.")
        sources = []

    source_ids: list[str] = []
    source_by_id: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"Source entry {index} must be an object.")
            continue
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            errors.append(f"Source entry {index} has no ID.")
            continue
        if source_id in source_by_id:
            errors.append(f"Duplicate source ID: {source_id}")
            continue
        source_ids.append(source_id)
        source_by_id[source_id] = source

    unknown_disabled = sorted(set(disabled) - set(source_by_id))
    if unknown_disabled:
        errors.append("Policy disables unknown source IDs: " + ", ".join(unknown_disabled))

    allowed_statuses = governance.get("allowedPublicCopyrightStatuses") or sorted(DEFAULT_ALLOWED_PUBLIC_STATUSES)
    if not isinstance(allowed_statuses, list) or not all(_nonempty_string(item) for item in allowed_statuses):
        errors.append("governance.allowedPublicCopyrightStatuses must be a list of strings.")
        allowed_statuses = sorted(DEFAULT_ALLOWED_PUBLIC_STATUSES)
    allowed_status_set = {str(item).strip() for item in allowed_statuses}
    require_unknown_disabled = governance.get("requirePolicyForUnknownStatus") is not False

    required_ids: list[str] = []
    active_ids: list[str] = []
    uncertain_ids: list[str] = []
    for source_id in source_ids:
        source = source_by_id[source_id]
        enabled = source.get("enabled", True) is not False
        required = source.get("required", False) is True
        source_type = str(source.get("type") or "").strip()
        status = str(source.get("copyright_status") or "").strip().lower()

        if source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(f"Source {source_id} has unsupported type {source_type!r}.")
        for field in ("name", "category", "source_homepage", "copyright_status", "license"):
            if not _nonempty_string(source.get(field)):
                errors.append(f"Source {source_id} is missing {field}.")
        homepage = str(source.get("source_homepage") or "")
        if homepage and not homepage.startswith("https://"):
            errors.append(f"Source {source_id} source_homepage must use HTTPS.")
        license_url = str(source.get("license_url") or "")
        if license_url and not license_url.startswith("https://"):
            errors.append(f"Source {source_id} license_url must use HTTPS.")

        if required:
            required_ids.append(source_id)
            if source_id in disabled:
                errors.append(f"Required source {source_id} cannot be disabled by policy.")
            if status not in allowed_status_set:
                errors.append(f"Required source {source_id} has non-public copyright status {status!r}.")
            if _as_int(source.get("min_items"), 0) < 1:
                errors.append(f"Required source {source_id} must define min_items >= 1.")

        if enabled and source_id not in disabled:
            active_ids.append(source_id)
            if status not in allowed_status_set:
                errors.append(
                    f"Enabled public source {source_id} has status {status!r} but is not disabled by policy."
                )
        elif status not in allowed_status_set:
            uncertain_ids.append(source_id)

        if require_unknown_disabled and status in {"", "unknown", "unclear", "unverified"} and source_id not in disabled:
            errors.append(f"Source {source_id} has uncertain authorization and must be explicitly policy-disabled.")

    expected_disabled = _as_int(governance.get("expectedPolicyDisabledSources"), len(disabled))
    if expected_disabled != len(disabled):
        errors.append(
            f"Policy-disabled source count changed: {len(disabled)} != expected {expected_disabled}."
        )

    settings = config.get("settings") or {}
    if not isinstance(settings, dict):
        errors.append("settings must be an object.")
        settings = {}
    if settings.get("base_url") != "https://officialjivaro.github.io/backend-jtorrent":
        errors.append("settings.base_url must be the backend-jtorrent GitHub Pages origin.")
    if settings.get("frontend_origin") != "https://www.jtorrent.net":
        errors.append("settings.frontend_origin must be https://www.jtorrent.net.")

    limits = settings.get("limits") or {}
    minimum_items = _as_int(governance.get("minimumItems"), 30_000)
    if _as_int(limits.get("min_total_items"), 0) < minimum_items:
        errors.append(f"settings.limits.min_total_items must be at least {minimum_items}.")

    output = policy.get("output") or {}
    if not isinstance(output, dict):
        errors.append("Search policy output must be an object.")
        output = {}
    if output.get("legacy_shards") is not False:
        errors.append("Search policy output.legacy_shards must be false.")
    if output.get("group_files") is not False:
        errors.append("Search policy output.group_files must be false.")
    search_v3 = output.get("search_v3") or {}
    if not isinstance(search_v3, dict) or search_v3.get("enabled") is not True:
        errors.append("Search policy output.search_v3.enabled must be true.")

    if not required_ids:
        warnings.append("No required sources are configured.")
    if len(active_ids) < _as_int(governance.get("minimumHealthySources"), 1):
        warnings.append("Configured active-source count is below the live minimum; the generated audit will enforce it.")

    if errors:
        raise PolicyError("\n".join(errors))

    return {
        "contract_version": 3,
        "source_count": len(source_ids),
        "active_source_ids": sorted(active_ids),
        "required_source_ids": sorted(required_ids),
        "disabled_source_ids": sorted(disabled),
        "uncertain_source_ids": sorted(set(uncertain_ids)),
        "allowed_public_copyright_statuses": sorted(allowed_status_set),
        "warnings": warnings,
    }


def apply_policy(config: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a copied source config with the validated public-search policy applied."""

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
