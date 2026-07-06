"""
数据库初始化模块

本模块负责 PostgreSQL 数据库的 schema 初始化，包括：
- 创建 pgvector 扩展（用于向量相似度搜索）
- 创建知识库、文档、切片等核心业务表
- 创建用户认证、权限、审计等系统表
- 创建必要的索引以优化查询性能

初始化流程：
1. 检查 schema 是否已初始化（通过全局标志 _SCHEMA_READY）
2. 获取数据库连接（如未提供）
3. 按顺序执行 INIT_SQLS 中的 SQL 语句
4. 设置 schema 就绪标志
5. 关闭连接（如果是本模块创建的连接）

线程安全：
- 使用全局锁 _SCHEMA_LOCK 确保多线程环境下只初始化一次
- 双重检查锁定模式避免不必要的锁竞争

SQL 执行顺序（详见 schema.py）：
1. 扩展安装：pgvector
2. 核心业务表：knowledge_bases, documents, chunks, chunk_drafts
3. 身份认证表：kb_identity_*, kb_auth_*, kb_sso_*
4. 审计日志表：kb_rag_query_logs, kb_llm_call_logs, kb_audit_logs
5. API 密钥表：kb_api_keys, kb_openapi_apps
6. 知识图谱表：kg_triples, entities, entity_mentions
7. 索引创建：为各表添加性能优化索引

使用方式：
    # 方式一：在代码中调用（推荐）
    from core.db.init_db import ensure_db_schema
    ensure_db_schema()

    # 方式二：命令行执行
    python -m core.db.init_db
"""
from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Callable

from dotenv import load_dotenv

from core.db.connection import get_db_connection
from core.db.schema import INIT_SQLS

# 加载项目根目录下的 .env 环境变量文件
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# SQL 执行描述列表
# 与 schema.py 中的 INIT_SQLS 一一对应，用于在初始化过程中输出进度信息
# 每条描述对应一条 SQL 语句的执行动作
_DESCRIPTIONS = [
    "Creating pgvector extension...",
    "Creating knowledge_bases table...",
    "Ensuring knowledge_bases default_strategy column...",
    "Ensuring knowledge_bases governance columns...",
    "Creating knowledge_bases tenant/owner index...",
    "Creating knowledge_bases status index...",
    "Creating documents table...",
    "Ensuring documents source_storage column...",
    "Ensuring documents source_path column...",
    "Ensuring documents source_url column...",
    "Ensuring documents parser_provider column...",
    "Creating console_settings table...",
    "Creating kb_identity_tenants table...",
    "Creating kb_identity_users table...",
    "Creating kb_identity_roles table...",
    "Creating kb_identity_user_roles table...",
    "Creating kb_identity_sync_runs table...",
    "Ensuring kb_identity_sync_runs HTTP metadata columns...",
    "Creating kb_identity_users tenant index...",
    "Creating kb_identity_roles tenant/code index...",
    "Creating kb_identity_user_roles user index...",
    "Creating kb_auth_sessions table...",
    "Creating kb_auth_sessions user index...",
    "Creating kb_sso_used_credentials table...",
    "Creating kb_sso_used_credentials expires index...",
    "Creating kb_rag_query_logs table...",
    "Creating kb_rag_query_logs scope index...",
    "Creating kb_rag_query_logs request index...",
    "Creating kb_llm_call_logs table...",
    "Creating kb_llm_call_logs scope index...",
    "Creating kb_llm_call_logs request index...",
    "Creating kb_token_usage_hourly table...",
    "Creating kb_token_usage_hourly scope index...",
    "Creating kb_audit_logs table...",
    "Creating kb_audit_logs scope index...",
    "Creating kb_audit_logs resource index...",
    "Creating kb_audit_logs request index...",
    "Creating kb_api_keys table...",
    "Ensuring kb_api_keys strong validation columns...",
    "Creating kb_openapi_apps table...",
    "Creating kb_openapi_apps tenant index...",
    "Creating kb_api_keys tenant index...",
    "Creating kb_api_keys hash index...",
    "Creating kb_api_key_nonces table...",
    "Creating kb_api_key_nonces expires index...",
    "Creating kb_api_key_usage_windows table...",
    "Creating kb_api_key_usage_windows updated index...",
    "Creating chunk_drafts table...",
    "Ensuring chunk_drafts related_ids column...",
    "Ensuring chunk_drafts enhanced_text column...",
    "Ensuring chunk_drafts extracted_entities column...",
    "Ensuring chunk_drafts extracted_triples column...",
    "Ensuring chunk_drafts relations column...",
    "Ensuring chunk_drafts image_path column...",
    "Creating chunks table...",
    "Ensuring chunks image_path column...",
    "Ensuring chunks search_text column...",
    "Ensuring chunks search_vector column...",
    "Creating chunk_relations table...",
    "Creating HNSW index on chunks.embedding...",
    "Creating kb_id index on chunks...",
    "Creating chunks search_vector index...",
    "Creating chunk_drafts task index...",
    "Creating chunk_drafts kb index...",
    "Creating chunk_drafts expires index...",
    "Creating chunk_relations src index...",
    "Creating chunk_relations dst index...",
    "Creating chunk_relations kb/type index...",
    "Creating kg_triples table...",
    "Creating kg_triples s index...",
    "Creating kg_triples o index...",
    "Creating kg_triples chunk index...",
    "Creating entities table...",
    "Creating entities kb/name index...",
    "Creating entities kb/type index...",
    "Creating entity_mentions table...",
    "Creating entity_mentions chunk index...",
]

# 全局标志：记录数据库 schema 是否已初始化
# True 表示已初始化，False 表示未初始化
# 在多进程/多线程环境下，每个进程/线程维护自己的标志
_SCHEMA_READY = False

# 全局线程锁：确保 schema 初始化的线程安全
# 使用双重检查锁定模式：
# 1. 先检查 _SCHEMA_READY（无锁快速路径）
# 2. 如果未就绪，获取锁后再检查（防止竞态条件）
# 3. 执行初始化后设置标志，后续调用直接返回
_SCHEMA_LOCK = Lock()


def _run_init_sqls(conn, emit: Callable[[str], None] | None = None) -> None:
    """
    执行所有数据库初始化 SQL 语句

    内部函数，按顺序执行 INIT_SQLS 中的所有 SQL 语句。
    执行顺序严格按照 schema 设计的依赖关系，确保：
    - 先创建扩展（pgvector）再创建使用扩展的表
    - 先创建表再添加索引
    - 先创建主表再创建关联表

    参数：
        conn: 数据库连接对象（psycopg2 connection）
        emit: 可选的日志输出函数，用于打印初始化进度。
              如果为 None，则静默执行。示例：print

    返回：
        None

    异常：
        如果 SQL 执行失败，会抛出数据库异常，由调用者处理

    执行流程：
        for each (description, sql) in zip(_DESCRIPTIONS, INIT_SQLS):
            1. 如果提供了 emit 函数，输出描述信息
            2. 执行对应的 SQL 语句
            3. 继续下一条（不自动提交，由外层控制）
    """
    with conn.cursor() as cur:
        for desc, sql in zip(_DESCRIPTIONS, INIT_SQLS):
            if emit is not None:
                emit(desc)
            cur.execute(sql)


def ensure_db_schema(conn=None) -> bool:
    """
    确保数据库 schema 已初始化

    这是数据库初始化的主要入口函数。在应用启动时调用此函数，
    确保所有必要的表、索引、扩展都已创建。如果 schema 已存在，
    则跳过初始化（幂等操作）。

    参数：
        conn: 可选的数据库连接对象。如果为 None，函数会自动创建连接。

    返回：
        bool: True 表示本次调用执行了初始化 SQL，
              False 表示 schema 已就绪，跳过了初始化

    线程安全：
        使用双重检查锁定模式确保多线程环境下只初始化一次：
        1. 第一次检查 _SCHEMA_READY（无锁，快速返回）
        2. 获取 _SCHEMA_LOCK
        3. 第二次检查 _SCHEMA_READY（防止竞态条件）
        4. 执行初始化
        5. 设置 _SCHEMA_READY = True

    使用示例：
        # 应用启动时调用
        from core.db.init_db import ensure_db_schema
        ensure_db_schema()

        # 或者传入已有连接（适用于事务场景）
        conn = get_db_connection()
        ensure_db_schema(conn)
        # 注意：如果传入连接，调用者需要负责关闭连接

    连接管理：
        - 如果 conn 为 None：函数创建连接，设置 autocommit=True，
          执行完毕后自动关闭连接
        - 如果传入 conn：调用者负责连接的生命周期管理

    注意事项：
        - autocommit=True 是必需的，因为 CREATE EXTENSION 等语句
          不能在事务块中执行
        - 如果初始化失败，异常会向上传播，_SCHEMA_READY 保持 False，
          允许后续重试
    """
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return False

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return False

        owns_connection = conn is None
        if owns_connection:
            conn = get_db_connection()
            conn.autocommit = True

        try:
            _run_init_sqls(conn)
            _SCHEMA_READY = True
            return True
        finally:
            if owns_connection:
                conn.close()


def main() -> None:
    """
    命令行入口函数

    用于通过命令行执行数据库初始化。提供友好的进度输出和错误处理。

    使用方式：
        # 作为模块运行
        python -m core.db.init_db

        # 或者直接运行此文件
        python core/db/init_db.py

    执行流程：
        1. 尝试连接数据库
           - 失败：输出错误信息，退出码 1
        2. 设置 autocommit=True（CREATE EXTENSION 需要）
        3. 执行所有初始化 SQL，输出每步的进度信息
        4. 成功：输出 "OK Database initialized successfully"
        5. 失败：输出错误信息，退出码 1
        6. 无论成功失败，都会关闭数据库连接

    返回值：
        无（通过 sys.exit() 设置进程退出码）

    退出码：
        0: 初始化成功
        1: 连接失败或初始化失败
    """
    try:
        conn = get_db_connection()
        conn.autocommit = True
    except Exception as exc:
        print(f"FAILED Connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        _run_init_sqls(conn, emit=print)
        print("OK Database initialized successfully")
    except Exception as exc:
        print(f"FAILED Initialization failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


# 当作为脚本直接运行时，执行 main 函数
# 用法：python -m core.db.init_db 或 python core/db/init_db.py
if __name__ == "__main__":
    main()
