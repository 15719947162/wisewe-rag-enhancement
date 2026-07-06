"""
【HTTP 服务启动入口 - serve.py】

本文件是后端 HTTP 服务的启动脚本，用于启动 FastAPI 应用。

主要职责：
1. 解析命令行参数（host、port、reload）
2. 自动初始化数据库表结构
3. 启动 Uvicorn ASGI 服务器

使用场景：
- 开发环境：使用 --reload 开启热重载
- 生产环境：指定 host 和 port
- 测试环境：快速启动服务

运行方式：
    # 方式 1: 直接运行（最简单）
    python backend/serve.py

    # 方式 2: 指定参数
    python backend/serve.py --host 0.0.0.0 --port 8000 --reload

    # 方式 3: 使用 uvicorn 命令（推荐开发环境）
    uvicorn backend.app:app --reload

    # 方式 4: 使用 uvicorn 命令（生产环境）
    uvicorn backend.app:app --host 0.0.0.0 --port 8000 --workers 4

知识点 - ASGI 服务器：
- ASGI: 异步服务器网关接口（Asynchronous Server Gateway Interface）
- Uvicorn: 基于 uvloop 和 httptools 的快速 ASGI 服务器
- FastAPI 是 ASGI 应用，需要 ASGI 服务器才能运行
- Uvicorn 比 Gunicorn（WSGI）更适合异步应用

作者：RAG 项目组
"""

from __future__ import annotations

import argparse


def _ensure_db_schema_before_serving() -> None:
    """
    【启动前自动初始化数据库表结构】

    知识点 - 数据库初始化时机：
    - 应该在服务启动时检查并创建表结构
    - 避免首次请求时才发现表不存在
    - 使用迁移脚本（如 Alembic）更专业，但这里简化处理

    为什么需要这个函数？
    1. 确保数据库表结构存在
    2. 如果是新部署，自动创建表
    3. 如果是已有部署，跳过创建

    异常处理：
    - 如果数据库连接失败，只打印警告，不阻止启动
    - 允许在没有数据库配置的情况下启动（某些功能可能不工作）

    实现逻辑：
    1. 导入数据库初始化模块
    2. 调用 ensure_db_schema() 函数
    3. 捕获所有异常并打印警告

    可能的异常：
    - ImportError: 数据库模块未安装
    - ConnectionError: 无法连接数据库
    - 其他数据库错误
    """
    try:
        # 导入数据库初始化函数
        # 放在 try 中避免导入失败导致服务无法启动
        from core.db.init_db import ensure_db_schema

        # 调用初始化函数，确保表结构存在
        # 这个函数会检查并创建所有必要的表
        ensure_db_schema()

    except Exception as exc:
        # 捕获所有异常
        # 打印警告但不退出，允许服务继续启动
        # 场景：数据库配置错误、连接失败等
        print(f"WARN Database schema auto-init skipped: {exc}")


def main() -> None:
    """
    【主函数 - 服务启动入口】

    知识点 - argparse 命令行参数解析：
    - argparse 是 Python 标准库的命令行解析模块
    - 自动生成帮助信息
    - 支持可选参数、必选参数、子命令等

    参数说明：
        --host: 监听的 IP 地址
            - 0.0.0.0: 监听所有网卡（适合生产环境）
            - 127.0.0.1: 只监听本地（适合开发环境）
        --port: 监听的端口
            - 默认 8000（FastAPI 常用端口）
            - 可以改为其他未被占用的端口
        --reload: 热重载开关
            - 开启后，代码修改会自动重启服务
            - 仅用于开发环境，生产环境不应使用

    执行流程：
    1. 解析命令行参数
    2. 初始化数据库表结构
    3. 启动 Uvicorn 服务器

    Uvicorn 参数说明：
    - "backend.app:app": 应用模块路径
        - backend.app: 模块名（backend/app.py）
        - :app: 模块中的变量名（app = create_app()）
    - host: 监听地址
    - port: 监听端口
    - reload: 是否开启热重载（开发模式）
    """
    # 导入 Uvicorn ASGI 服务器
    import uvicorn

    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description="WiseWe RAG HTTP Service"  # 描述信息，显示在帮助中
    )

    # 添加 --host 参数
    parser.add_argument(
        "--host",
        default="0.0.0.0",  # 默认监听所有网卡
        help="监听的 IP 地址，默认 0.0.0.0"
    )

    # 添加 --port 参数
    parser.add_argument(
        "--port",
        default=8000,  # 默认端口
        type=int,      # 参数类型为整数
        help="监听的端口，默认 8000"
    )

    # 添加 --reload 参数
    parser.add_argument(
        "--reload",
        action="store_true",  # 开关型参数，出现即为 True
        help="开启热重载（仅用于开发环境）"
    )

    # 解析命令行参数
    args = parser.parse_args()

    # 启动前初始化数据库表结构
    _ensure_db_schema_before_serving()

    # 启动 Uvicorn 服务器
    # uvicorn.run() 会阻塞，服务持续运行直到手动停止
    uvicorn.run(
        "backend.app:app",      # 应用路径（模块:变量）
        host=args.host,         # 监听地址
        port=args.port,         # 监听端口
        reload=args.reload      # 是否热重载
    )


# ============================================================================
# 程序入口
# ============================================================================
# 当直接运行此文件时（python backend/serve.py），执行 main 函数
# 如果被导入（import backend.serve），不执行 main
if __name__ == "__main__":
    main()
