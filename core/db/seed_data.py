from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from dotenv import load_dotenv

from core.db.connection import get_db_connection
from core.db.init_db import ensure_db_schema
from core.runtime_settings import ENV_ONLY_RUNTIME_SETTING_KEYS, RUNTIME_SETTING_SPECS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

PROFILE_BASE = "base"
PROFILE_INTEGRATION_TEMPLATE = "integration-template"
PROFILE_DEMO = "demo"
PROFILE_ALL = "all"
SUPPORTED_PROFILES = (PROFILE_BASE, PROFILE_INTEGRATION_TEMPLATE, PROFILE_DEMO)

UPDATED_BY_BASE = "seed:base"
UPDATED_BY_INTEGRATION = "seed:integration-template"
UPDATED_BY_DEMO = "seed:demo"

PROCESSING_COST_RATES = {
    "parse": [
        {
            "provider": "*",
            "unit": "page",
            "currency": "CNY",
            "pricePerPage": 0.04,
            "metadata": {"parseBillingTier": "enhanced"},
        },
        {
            "provider": "*",
            "unit": "page",
            "currency": "CNY",
            "pricePerPage": 0.02,
        },
    ],
    "oss": {
        "currency": "CNY",
        "putPer10000": 0.01,
        "getPer10000": 0.01,
        "trafficGb": 0.5,
        "storageGbMonth": 0.12,
        "retentionDays": 15,
    },
}

BASE_RUNTIME_SETTINGS: dict[str, Any] = {
    "KB_TOKEN_COST_CURRENCY": "CNY",
    "KB_PROCESSING_COST_RATES_JSON": json.dumps(
        PROCESSING_COST_RATES,
        ensure_ascii=False,
        separators=(",", ":"),
    ),
    "RAG_RETRIEVAL_SNAPSHOT": True,
    "RAG_LLM_ENABLED": True,
    "LLM_CLEANER_ENABLED": False,
    "LLM_QUALITY_GATE_ENABLED": False,
}

INTEGRATION_TEMPLATE_CONFIG_ID = "ext_seed_ai_base_template"
INTEGRATION_TEMPLATE_SOURCE_ID = "src_ai_base_template"
INTEGRATION_TEMPLATE_APP_ID = "app_seed_integration_template"

DEMO_SOURCE_CONFIG_ID = "ext_seed_demo_identity"
DEMO_SOURCE_ID = "src_demo_identity"
DEMO_TENANT_ID = "tenant_demo"
DEMO_USER_ID = "user_demo_admin"
DEMO_ROLE_ID = "role_demo_super_manager"
DEMO_KB_ID = "demo-textbook-kb"
DEMO_DOCUMENT_ID = "00000000-0000-4000-8000-000000000001"
DEMO_CHUNK_IDS = (
    "00000000-0000-4000-8000-000000000101",
    "00000000-0000-4000-8000-000000000102",
)
DEMO_ENTITY_ID = "00000000-0000-4000-8000-000000000201"
DEMO_FILE_HASH = "d" * 64


@dataclass(frozen=True)
class SeedPlanItem:
    profile: str
    table: str
    key: str
    description: str


def normalize_profiles(profiles: Iterable[str] | None) -> tuple[str, ...]:
    raw_profiles = tuple(profiles or (PROFILE_BASE,))
    if any(profile == PROFILE_ALL for profile in raw_profiles):
        return SUPPORTED_PROFILES

    normalized: list[str] = []
    for profile in raw_profiles:
        value = str(profile or "").strip()
        if value not in SUPPORTED_PROFILES:
            raise ValueError(f"Unsupported seed profile: {value}")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized or (PROFILE_BASE,))


def build_seed_plan(
    profiles: Iterable[str] | None = None,
    *,
    allow_sensitive: bool = False,
) -> list[SeedPlanItem]:
    normalized_profiles = normalize_profiles(profiles)

    plan: list[SeedPlanItem] = []
    if PROFILE_BASE in normalized_profiles:
        plan.extend(
            SeedPlanItem(
                profile=PROFILE_BASE,
                table="console_settings",
                key=key,
                description=f"初始化控制台运行时配置 {key}",
            )
            for key in sorted(BASE_RUNTIME_SETTINGS)
        )
        plan.append(
            SeedPlanItem(
                profile=PROFILE_BASE,
                table="console_settings_versions",
                key="bootstrap",
                description="写入初始化配置版本快照",
            )
        )

    if PROFILE_INTEGRATION_TEMPLATE in normalized_profiles:
        plan.extend(
            [
                SeedPlanItem(
                    profile=PROFILE_INTEGRATION_TEMPLATE,
                    table="kb_external_system_configs",
                    key=_env("SEED_SSO_CONFIG_ID", INTEGRATION_TEMPLATE_CONFIG_ID),
                    description="初始化禁用态 AI 基座 SSO 对接模板",
                ),
                SeedPlanItem(
                    profile=PROFILE_INTEGRATION_TEMPLATE,
                    table="kb_openapi_apps",
                    key=_env("SEED_OPENAPI_APP_ID", INTEGRATION_TEMPLATE_APP_ID),
                    description="初始化禁用态 OpenAPI 联调应用模板",
                ),
            ]
        )

    if PROFILE_DEMO in normalized_profiles:
        plan.extend(
            [
                SeedPlanItem(PROFILE_DEMO, "kb_external_system_configs", DEMO_SOURCE_CONFIG_ID, "初始化演示身份来源"),
                SeedPlanItem(PROFILE_DEMO, "kb_identity_tenants", DEMO_TENANT_ID, "初始化演示租户快照"),
                SeedPlanItem(PROFILE_DEMO, "kb_identity_users", DEMO_USER_ID, "初始化演示管理员快照"),
                SeedPlanItem(PROFILE_DEMO, "kb_identity_roles", DEMO_ROLE_ID, "初始化演示角色快照"),
                SeedPlanItem(PROFILE_DEMO, "kb_identity_user_roles", DEMO_USER_ID, "初始化演示用户角色关系"),
                SeedPlanItem(PROFILE_DEMO, "knowledge_bases", DEMO_KB_ID, "初始化演示知识库"),
                SeedPlanItem(PROFILE_DEMO, "documents", DEMO_DOCUMENT_ID, "初始化演示文档"),
                SeedPlanItem(PROFILE_DEMO, "chunks", DEMO_CHUNK_IDS[0], "初始化演示文本切片"),
                SeedPlanItem(PROFILE_DEMO, "chunks", DEMO_CHUNK_IDS[1], "初始化演示图谱切片"),
                SeedPlanItem(PROFILE_DEMO, "chunk_relations", f"{DEMO_CHUNK_IDS[0]}->{DEMO_CHUNK_IDS[1]}", "初始化演示切片关系"),
                SeedPlanItem(PROFILE_DEMO, "kg_triples", "针灸学-包含-经络腧穴", "初始化演示知识图谱三元组"),
                SeedPlanItem(PROFILE_DEMO, "entities", DEMO_ENTITY_ID, "初始化演示实体"),
                SeedPlanItem(PROFILE_DEMO, "entity_mentions", DEMO_ENTITY_ID, "初始化演示实体提及"),
            ]
        )

    return plan


def seed_database(
    *,
    profiles: Iterable[str] | None = None,
    apply: bool = False,
    overwrite: bool = False,
    allow_sensitive: bool = False,
    conn: Any | None = None,
    ensure_schema: bool = True,
    emit: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    normalized_profiles = normalize_profiles(profiles)
    if apply and PROFILE_INTEGRATION_TEMPLATE in normalized_profiles:
        _validate_sensitive_seed_inputs(allow_sensitive=allow_sensitive)
    plan = build_seed_plan(normalized_profiles, allow_sensitive=allow_sensitive)
    if not apply:
        return _result(
            profiles=normalized_profiles,
            dry_run=True,
            overwrite=overwrite,
            planned=plan,
            changed=[],
        )

    owns_connection = conn is None
    if owns_connection:
        conn = get_db_connection()

    changed: list[SeedPlanItem] = []
    try:
        if ensure_schema:
            ensure_db_schema(conn)
        with conn.cursor() as cur:
            if PROFILE_BASE in normalized_profiles:
                changed.extend(_apply_base_seed(cur, overwrite=overwrite))
            if PROFILE_INTEGRATION_TEMPLATE in normalized_profiles:
                changed.extend(
                    _apply_integration_template_seed(
                        cur,
                        overwrite=overwrite,
                        allow_sensitive=allow_sensitive,
                    )
                )
            if PROFILE_DEMO in normalized_profiles:
                changed.extend(_apply_demo_seed(cur, overwrite=overwrite))
        conn.commit()
    except Exception:
        if hasattr(conn, "rollback"):
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()

    if emit is not None:
        for item in changed:
            emit(f"applied {item.profile}:{item.table}:{item.key}")

    return _result(
        profiles=normalized_profiles,
        dry_run=False,
        overwrite=overwrite,
        planned=plan,
        changed=changed,
    )


def _apply_base_seed(cur: Any, *, overwrite: bool) -> list[SeedPlanItem]:
    changed: list[SeedPlanItem] = []
    for key, value in sorted(BASE_RUNTIME_SETTINGS.items()):
        if key not in RUNTIME_SETTING_SPECS or key in ENV_ONLY_RUNTIME_SETTING_KEYS:
            continue
        changed_key = _upsert_jsonb_setting(
            cur,
            key=key,
            value=value,
            updated_by=UPDATED_BY_BASE,
            overwrite=overwrite,
        )
        if changed_key:
            changed.append(
                SeedPlanItem(
                    profile=PROFILE_BASE,
                    table="console_settings",
                    key=changed_key,
                    description=f"初始化控制台运行时配置 {changed_key}",
                )
            )

    if changed:
        snapshot = _read_console_settings_snapshot(cur)
        version_id = _insert_settings_version(
            cur,
            action="bootstrap",
            snapshot=snapshot,
            changed_keys=[item.key for item in changed],
            created_by=UPDATED_BY_BASE,
        )
        if version_id:
            changed.append(
                SeedPlanItem(
                    profile=PROFILE_BASE,
                    table="console_settings_versions",
                    key=version_id,
                    description="写入初始化配置版本快照",
                )
            )
    return changed


def _apply_integration_template_seed(
    cur: Any,
    *,
    overwrite: bool,
    allow_sensitive: bool,
) -> list[SeedPlanItem]:
    _validate_sensitive_seed_inputs(allow_sensitive=allow_sensitive)
    changed: list[SeedPlanItem] = []
    secret = _env("SEED_SSO_CLIENT_SECRET", "") if allow_sensitive else ""
    config_id = _env("SEED_SSO_CONFIG_ID", INTEGRATION_TEMPLATE_CONFIG_ID)
    source_id = _env("SEED_SSO_SOURCE_ID", INTEGRATION_TEMPLATE_SOURCE_ID)
    source_name = _env("SEED_SSO_SOURCE_NAME", "AI 基座 SSO 模板")
    if _execute_returning_key(
        cur,
        _insert_or_update_sql(
            table="kb_external_system_configs",
            columns=(
                "id",
                "source_id",
                "source_name",
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
            ),
            conflict="id",
            update_columns=(
                "source_name",
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
            ),
            overwrite=overwrite,
            returning="id",
        ),
        (
            config_id,
            source_id,
            source_name,
            _env("SEED_SSO_TENANT_ID", "") or None,
            _env("SEED_SSO_CREATED_BY", "seed"),
            _env("SEED_SSO_BASE_URL", ""),
            _env("SEED_SSO_CLIENT_ID", ""),
            secret,
            _env("SEED_SSO_REDIRECT_URI", ""),
            _env("SEED_SSO_LAUNCH_BASE_URL", ""),
            _env("SEED_SSO_LAUNCH_PATH", "/sso"),
            _env("SEED_SSO_EXCHANGE_PATH", "/ai/system/internal/sso/exchange"),
            _env("SEED_SSO_USER_SNAPSHOT_PATH_TEMPLATE", "/ai/system/internal/identity/snapshot/users/{userId}"),
            _env("SEED_SSO_DELTA_PATH", "/ai/system/internal/identity/snapshot/delta"),
            "disabled",
        ),
    ):
        changed.append(
            SeedPlanItem(
                PROFILE_INTEGRATION_TEMPLATE,
                "kb_external_system_configs",
                config_id,
                "初始化禁用态 AI 基座 SSO 对接模板",
            )
        )

    app_id = _env("SEED_OPENAPI_APP_ID", INTEGRATION_TEMPLATE_APP_ID)
    if _execute_returning_key(
        cur,
        _insert_or_update_sql(
            table="kb_openapi_apps",
            columns=("id", "name", "tenant_id", "owner_user_id", "status", "note"),
            conflict="id",
            update_columns=("name", "tenant_id", "owner_user_id", "status", "note", "updated_at"),
            overwrite=overwrite,
            returning="id",
            updated_at=True,
        ),
        (
            app_id,
            _env("SEED_OPENAPI_APP_NAME", "默认联调应用模板"),
            _env("SEED_OPENAPI_APP_TENANT_ID", "") or None,
            _env("SEED_OPENAPI_APP_OWNER_USER_ID", "") or None,
            "disabled",
            "初始化脚本创建的禁用态 OpenAPI App 模板；正式 API Key 请在控制台生成。",
        ),
    ):
        changed.append(
            SeedPlanItem(
                PROFILE_INTEGRATION_TEMPLATE,
                "kb_openapi_apps",
                app_id,
                "初始化禁用态 OpenAPI 联调应用模板",
            )
        )
    return changed


def _apply_demo_seed(cur: Any, *, overwrite: bool) -> list[SeedPlanItem]:
    changed: list[SeedPlanItem] = []

    demo_ops = [
        (
            "kb_external_system_configs",
            DEMO_SOURCE_CONFIG_ID,
            _insert_or_update_sql(
                table="kb_external_system_configs",
                columns=(
                    "id",
                    "source_id",
                    "source_name",
                    "tenant_id",
                    "created_by",
                    "sso_base_url",
                    "sso_client_id",
                    "sso_client_secret",
                    "sso_redirect_uri",
                    "sso_launch_base_url",
                    "status",
                ),
                conflict="id",
                update_columns=("source_name", "tenant_id", "created_by", "status"),
                overwrite=overwrite,
                returning="id",
            ),
            (
                DEMO_SOURCE_CONFIG_ID,
                DEMO_SOURCE_ID,
                "演示身份源",
                DEMO_TENANT_ID,
                DEMO_USER_ID,
                "",
                "",
                "",
                "",
                "",
                "disabled",
            ),
            "初始化演示身份来源",
        ),
        (
            "kb_identity_tenants",
            DEMO_TENANT_ID,
            _insert_or_update_sql(
                table="kb_identity_tenants",
                columns=("source_id", "tenant_id", "tenant_name", "tenant_code", "tenant_status", "raw_status"),
                conflict="source_id, tenant_id",
                update_columns=("tenant_name", "tenant_code", "tenant_status", "raw_status", "synced_at"),
                overwrite=overwrite,
                returning="tenant_id",
                synced_at=True,
            ),
            (DEMO_SOURCE_ID, DEMO_TENANT_ID, "演示租户", "DEMO", "active", "active"),
            "初始化演示租户快照",
        ),
        (
            "kb_identity_users",
            DEMO_USER_ID,
            _insert_or_update_sql(
                table="kb_identity_users",
                columns=(
                    "source_id",
                    "user_id",
                    "tenant_id",
                    "username",
                    "display_name",
                    "user_status",
                    "raw_status",
                ),
                conflict="source_id, user_id",
                update_columns=("tenant_id", "username", "display_name", "user_status", "raw_status", "synced_at"),
                overwrite=overwrite,
                returning="user_id",
                synced_at=True,
            ),
            (DEMO_SOURCE_ID, DEMO_USER_ID, DEMO_TENANT_ID, "demo-admin", "演示管理员", "active", "active"),
            "初始化演示管理员快照",
        ),
        (
            "kb_identity_roles",
            DEMO_ROLE_ID,
            _insert_or_update_sql(
                table="kb_identity_roles",
                columns=("source_id", "role_id", "tenant_id", "role_code", "role_name", "role_status", "raw_status"),
                conflict="source_id, role_id",
                update_columns=("tenant_id", "role_code", "role_name", "role_status", "raw_status", "synced_at"),
                overwrite=overwrite,
                returning="role_id",
                synced_at=True,
            ),
            (DEMO_SOURCE_ID, DEMO_ROLE_ID, DEMO_TENANT_ID, "superManager", "超级管理员", "active", "active"),
            "初始化演示角色快照",
        ),
        (
            "kb_identity_user_roles",
            DEMO_USER_ID,
            _insert_or_update_sql(
                table="kb_identity_user_roles",
                columns=("source_id", "tenant_id", "user_id", "role_id", "relation_status"),
                conflict="source_id, tenant_id, user_id, role_id",
                update_columns=("relation_status", "synced_at"),
                overwrite=overwrite,
                returning="user_id",
                synced_at=True,
            ),
            (DEMO_SOURCE_ID, DEMO_TENANT_ID, DEMO_USER_ID, DEMO_ROLE_ID, "active"),
            "初始化演示用户角色关系",
        ),
        (
            "knowledge_bases",
            DEMO_KB_ID,
            _insert_or_update_sql(
                table="knowledge_bases",
                columns=(
                    "id",
                    "name",
                    "description",
                    "default_strategy",
                    "tenant_id",
                    "created_by",
                    "owner_user_id",
                    "status",
                ),
                conflict="id",
                update_columns=(
                    "name",
                    "description",
                    "default_strategy",
                    "tenant_id",
                    "created_by",
                    "owner_user_id",
                    "status",
                ),
                overwrite=overwrite,
                returning="id",
            ),
            (
                DEMO_KB_ID,
                "示例教材知识库",
                "由 seed demo 初始化的最小教材知识库，用于本地演示和 UAT。",
                "hierarchical",
                DEMO_TENANT_ID,
                DEMO_USER_ID,
                DEMO_USER_ID,
                "active",
            ),
            "初始化演示知识库",
        ),
        (
            "documents",
            DEMO_DOCUMENT_ID,
            _insert_or_update_sql(
                table="documents",
                columns=(
                    "id",
                    "kb_id",
                    "filename",
                    "file_hash",
                    "file_size_bytes",
                    "chunk_count",
                    "source_storage",
                    "source_path",
                    "parser_provider",
                ),
                conflict="id",
                update_columns=(
                    "filename",
                    "file_size_bytes",
                    "chunk_count",
                    "source_storage",
                    "source_path",
                    "parser_provider",
                    "updated_at",
                ),
                overwrite=overwrite,
                returning="id",
            ),
            (
                DEMO_DOCUMENT_ID,
                DEMO_KB_ID,
                "示例教材片段.md",
                DEMO_FILE_HASH,
                2048,
                2,
                "seed",
                "seed://demo/textbook-fragment",
                "seed_demo",
            ),
            "初始化演示文档",
        ),
    ]

    for table, key, sql, params, description in demo_ops:
        if _execute_returning_key(cur, sql, params):
            changed.append(SeedPlanItem(PROFILE_DEMO, table, key, description))

    changed.extend(_apply_demo_chunks(cur, overwrite=overwrite))
    return changed


def _apply_demo_chunks(cur: Any, *, overwrite: bool) -> list[SeedPlanItem]:
    changed: list[SeedPlanItem] = []
    chunk_rows = [
        (
            DEMO_CHUNK_IDS[0],
            DEMO_KB_ID,
            DEMO_DOCUMENT_ID,
            "针灸学是研究经络、腧穴、刺灸方法及临床应用规律的学科。",
            "示例教材片段.md",
            1,
            0,
            "hierarchical",
            "针灸学概念",
            42,
            "child",
        ),
        (
            DEMO_CHUNK_IDS[1],
            DEMO_KB_ID,
            DEMO_DOCUMENT_ID,
            "经络腧穴理论是针灸辨证施治和取穴配伍的重要基础。",
            "示例教材片段.md",
            1,
            1,
            "hierarchical",
            "经络腧穴基础",
            36,
            "child",
        ),
    ]
    chunk_sql = _insert_or_update_sql(
        table="chunks",
        columns=(
            "id",
            "kb_id",
            "document_id",
            "content",
            "source",
            "page",
            "chunk_index",
            "strategy",
            "title",
            "char_count",
            "layer",
            "search_text",
            "search_vector",
        ),
        conflict="id",
        update_columns=(
            "content",
            "source",
            "page",
            "chunk_index",
            "strategy",
            "title",
            "char_count",
            "layer",
            "search_text",
            "search_vector",
        ),
        overwrite=overwrite,
        returning="id",
        value_sql_overrides={"search_vector": "to_tsvector('simple', %s)"},
    )
    for row in chunk_rows:
        content = row[3]
        params = (*row, content, content)
        if _execute_returning_key(cur, chunk_sql, params):
            changed.append(SeedPlanItem(PROFILE_DEMO, "chunks", row[0], "初始化演示切片"))

    if _execute_returning_key(
        cur,
        _insert_or_update_sql(
            table="chunk_relations",
            columns=("kb_id", "src_id", "dst_id", "rel_type", "weight", "source", "evidence"),
            conflict="kb_id, src_id, dst_id, rel_type",
            update_columns=("weight", "source", "evidence"),
            overwrite=overwrite,
            returning="id",
        ),
        (
            DEMO_KB_ID,
            DEMO_CHUNK_IDS[0],
            DEMO_CHUNK_IDS[1],
            "related_to",
            0.85,
            "seed_demo",
            "针灸学概念与经络腧穴基础存在上下文关联。",
        ),
    ):
        changed.append(SeedPlanItem(PROFILE_DEMO, "chunk_relations", DEMO_CHUNK_IDS[0], "初始化演示切片关系"))

    if _upsert_demo_kg_triple(cur, overwrite=overwrite):
        changed.append(SeedPlanItem(PROFILE_DEMO, "kg_triples", "针灸学-包含-经络腧穴", "初始化演示三元组"))

    if _execute_returning_key(
        cur,
        _insert_or_update_sql(
            table="entities",
            columns=("id", "kb_id", "name", "aliases", "type", "definition"),
            conflict="id",
            update_columns=("name", "aliases", "type", "definition", "updated_at"),
            overwrite=overwrite,
            returning="id",
        ),
        (
            DEMO_ENTITY_ID,
            DEMO_KB_ID,
            "经络腧穴",
            ["经络", "腧穴"],
            "concept",
            "针灸学中的基础理论与定位体系。",
        ),
    ):
        changed.append(SeedPlanItem(PROFILE_DEMO, "entities", DEMO_ENTITY_ID, "初始化演示实体"))

    if _execute_returning_key(
        cur,
        _insert_or_update_sql(
            table="entity_mentions",
            columns=("entity_id", "chunk_id", "kb_id"),
            conflict="entity_id, chunk_id",
            update_columns=("kb_id",),
            overwrite=overwrite,
            returning="entity_id",
        ),
        (DEMO_ENTITY_ID, DEMO_CHUNK_IDS[1], DEMO_KB_ID),
    ):
        changed.append(SeedPlanItem(PROFILE_DEMO, "entity_mentions", DEMO_ENTITY_ID, "初始化演示实体提及"))

    return changed


def _upsert_demo_kg_triple(cur: Any, *, overwrite: bool) -> bool:
    cur.execute(
        """
        SELECT id
        FROM kg_triples
        WHERE kb_id = %s AND s = %s AND p = %s AND o = %s
        LIMIT 1
        """,
        (DEMO_KB_ID, "针灸学", "包含基础理论", "经络腧穴"),
    )
    row = cur.fetchone()
    if row and not overwrite:
        return False
    if row:
        cur.execute(
            """
            UPDATE kg_triples
            SET confidence = %s,
                source_chunk = %s
            WHERE id = %s
            RETURNING id
            """,
            (0.9, DEMO_CHUNK_IDS[1], row[0]),
        )
    else:
        cur.execute(
            """
            INSERT INTO kg_triples(kb_id, s, p, o, confidence, source_chunk)
            VALUES(%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (DEMO_KB_ID, "针灸学", "包含基础理论", "经络腧穴", 0.9, DEMO_CHUNK_IDS[1]),
        )
    return cur.fetchone() is not None


def _upsert_jsonb_setting(cur: Any, *, key: str, value: Any, updated_by: str, overwrite: bool) -> str | None:
    if overwrite:
        sql = """
        INSERT INTO console_settings(key, value, updated_by)
        VALUES(%s, %s::jsonb, %s)
        ON CONFLICT(key) DO UPDATE
        SET value = EXCLUDED.value,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        WHERE console_settings.value IS DISTINCT FROM EXCLUDED.value
           OR console_settings.updated_by IS DISTINCT FROM EXCLUDED.updated_by
        RETURNING key
        """
    else:
        sql = """
        INSERT INTO console_settings(key, value, updated_by)
        VALUES(%s, %s::jsonb, %s)
        ON CONFLICT(key) DO NOTHING
        RETURNING key
        """
    return _execute_returning_key(cur, sql, (key, json.dumps(value, ensure_ascii=False), updated_by))


def _read_console_settings_snapshot(cur: Any) -> dict[str, Any]:
    cur.execute("SELECT key, value FROM console_settings ORDER BY key")
    rows = cur.fetchall()
    return {
        str(key): value
        for key, value in rows
        if str(key) in RUNTIME_SETTING_SPECS and str(key) not in ENV_ONLY_RUNTIME_SETTING_KEYS
    }


def _insert_settings_version(
    cur: Any,
    *,
    action: str,
    snapshot: dict[str, Any],
    changed_keys: list[str],
    created_by: str,
) -> str | None:
    return _execute_returning_key(
        cur,
        """
        INSERT INTO console_settings_versions(action, settings_snapshot, changed_keys, created_by)
        VALUES(%s, %s::jsonb, %s::jsonb, %s)
        RETURNING id
        """,
        (
            action,
            json.dumps(snapshot, ensure_ascii=False),
            json.dumps(sorted(set(changed_keys)), ensure_ascii=False),
            created_by,
        ),
    )


def _insert_or_update_sql(
    *,
    table: str,
    columns: tuple[str, ...],
    conflict: str,
    update_columns: tuple[str, ...],
    overwrite: bool,
    returning: str,
    updated_at: bool = False,
    synced_at: bool = False,
    value_sql_overrides: dict[str, str] | None = None,
) -> str:
    value_sql_overrides = value_sql_overrides or {}
    value_placeholders = [value_sql_overrides.get(column, "%s") for column in columns]
    if overwrite:
        assignments: list[str] = []
        for column in update_columns:
            if column == "updated_at":
                assignments.append("updated_at = NOW()")
            elif column == "synced_at":
                assignments.append("synced_at = NOW()")
            elif column in value_sql_overrides:
                assignments.append(f"{column} = EXCLUDED.{column}")
            else:
                assignments.append(f"{column} = EXCLUDED.{column}")
        if updated_at and "updated_at" not in update_columns:
            assignments.append("updated_at = NOW()")
        if synced_at and "synced_at" not in update_columns:
            assignments.append("synced_at = NOW()")
        conflict_action = "DO UPDATE SET " + ", ".join(assignments)
    else:
        conflict_action = "DO NOTHING"
    return f"""
        INSERT INTO {table}({", ".join(columns)})
        VALUES({", ".join(value_placeholders)})
        ON CONFLICT({conflict}) {conflict_action}
        RETURNING {returning}
    """


def _execute_returning_key(cur: Any, sql: str, params: tuple[Any, ...]) -> str | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        return None
    return str(row[0])


def _validate_sensitive_seed_inputs(*, allow_sensitive: bool) -> None:
    if not allow_sensitive and _env("SEED_SSO_CLIENT_SECRET", ""):
        raise ValueError(
            "SEED_SSO_CLIENT_SECRET is set. Re-run with --allow-sensitive if this secret should be written."
        )


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _result(
    *,
    profiles: tuple[str, ...],
    dry_run: bool,
    overwrite: bool,
    planned: list[SeedPlanItem],
    changed: list[SeedPlanItem],
) -> dict[str, Any]:
    return {
        "profiles": list(profiles),
        "dryRun": dry_run,
        "overwrite": overwrite,
        "plannedCount": len(planned),
        "changedCount": len(changed),
        "planned": [item.__dict__ for item in planned],
        "changed": [item.__dict__ for item in changed],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed WiseWe RAG initial business data.")
    parser.add_argument(
        "--profile",
        action="append",
        choices=(*SUPPORTED_PROFILES, PROFILE_ALL),
        help="Seed profile to run. May be repeated. Defaults to base.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes. Without this flag only prints a dry-run.")
    parser.add_argument("--overwrite", action="store_true", help="Update existing seed rows instead of insert-only.")
    parser.add_argument(
        "--allow-sensitive",
        action="store_true",
        help="Allow writing sensitive seed values such as SEED_SSO_CLIENT_SECRET.",
    )
    args = parser.parse_args(argv)

    try:
        result = seed_database(
            profiles=args.profile,
            apply=args.apply,
            overwrite=args.overwrite,
            allow_sensitive=args.allow_sensitive,
            emit=print,
        )
    except Exception as exc:
        print(f"FAILED Seed failed: {exc}", file=sys.stderr)
        sys.exit(1)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode} profiles={','.join(result['profiles'])} planned={result['plannedCount']} changed={result['changedCount']}")
    if not args.apply:
        print("Use --apply to write these seed rows.")
        for item in result["planned"]:
            print(f"- {item['profile']} {item['table']} {item['key']}: {item['description']}")


if __name__ == "__main__":
    main()
