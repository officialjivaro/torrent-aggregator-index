from __future__ import annotations

import pytest

from jtorrent_backend.search_policy import PolicyError, apply_policy, validate_policy_contract


def config() -> dict:
    return {
        "settings": {
            "base_url": "https://index.jtorrent.net",
            "custom_domain": "index.jtorrent.net",
            "frontend_origin": "https://www.jtorrent.net",
            "limits": {"min_total_items": 30000},
        },
        "sources": [
            {
                "id": "ubuntu",
                "name": "Ubuntu",
                "type": "html_torrent_links",
                "enabled": True,
                "required": True,
                "min_items": 1,
                "category": "linux",
                "source_homepage": "https://ubuntu.com/",
                "copyright_status": "open-source",
                "license": "Official release torrents",
            },
            {
                "id": "uncertain",
                "name": "Uncertain collection",
                "type": "rss_feed",
                "enabled": True,
                "required": False,
                "category": "general",
                "source_homepage": "https://example.com/",
                "copyright_status": "unknown",
                "license": "Collection-level authorization unverified",
            },
        ],
    }


def policy() -> dict:
    return {
        "contractVersion": 4,
        "defaultIndexScope": "open-authorized",
        "scopeDefinitions": {
            "open-authorized": {"label": "Authorized / open", "defaultIncluded": True},
            "third-party-unverified": {"label": "Third-party / unverified", "defaultIncluded": True},
        },
        "sourceScopes": {},
        "disabledSources": {"uncertain": "Collection-level authorization is unverified."},
        "output": {
            "legacy_shards": False,
            "group_files": False,
            "v4_only": True,
            "search_v3": {"enabled": False},
            "search_v4": {"enabled": True},
        },
        "governance": {
            "failClosed": True,
            "allowedPublicCopyrightStatuses": ["open-source", "open-data", "public-domain", "creative-commons"],
            "requirePolicyForUnknownStatus": True,
            "minimumHealthySources": 1,
            "minimumItems": 30000,
            "expectedPolicyDisabledSources": 1,
        },
    }


def test_valid_policy_is_applied_fail_closed():
    report = validate_policy_contract(config(), policy())
    assert report["active_source_ids"] == ["ubuntu"]
    effective, disabled = apply_policy(config(), policy())
    assert next(item for item in effective["sources"] if item["id"] == "uncertain")["enabled"] is False
    assert [item["id"] for item in disabled] == ["uncertain"]


def test_unknown_rights_source_must_be_policy_disabled():
    value = policy()
    value["disabledSources"] = {}
    value["governance"]["expectedPolicyDisabledSources"] = 0
    with pytest.raises(PolicyError, match="uncertain authorization"):
        validate_policy_contract(config(), value)


def test_policy_cannot_disable_unknown_id():
    value = policy()
    value["disabledSources"]["missing"] = "Bad ID"
    value["governance"]["expectedPolicyDisabledSources"] = 2
    with pytest.raises(PolicyError, match="unknown source IDs"):
        validate_policy_contract(config(), value)


def test_required_source_cannot_be_disabled():
    value = policy()
    value["disabledSources"]["ubuntu"] = "Should not be disabled"
    value["governance"]["expectedPolicyDisabledSources"] = 2
    with pytest.raises(PolicyError, match="Required source ubuntu"):
        validate_policy_contract(config(), value)


def test_policy_must_explicitly_fail_closed():
    value = policy()
    value["governance"]["failClosed"] = False
    with pytest.raises(PolicyError, match="failClosed"):
        validate_policy_contract(config(), value)


def test_third_party_scope_must_be_included_by_default():
    value = policy()
    value["scopeDefinitions"]["third-party-unverified"]["defaultIncluded"] = False
    with pytest.raises(PolicyError, match="explicit opt-out"):
        validate_policy_contract(config(), value)
