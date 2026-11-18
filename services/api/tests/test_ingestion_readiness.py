import json

import httpx
import pytest
from playmatch.config import Settings
from playmatch.ingestion_readiness import (
    build_config_report,
    probe_runtime,
    requirements_satisfied,
)


def test_default_configuration_is_explicitly_disabled_and_redacted():
    settings = Settings(_env_file=None)
    report = build_config_report(settings)
    assert report["steam"]["state"] == "disabled"
    assert report["metadata"]["state"] == "disabled"
    assert report["does_not_verify_connectivity"] is True
    rendered = json.dumps(report)
    assert "steam_web_api_key" not in rendered
    assert "igdb_client_secret" not in rendered


def test_enabled_integrations_without_credentials_are_blocked():
    settings = Settings(
        _env_file=None,
        steam_sync_enabled=True,
        metadata_enrichment_enabled=True,
        metadata_provider="igdb",
    )
    report = build_config_report(settings)
    assert report["steam"]["state"] == "missing_credentials"
    assert report["metadata"]["state"] == "missing_credentials"
    assert report["metadata"]["provider"] == "igdb"
    assert not requirements_satisfied(report, require_steam=True)
    assert not requirements_satisfied(report, require_metadata=True)


def test_complete_credentials_are_reported_as_unprobed_not_healthy():
    settings = Settings(
        _env_file=None,
        steam_sync_enabled=True,
        steam_web_api_key="synthetic-steam-secret",
        metadata_enrichment_enabled=True,
        metadata_provider="igdb",
        igdb_client_id="synthetic-client-id",
        igdb_client_secret="synthetic-client-secret",
        run_execution_mode="external",
    )
    report = build_config_report(settings)
    assert report["steam"]["state"] == "configured_not_probed"
    assert report["metadata"]["state"] == "configured_not_probed"
    assert report["metadata"]["provider"] == "igdb"
    assert report["recommendation_execution"]["independent_worker_required"] is True
    assert requirements_satisfied(report, require_steam=True, require_metadata=True)
    rendered = json.dumps(report)
    for secret in ("synthetic-steam-secret", "synthetic-client-id", "synthetic-client-secret"):
        assert secret not in rendered


def test_runtime_probe_allowlists_fields_and_redacts_provider_payload():
    def handler(request):
        assert request.headers["accept"] == "application/json"
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.8.0", "secret": "no"})
        if request.url.path.endswith("/steam/status"):
            return httpx.Response(
                200,
                json={
                    "status": "up_to_date",
                    "enabled": True,
                    "configured": True,
                    "worker_recent": True,
                    "discovered_games": 500,
                    "last_full_success_at": 1760000000,
                    "private_key": "never-return",
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "enabled": True,
                "configured": True,
                "worker_recent": True,
                "automatic_metadata": 120,
                "eligible_metadata": 130,
                "last_success_at": 1760000001,
                "access_token": "never-return",
            },
        )

    report = probe_runtime(
        "http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
    )
    assert report["endpoints"]["health"]["status"] == "ok"
    steam = report["endpoints"]["steam"]["data"]
    metadata = report["endpoints"]["metadata"]["data"]
    assert steam["discovered_games"] == 500
    assert steam["last_full_success_at"] == 1760000000
    assert metadata["automatic_metadata"] == 120
    rendered = json.dumps(report)
    assert "never-return" not in rendered
    assert "private_key" not in rendered
    assert "access_token" not in rendered


def test_runtime_probe_sanitizes_http_failures():
    def handler(request):
        return httpx.Response(503, text="upstream-secret-and-stacktrace")

    report = probe_runtime(
        "https://playmatch.example",
        transport=httpx.MockTransport(handler),
    )
    assert all(not item["reachable"] for item in report["endpoints"].values())
    assert "upstream-secret-and-stacktrace" not in json.dumps(report)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:8000/path",
        "https://example.com?token=secret",
        "file:///tmp/db",
    ],
)
def test_runtime_probe_rejects_unsafe_base_urls(url):
    with pytest.raises(ValueError):
        probe_runtime(url)


def test_live_acceptance_requirements_need_successful_runtime_evidence():
    settings = Settings(
        _env_file=None,
        steam_sync_enabled=True,
        steam_web_api_key="synthetic-key",
        metadata_enrichment_enabled=True,
        igdb_client_id="synthetic-id",
        igdb_client_secret="synthetic-secret",
    )
    report = build_config_report(settings)
    report["runtime"] = {
        "endpoints": {
            "steam": {
                "reachable": True,
                "data": {"last_full_success_at": None, "discovered_games": 0},
            },
            "metadata": {
                "reachable": True,
                "data": {"last_success_at": None, "automatic_metadata": 0},
            },
        }
    }
    assert not requirements_satisfied(report, require_steam_full_sync=True)
    assert not requirements_satisfied(report, require_metadata_success=True)
    report["runtime"]["endpoints"]["steam"]["data"].update(
        {"last_full_success_at": 1760000000, "discovered_games": 500}
    )
    report["runtime"]["endpoints"]["metadata"]["data"].update(
        {"last_success_at": 1760000001, "automatic_metadata": 120}
    )
    assert requirements_satisfied(report, require_steam_full_sync=True)
    assert requirements_satisfied(report, require_metadata_success=True)



def test_default_steam_rawg_provider_needs_no_igdb_or_rawg_credentials():
    settings = Settings(_env_file=None, metadata_enrichment_enabled=True)
    report = build_config_report(settings)
    assert report["metadata"]["provider"] == "steam_rawg"
    assert report["metadata"]["state"] == "configured_not_probed"
    assert report["metadata"]["configured"] is True
    assert report["metadata"]["rawg_optional_supplement_configured"] is False
