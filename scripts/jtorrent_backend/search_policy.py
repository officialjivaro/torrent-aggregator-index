from __future__ import annotations

import copy
from typing import Any


ALLOWED_SOURCE_TYPES = {
    "direct_list",
    "html_torrent_links",
    "rss_feed",
    "internet_archive_advancedsearch",
    "apibay",
    "yts_meta_search",
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
    with uncertain collection-level authorization must be either explicitly
    disabled or assigned to the labelled third-party scope before a public build runs.
    """

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(config, dict):
        raise PolicyError("Source configuration must be a mapping.")
    if not isinstance(policy, dict):
        raise PolicyError("Search policy must be a JSON object.")

    if policy.get("contractVersion") != 4:
        errors.append("Search policy contractVersion must be 4.")
    if policy.get("defaultIndexScope") != "open-authorized":
        errors.append("Search policy defaultIndexScope must be 'open-authorized'.")

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

    scope_definitions = policy.get("scopeDefinitions") or {}
    if not isinstance(scope_definitions, dict):
        errors.append("Search policy scopeDefinitions must be an object.")
        scope_definitions = {}
    required_scopes = {"open-authorized", "third-party-unverified"}
    if not required_scopes.issubset(scope_definitions):
        errors.append("Search policy must define open-authorized and third-party-unverified scopes.")
    for scope_id, definition in scope_definitions.items():
        if not _nonempty_string(scope_id) or not isinstance(definition, dict):
            errors.append("Every scope definition must have a non-empty ID and object value.")
            continue
        if not _nonempty_string(definition.get("label")):
            errors.append(f"Scope {scope_id} must define a label.")
        if not isinstance(definition.get("defaultIncluded"), bool):
            errors.append(f"Scope {scope_id} must define boolean defaultIncluded.")
    if (scope_definitions.get("open-authorized") or {}).get("defaultIncluded") is not True:
        errors.append("open-authorized must be included by default.")
    if (scope_definitions.get("third-party-unverified") or {}).get("defaultIncluded") is not True:
        errors.append("third-party-unverified must be included by default so clients can offer an explicit opt-out.")

    source_scopes = policy.get("sourceScopes") or {}
    if not isinstance(source_scopes, dict):
        errors.append("Search policy sourceScopes must be an object.")
        source_scopes = {}
    for source_id, scope_id in source_scopes.items():
        if not _nonempty_string(source_id) or not _nonempty_string(scope_id):
            errors.append("Every source scope assignment must have non-empty source and scope IDs.")
        elif scope_id not in scope_definitions:
            errors.append(f"Source {source_id} uses unknown policy scope {scope_id!r}.")

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
    unknown_scoped = sorted(set(source_scopes) - set(source_by_id))
    if unknown_scoped:
        errors.append("Policy assigns scopes to unknown source IDs: " + ", ".join(unknown_scoped))

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
        explicit_scope = source_scopes.get(source_id)
        scope_id = str(explicit_scope or "open-authorized")

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
            if scope_id != "open-authorized":
                errors.append(f"Required source {source_id} must use the open-authorized scope.")
            if _as_int(source.get("min_items"), 0) < 1:
                errors.append(f"Required source {source_id} must define min_items >= 1.")

        if enabled and source_id not in disabled:
            active_ids.append(source_id)
            if status not in allowed_status_set and not explicit_scope:
                errors.append(
                    f"Enabled source {source_id} has status {status!r} but has no explicit third-party scope."
                )
        elif status not in allowed_status_set:
            uncertain_ids.append(source_id)

        if require_unknown_disabled and status in {"", "unknown", "unclear", "unverified"}:
            if source_id not in disabled and explicit_scope != "third-party-unverified":
                errors.append(
                    f"Source {source_id} has uncertain authorization and must be disabled or explicitly assigned "
                    "to third-party-unverified."
                )

    settings = config.get("settings") or {}
    if not isinstance(settings, dict):
        errors.append("settings must be an object.")
        settings = {}
    if settings.get("base_url") != "https://index.jtorrent.net":
        errors.append("settings.base_url must be the index.jtorrent.net index origin.")
    if settings.get("custom_domain") != "index.jtorrent.net":
        errors.append("settings.custom_domain must be index.jtorrent.net.")
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
    search_v4 = output.get("search_v4") or {}
    if not isinstance(search_v4, dict) or search_v4.get("enabled") is not True:
        errors.append("Search policy output.search_v4.enabled must be true.")

    if not required_ids:
        warnings.append("No required sources are configured.")
    if len(active_ids) < _as_int(governance.get("minimumHealthySources"), 1):
        warnings.append("Configured active-source count is below the live minimum; the generated audit will enforce it.")

    if errors:
        raise PolicyError("\n".join(errors))

    return {
        "contract_version": 4,
        "source_count": len(source_ids),
        "active_source_ids": sorted(active_ids),
        "required_source_ids": sorted(required_ids),
        "disabled_source_ids": sorted(disabled),
        "uncertain_source_ids": sorted(set(uncertain_ids)),
        "allowed_public_copyright_statuses": sorted(allowed_status_set),
        "source_scopes": {source_id: source_scopes.get(source_id, "open-authorized") for source_id in source_ids},
        "warnings": warnings,
    }


def apply_policy(config: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a copied source config with the validated public-search policy applied."""

    result = copy.deepcopy(config)
    disabled = policy.get("disabledSources") or {}
    source_scopes = policy.get("sourceScopes") or {}
    disabled_records: list[dict[str, Any]] = []
    for source in result.get("sources", []):
        source_id = source.get("id")
        source["policy_scope"] = source_scopes.get(source_id, "open-authorized")
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
    settings["policy_scopes"] = copy.deepcopy(policy.get("scopeDefinitions") or {})
    settings["default_index_scope"] = policy.get("defaultIndexScope", "open-authorized")
    settings["suppression_path"] = policy.get("suppressionPath", "config/suppressions.yml")
    output = settings.setdefault("output", {})
    output.update(policy.get("output") or {})
    return result, disabled_records
