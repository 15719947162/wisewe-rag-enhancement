"""
数据库连接模块

本模块提供 PostgreSQL 数据库连接的统一管理，用于 pgvector 向量存储功能。
支持从环境变量构建连接字符串，并提供连接健康检查。

环境变量优先级（从高到低）：
    1. DATABASE_URL — 完整的数据库连接 URL（如果设置，直接使用）
    2. PGVECTOR_* 系列变量 — 单独配置各个连接参数，自动组装为 URL

环境变量列表：
    - DATABASE_URL: 完整连接 URL，格式 postgresql://user:pass@host:port/db
    - PGVECTOR_USER: 数据库用户名，默认 'postgres'
    - PGVECTOR_PASSWORD: 数据库密码，默认为空
    - PGVECTOR_HOST: 数据库主机地址，默认 'localhost'
    - PGVECTOR_PORT: 数据库端口，默认 '5432'
    - PGVECTOR_DB: 数据库名称，默认 'rag_db'

依赖：
    - psycopg2: PostgreSQL 数据库适配器（需单独安装）

使用示例：
    >>> from core.db.connection import get_db_url, get_db_connection, is_db_available

    # 获取连接 URL
    >>> url = get_db_url()
    >>> print(url)
    'postgresql://postgres:secret@localhost:5432/rag_db'

    # 获取数据库连接
    >>> conn = get_db_connection()
    >>> cursor = conn.cursor()
    >>> cursor.execute("SELECT version();")
    >>> print(cursor.fetchone())
    ('PostgreSQL 15.2 ...',)

    # 检查数据库是否可用
    >>> if is_db_available():
    ...     print("数据库连接正常")
    ... else:
    ...     print("无法连接数据库")

安全说明：
    - 密码等敏感信息应通过 .env 文件配置，避免硬编码
    - 连接字符串中的特殊字符会自动进行 URL 编码
"""

from __future__ import annotations

import os
from urllib.parse import quote_plus


def get_db_url() -> str:
    """
    从环境变量构建 PostgreSQL 连接 URL。

    该函数实现了环境变量的优先级解析机制，支持两种配置方式：

    优先级机制：
        1. 如果设置了 DATABASE_URL 环境变量，直接返回该值
           （适用于生产环境或需要完整控制连接参数的场景）
        2. 否则，从 PGVECTOR_* 系列环境变量组装连接 URL
           （适用于开发环境，便于单独配置各个参数）

    连接字符串格式：
        - 带密码：postgresql://{user}:{password}@{host}:{port}/{db}
        - 无密码：postgresql://{user}@{host}:{port}/{db}

        其中 user 和 password 会被 URL 编码，以处理特殊字符。

    默认值：
        - PGVECTOR_USER: 'postgres'
        - PGVECTOR_PASSWORD: '' (空字符串)
        - PGVECTOR_HOST: 'localhost'
        - PGVECTOR_PORT: '5432'
        - PGVECTOR_DB: 'rag_db'

    Returns:
        str: 完整的 PostgreSQL 连接 URL 字符串

    Examples:
        # 方式一：使用 DATABASE_URL（优先级最高）
        >>> import os
        >>> os.environ['DATABASE_URL'] = 'postgresql://admin:pass@remote:5432/mydb'
        >>> get_db_url()
        'postgresql://admin:pass@remote:5432/mydb'

        # 方式二：使用单独的 PGVECTOR_* 变量
        >>> os.environ.pop('DATABASE_URL', None)  # 清除 DATABASE_URL
        >>> os.environ['PGVECTOR_USER'] = 'postgres'
        >>> os.environ['PGVECTOR_PASSWORD'] = 'my@pass#word'  # 包含特殊字符
        >>> os.environ['PGVECTOR_HOST'] = 'localhost'
        >>> os.environ['PGVECTOR_PORT'] = '5432'
        >>> os.environ['PGVECTOR_DB'] = 'rag_db'
        >>> get_db_url()
        'postgresql://postgres:my%40pass%23word@localhost:5432/rag_db'

        # 方式三：无密码连接
        >>> os.environ.pop('PGVECTOR_PASSWORD', None)
        >>> get_db_url()
        'postgresql://postgres@localhost:5432/rag_db'
    """
    # 优先级 1: 检查是否有完整的 DATABASE_URL 环境变量
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    # 优先级 2: 从单独的环境变量组装连接 URL
    user = os.environ.get("PGVECTOR_USER", "postgres")
    password = os.environ.get("PGVECTOR_PASSWORD", "")
    host = os.environ.get("PGVECTOR_HOST", "localhost")
    port = os.environ.get("PGVECTOR_PORT", "5432")
    db = os.environ.get("PGVECTOR_DB", "rag_db")

    # 构建连接 URL，对用户名和密码进行 URL 编码以处理特殊字符
    if password:
        return f"postgresql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"
    return f"postgresql://{quote_plus(user)}@{host}:{port}/{db}"


def get_db_connection():
    """
    获取 PostgreSQL 数据库连接。

    使用 psycopg2 库建立与 PostgreSQL 数据库的连接。
    连接参数通过 get_db_url() 从环境变量获取。

    连接特性：
        - 客户端编码设置为 UTF-8，确保中文等多字节字符正确处理
        - 自动检测并提示 psycopg2 未安装的情况

    Returns:
        psycopg2.extensions.connection: PostgreSQL 数据库连接对象

    Raises:
        ImportError: psycopg2 未安装时抛出，提示安装命令
        ConnectionError: 数据库连接失败时抛出，包含详细的错误信息

    Examples:
        >>> from core.db.connection import get_db_connection

        # 基本使用
        >>> conn = get_db_connection()
        >>> cursor = conn.cursor()
        >>> cursor.execute("SELECT 1;")
        >>> cursor.fetchone()
        (1,)
        >>> conn.close()

        # 使用上下文管理器（推荐）
        >>> with get_db_connection() as conn:
        ...     with conn.cursor() as cursor:
        ...         cursor.execute("SELECT version();")
        ...         print(cursor.fetchone())

        # 错误处理
        >>> try:
        ...     conn = get_db_connection()
        ... except ConnectionError as e:
        ...     print(f"连接失败: {e}")

    Note:
        使用完毕后应关闭连接，建议使用上下文管理器自动管理连接生命周期。
    """
    # 检查 psycopg2 是否安装
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError(
            "psycopg2 is required for pgvector support. "
            "Install with: pip install psycopg2-binary"
        ) from exc

    # 获取连接 URL 并建立连接
    url = get_db_url()
    try:
        conn = psycopg2.connect(url, client_encoding="UTF8")
        return conn
    except Exception as exc:
        raise ConnectionError(
            f"Cannot connect to PostgreSQL: {exc}\n"
            "请检查 .env 中的 PGVECTOR_* 配置"
        ) from exc


def is_db_available() -> bool:
    """
    检查数据库连接是否可用。

    尝试建立数据库连接来验证配置是否正确以及数据库服务是否可达。
    连接成功后立即关闭，不会保持长连接。

    该函数适用于：
        - 应用启动时的健康检查
        - 功能开关判断（是否启用向量存储功能）
        - 监控和诊断

    Returns:
        bool: True 表示数据库连接正常，False 表示无法连接

    Examples:
        >>> from core.db.connection import is_db_available

        # 启动时检查
        >>> if is_db_available():
        ...     print("✓ 数据库连接正常，启用向量存储功能")
        ... else:
        ...     print("✗ 无法连接数据库，向量存储功能已禁用")

        # 功能开关
        >>> ENABLE_VECTOR_SEARCH = is_db_available()
        >>> if ENABLE_VECTOR_SEARCH:
        ...     # 执行向量检索
        ...     pass

        # 监控脚本
        >>> import time
        >>> while True:
        ...     status = "UP" if is_db_available() else "DOWN"
        ...     print(f"[{time.strftime('%H:%M:%S')}] Database: {status}")
        ...     time.sleep(60)

    Note:
        该函数会捕获所有异常并返回 False，不会抛出异常。
        如需详细的错误信息，请使用 get_db_connection()。
    """
    try:
        conn = get_db_connection()
        conn.close()
        return True
    except Exception:
        return False
