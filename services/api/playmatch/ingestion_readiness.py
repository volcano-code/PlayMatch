"""Safe, read-only readiness checks for optional Steam and IGDB ingestion.

The report contains configuration presence and allow-listed runtime fields only.
It never prints credentials, performs ingestion, or treats configured credentials
as proof that an upstream provider is reachable.
"""

from __future__ import annotations

import ipaddress
import math
from urllib.parse import urlsplit

import httpx

from playmatch.config import Settings

_RUNTIME_FIELDS = {
    "steam": (
        "status",
        "enabled",
        "configured",
        "worker_recent",
        "discovered_games",
        "with_recommendation_metadata",
        "metadata_pending",
        "last_success_at",
        "last_full_success_at",
        "cycle_mode",
        "cycle_in_progress",
        "pages_committed",
        "rows_seen_in_cycle",
        "cursor",
        "last_error",
        "requests_today",
        "request_budget",
    ),
    "metadata": (
        "status",
        "enabled",
        "configured",
        "worker_recent",
        "jobs_by_state",
        "eligible_metadata",
        "automatic_metadata",
        "last_success_at",
        "last_error",
        "reserved_requests_today",
        "request_budget",
        "refresh_seconds",
        "retrieval_candidate_limit",
        "metadata_provider",
        "rawg_configured",
    ),
}


def _secret_present(value: object) -> bool:
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    return isinstance(value, str) and bool(value.strip())


def _component_state(enabled: bool, configured: bool) -> str:
    if not enabled:
        return "disabled"
    if not configured:
        return "missing_credentials"
    return "configured_not_probed"


def build_config_report(settings: Settings) -> dict:
    steam_key_present = _secret_present(settings.steam_web_api_key)
    igdb_id_present = bool(settings.igdb_client_id.strip())
    igdb_secret_present = bool(settings.igdb_client_secret.strip())
    steam_configured = steam_key_present
    rawg_key_present = _secret_present(settings.rawg_api_key)
    metadata_configured = settings.metadata_configured

    return {
        "schema_version": 1,
        "scope": "configuration_only",
        "does_not_verify_connectivity": True,
        "steam": {
            "state": _component_state(settings.steam_sync_enabled, steam_configured),
            "enabled": settings.steam_sync_enabled,
            "configured": steam_configured,
            "credential_presence": {"web_api_key": steam_key_present},
            "page_size": settings.steam_sync_page_size,
            "poll_interval_seconds": settings.steam_sync_interval_seconds,
            "full_sync_interval_seconds": settings.steam_full_sync_seconds,
            "daily_request_budget": settings.steam_sync_daily_requests,
        },
        "metadata": {
            "state": _component_state(settings.metadata_enrichment_enabled, metadata_configured),
            "enabled": settings.metadata_enrichment_enabled,
            "configured": metadata_configured,
            "provider": settings.metadata_provider,
            "credential_presence": {
                "igdb_client_id": igdb_id_present,
                "igdb_client_secret": igdb_secret_present,
                "rawg_api_key": rawg_key_present,
            },
            "rawg_optional_supplement_configured": rawg_key_present,
            "batch_size": settings.metadata_batch_size,
            "daily_request_budget": settings.metadata_daily_requests,
            "refresh_interval_seconds": settings.metadata_refresh_seconds,
        },
        "recommendation_execution": {
            "mode": settings.run_execution_mode,
            "independent_worker_required": settings.run_execution_mode == "external",
            "max_attempts": settings.run_max_attempts,
            "timeout_seconds": settings.run_timeout_seconds,
        },
        "retrieval": {
            "mode": settings.retrieval_mode,
            "candidate_limit": settings.retrieval_candidate_limit,
            "embedding_configured": settings.embedding_enabled,
        },
        "warnings": [
            "Configured credentials are not proof of successful authentication or synchronization.",
            "Steam discovery identities are not recommendation-ready game metadata.",
            "IGDB enrichment only processes identities already present in the Steam directory.",
            "This command does not start workers, call providers, or modify the database.",
        ],
    }


def _validate_api_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("api_base_url_must_be_an_origin")
    if parsed.scheme == "http":
        try:
            loopback = (
                parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("plain_http_is_allowed_only_for_loopback")
    return f"{parsed.scheme}://{parsed.netloc}"


def _allowlisted_payload(kind: str, payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    result = {}
    for key in _RUNTIME_FIELDS[kind]:
        if key not in payload:
            continue
        value = payload[key]
        if key == "jobs_by_state":
            if isinstance(value, dict) and all(
                isinstance(k, str) and type(v) is int and v >= 0 for k, v in value.items()
            ):
                result[key] = value
        elif key in {
            "discovered_games",
            "with_recommendation_metadata",
            "metadata_pending",
            "pages_committed",
            "rows_seen_in_cycle",
            "cursor",
            "requests_today",
            "request_budget",
            "eligible_metadata",
            "automatic_metadata",
            "reserved_requests_today",
            "refresh_seconds",
            "retrieval_candidate_limit",
        }:
            if type(value) is int and value >= 0:
                result[key] = value
        elif key in {"last_success_at", "last_full_success_at"}:
            if type(value) in {int, float} and math.isfinite(value) and value >= 0:
                result[key] = value
        elif key in {"enabled", "configured", "worker_recent", "cycle_in_progress", "rawg_configured"}:
            if type(value) is bool:
                result[key] = value
        elif value is None or isinstance(value, (str, bool)):
            result[key] = value
    return result


def probe_runtime(api_base_url: str, *, transport=None, timeout: float = 3.0) -> dict:
    """Read health and ingestion status endpoints; return only allow-listed fields."""
    base = _validate_api_base_url(api_base_url)
    endpoints = {
        "health": "/health",
        "steam": "/api/v1/catalog/steam/status",
        "metadata": "/api/v1/catalog/enrichment/status",
    }
    result = {}
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        for name, path in endpoints.items():
            try:
                response = client.get(base + path, headers={"Accept": "application/json"})
                if response.status_code != 200:
                    result[name] = {
                        "reachable": False,
                        "reason": f"http_status_{response.status_code}",
                    }
                    continue
                payload = response.json()
                if name == "health":
                    result[name] = {
                        "reachable": True,
                        "status": payload.get("status") if isinstance(payload, dict) else None,
                        "version": payload.get("version") if isinstance(payload, dict) else None,
                    }
                else:
                    safe = _allowlisted_payload(name, payload)
                    result[name] = (
                        {"reachable": True, "data": safe}
                        if safe is not None
                        else {"reachable": False, "reason": "invalid_response"}
                    )
            except (httpx.HTTPError, ValueError):
                result[name] = {"reachable": False, "reason": "request_failed"}
    return {
        "scope": "read_only_runtime_probe",
        "base_origin": base,
        "does_not_verify_upstream_provider": True,
        "endpoints": result,
    }


def requirements_satisfied(
    report: dict,
    *,
    require_steam: bool = False,
    require_metadata: bool = False,
    require_api: bool = False,
    require_steam_full_sync: bool = False,
    require_metadata_success: bool = False,
) -> bool:
    if require_steam and report["steam"]["state"] != "configured_not_probed":
        return False
    if require_metadata and report["metadata"]["state"] != "configured_not_probed":
        return False

    runtime = report.get("runtime", {}).get("endpoints", {})
    if require_api and not all(
        runtime.get(name, {}).get("reachable") is True for name in ("health", "steam", "metadata")
    ):
        return False
    if require_steam_full_sync:
        steam = runtime.get("steam", {}).get("data", {})
        if not steam.get("last_full_success_at") or not steam.get("discovered_games", 0):
            return False
    if require_metadata_success:
        metadata = runtime.get("metadata", {}).get("data", {})
        if not metadata.get("last_success_at") or not metadata.get("automatic_metadata", 0):
            return False
    return True
