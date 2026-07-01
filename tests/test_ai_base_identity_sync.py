from __future__ import annotations

from scripts.sync_ai_base_identity import (
    REQUIRED_SOURCE_TABLES,
    mask_email,
    mask_mobile,
    normalize_tenant_status,
    normalize_user_or_role_status,
)


def test_masks_mobile_before_snapshot_storage() -> None:
    assert mask_mobile("13812345678") == "138****5678"
    assert mask_mobile("abc") == "***"
    assert mask_mobile("") is None


def test_masks_email_before_snapshot_storage() -> None:
    assert mask_email("zhangsan@example.com") == "z***@example.com"
    assert mask_email("not-email") == "***"
    assert mask_email("") is None


def test_status_normalization_matches_ai_base_tables() -> None:
    assert normalize_tenant_status(1) == "active"
    assert normalize_tenant_status(0) == "disabled"
    assert normalize_user_or_role_status(0) == "active"
    assert normalize_user_or_role_status(1) == "disabled"


def test_sync_scope_only_uses_allowed_identity_tables() -> None:
    assert REQUIRED_SOURCE_TABLES == ("system_tenant", "sys_user", "sys_role", "sys_user_role")
