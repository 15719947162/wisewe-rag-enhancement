from __future__ import annotations

from unittest.mock import patch

import pytest

from core.runtime_settings import resolve_runtime_setting, save_runtime_overrides


def test_save_runtime_overrides_raises_when_db_is_unavailable() -> None:
    with patch("core.runtime_settings.get_db_connection", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError, match="Database unavailable while saving console settings"):
            save_runtime_overrides({"302AI_API_BASE": "https://api.example.com"})


def test_resolve_runtime_setting_keeps_document_mind_concurrency_independent_from_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET", raising=False)
    monkeypatch.delenv("OSS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OSS_ACCESS_KEY_SECRET", raising=False)
    monkeypatch.setenv(
        "ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL",
        "ak-1:sk-1,ak-2:sk-2,ak-3:sk-3,ak-4:sk-4,ak-5:sk-5,ak-6:sk-6",
    )

    value, source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_SHARDING_MAX_CONCURRENCY", overrides={})

    assert value == 4
    assert source == "code"


def test_resolve_runtime_setting_exposes_document_mind_probe_concurrency_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY", raising=False)

    value, source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_KEY_PROBE_CONCURRENCY", overrides={})

    assert value == 1
    assert source == "code"


def test_resolve_runtime_setting_disables_document_mind_managed_llm_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT", raising=False)

    value, source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_LLM_ENHANCEMENT", overrides={})

    assert value is False
    assert source == "code"


def test_resolve_runtime_setting_exposes_document_mind_hedge_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS", raising=False)

    enabled, enabled_source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_HEDGED_SHARD_ENABLED", overrides={})
    after_seconds, after_source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_HEDGE_AFTER_SECONDS", overrides={})
    max_extra, max_extra_source = resolve_runtime_setting("ALIYUN_DOCUMENT_MIND_HEDGE_MAX_EXTRA_ATTEMPTS", overrides={})

    assert enabled is False
    assert enabled_source == "code"
    assert after_seconds == 90.0
    assert after_source == "code"
    assert max_extra == 1
    assert max_extra_source == "code"


def test_resolve_runtime_setting_exposes_document_mind_weighted_sharding_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST", raising=False)

    weighted_enabled, weighted_source = resolve_runtime_setting(
        "ALIYUN_DOCUMENT_MIND_WEIGHTED_SHARDING_ENABLED",
        overrides={},
    )
    heavy_first, heavy_source = resolve_runtime_setting(
        "ALIYUN_DOCUMENT_MIND_HEAVY_SHARD_FIRST",
        overrides={},
    )

    assert weighted_enabled is False
    assert weighted_source == "code"
    assert heavy_first is False
    assert heavy_source == "code"


def test_resolve_runtime_setting_exposes_document_mind_shard_save_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE", raising=False)
    monkeypatch.delenv("ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE", raising=False)

    garbage, garbage_source = resolve_runtime_setting(
        "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_GARBAGE",
        overrides={},
    )
    deflate, deflate_source = resolve_runtime_setting(
        "ALIYUN_DOCUMENT_MIND_SHARD_SAVE_DEFLATE",
        overrides={},
    )

    assert garbage == 1
    assert garbage_source == "code"
    assert deflate is True
    assert deflate_source == "code"


def test_resolve_runtime_setting_exposes_ingestion_ready_mode_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INGESTION_READY_MODE", raising=False)

    value, source = resolve_runtime_setting("INGESTION_READY_MODE", overrides={})

    assert value == "full"
    assert source == "code"


def test_resolve_runtime_setting_exposes_mineru_official_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in [
        "MINERU_OFFICIAL_API_BASE",
        "MINERU_OFFICIAL_API_TOKEN",
        "MINERU_OFFICIAL_MODEL_VERSION",
        "MINERU_OFFICIAL_SHARDING_ENABLED",
        "MINERU_OFFICIAL_SHARDING_MIN_FILE_MB",
        "MINERU_OFFICIAL_SHARDING_MIN_PAGES",
        "MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD",
        "MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY",
    ]:
        monkeypatch.delenv(key, raising=False)

    assert resolve_runtime_setting("MINERU_OFFICIAL_API_BASE", overrides={}) == ("https://mineru.net", "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_API_TOKEN", overrides={}) == ("", "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_MODEL_VERSION", overrides={}) == ("vlm", "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_SHARDING_ENABLED", overrides={}) == (True, "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_SHARDING_MIN_FILE_MB", overrides={}) == (180.0, "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_SHARDING_MIN_PAGES", overrides={}) == (201, "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_SHARDING_PAGES_PER_SHARD", overrides={}) == (180, "code")
    assert resolve_runtime_setting("MINERU_OFFICIAL_SHARDING_MAX_CONCURRENCY", overrides={}) == (2, "code")
