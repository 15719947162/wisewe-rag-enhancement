from __future__ import annotations

from datetime import datetime, timezone

from core.db.external_system_configs import _row_to_payload


def test_external_system_config_payload_masks_secret() -> None:
    created_at = datetime(2026, 6, 29, 8, 0, tzinfo=timezone.utc)
    row = (
        "ext_test",
        "t1",
        "admin",
        "https://sso.example.test",
        "client-a",
        "plain-secret",
        "https://app.example.test/sso/callback",
        "https://browser-sso.example.test",
        "/sso",
        "/internal/sso/exchange",
        "/internal/identity/users/{userId}",
        "/internal/identity/delta",
        "active",
        created_at,
        created_at,
        None,
    )
    cols = [
        "id",
        "tenant_id",
        "created_by",
        "sso_base_url",
        "sso_client_id",
        "sso_client_secret",
        "sso_redirect_uri",
        "sso_launch_base_url",
        "sso_launch_path",
        "sso_exchange_path",
        "sso_user_snapshot_path_template",
        "sso_delta_path",
        "status",
        "created_at",
        "updated_at",
        "deleted_at",
    ]

    payload = _row_to_payload(row, cols)

    assert payload["ssoClientSecretMasked"] == "****cret"
    assert payload["ssoBaseUrl"] == "https://sso.example.test"
    assert payload["ssoLaunchBaseUrl"] == "https://browser-sso.example.test"
    assert payload["ssoLaunchPath"] == "/sso"
    assert payload["ssoExchangePath"] == "/internal/sso/exchange"
    assert payload["ssoUserSnapshotPathTemplate"] == "/internal/identity/users/{userId}"
    assert payload["ssoDeltaPath"] == "/internal/identity/delta"
    assert "ssoClientSecret" not in payload
    assert "plain-secret" not in str(payload)

    internal_payload = _row_to_payload(row, cols, include_secret=True)
    assert internal_payload["ssoClientSecret"] == "plain-secret"
