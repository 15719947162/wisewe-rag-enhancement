"""
【FastAPI 应用入口模块 - app.py】

本文件是整个后端服务的核心入口，负责创建和配置 FastAPI 应用。

主要职责：
1. 创建 FastAPI 应用实例
2. 配置 CORS（跨域资源共享）
3. 注册所有路由模块
4. 配置异常处理
5. 设置静态文件服务
6. 处理应用生命周期（启动/关闭）

技术栈：
- FastAPI: 现代、快速的 Web 框架（基于 Starlette 和 Pydantic）
- CORS: 解决前后端分离时的跨域问题
- ORJSON: 高性能 JSON 序列化库（比标准 json 快 3-5 倍）

特殊处理：
- Starlette 1.0 兼容性补丁（处理 on_startup/on_shutdown）
- OpenAPI V1 接口的特殊错误处理

作者：RAG 项目组
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from contextlib import asynccontextmanager
from pathlib import Path

# 导入 Starlette 路由模块（FastAPI 基于 Starlette）
import starlette.routing as _starlette_routing

# ============================================================================
# Starlette 1.0 兼容性补丁
# ============================================================================
# 知识点 - 版本兼容性问题：
# - Starlette 1.0 移除了 Router.__init__ 的 on_startup/on_shutdown 参数
# - 但 FastAPI 0.115 仍然会传递这些参数并读取它们作为属性
# - 这会导致 TypeError: Router.__init__() got unexpected keyword arguments
# - 解决方案：手动打补丁，让 Router.__init__ 忽略这些参数

# Patch 1: 修改 Router.__init__ 方法，让它忽略 on_startup/on_shutdown 参数
if "on_startup" not in inspect.signature(_starlette_routing.Router.__init__).parameters:
    # 检查 Router.__init__ 是否缺少 on_startup 参数
    # 如果缺少，说明是 Starlette 1.0+，需要打补丁

    # 保存原始的 __init__ 方法
    _orig_router_init = _starlette_routing.Router.__init__

    # 定义兼容版本的 __init__ 方法
    def _compat_router_init(self, *args, on_startup=None, on_shutdown=None, lifespan=None, **kwargs):
        """
        兼容版本的 Router.__init__

        这个方法会接收 on_startup/on_shutdown 参数，但不传递给父类。
        只传递 lifespan 参数（Starlette 1.0+ 的新生命周期管理方式）。

        参数说明：
            on_startup: 旧版启动回调列表（被忽略）
            on_shutdown: 旧版关闭回调列表（被忽略）
            lifespan: 新版生命周期上下文管理器（Starlette 1.0+）
        """
        # 调用原始的 __init__，只传递 lifespan 参数
        return _orig_router_init(self, *args, lifespan=lifespan, **kwargs)

    # 替换 Router.__init__ 方法（猴子补丁）
    _starlette_routing.Router.__init__ = _compat_router_init  # type: ignore[method-assign]

# Patch 2: 为 Router 类添加 on_startup/on_shutdown 属性
# FastAPI 的 include_router 会读取这些属性作为实例属性
if not hasattr(_starlette_routing.Router, "on_startup"):
    # 如果 Router 类没有 on_startup 属性，添加一个空列表
    _starlette_routing.Router.on_startup = []  # type: ignore[attr-defined]
if not hasattr(_starlette_routing.Router, "on_shutdown"):
    # 如果 Router 类没有 on_shutdown 属性，添加一个空列表
    _starlette_routing.Router.on_shutdown = []  # type: ignore[attr-defined]

# ============================================================================
# 导入 FastAPI 相关模块
# ============================================================================
from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

# 导入所有路由模块
from backend.routes import console, dashboard, eval, health, identity, ingestion, knowledge_bases, openapi_v1, parse, rag
# 导入身份同步调度器（用于定期同步用户身份信息）
from backend.services.identity_sync_scheduler import start_identity_sync_scheduler, stop_identity_sync_scheduler
# 导入配置和运行时设置
from core.config import load_project_env
from core.runtime_settings import apply_runtime_env_overrides


# ============================================================================
# CORS 配置常量
# ============================================================================
# 知识点 - CORS（跨域资源共享）：
# - 浏览器的安全策略：禁止网页向不同域名发送 AJAX 请求
# - 前端（localhost:3000）和后端（localhost:8000）端口不同，属于跨域
# - CORS 中间件允许指定哪些域名可以访问后端

# 默认允许的 HTTP 头列表
_DEFAULT_CORS_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Content-Language",
    "Content-Type",
    "X-API-Key",
    "X-KB-Timestamp",
    "X-KB-Nonce",
    "X-KB-Body-SHA256",
    "X-KB-Signature",
    "X-Requested-With",
]


def _get_default_response_class():
    """
    【获取默认响应类】

    知识点 - ORJSON vs JSONResponse：
    - ORJSON: 高性能 JSON 序列化库，速度比标准 json 快 3-5 倍
    - JSONResponse: FastAPI 默认的响应类，使用标准 json 库
    - ORJSON 不在所有环境都可用，需要优雅降级

    为什么需要检查？
    - Docker 最小镜像可能不包含 orjson 包
    - 后端健康检查应该能在最小环境工作
    - 动态检查避免导入错误导致应用崩溃

    实现逻辑：
    1. 使用 importlib.util.find_spec 检查 orjson 是否可用
    2. 如果可用，返回 ORJSONResponse（高性能）
    3. 如果不可用，返回 JSONResponse（标准实现）

    返回：
        响应类：ORJSONResponse 或 JSONResponse
    """
    # 检查 orjson 包是否已安装
    # importlib.util.find_spec 不实际导入模块，只检查是否存在
    if importlib.util.find_spec("orjson") is None:
        # 如果 orjson 未安装，使用标准 JSONResponse
        return JSONResponse

    # 如果 orjson 已安装，使用高性能的 ORJSONResponse
    return ORJSONResponse


def _get_cors_origins() -> list[str]:
    """
    【获取 CORS 允许的域名列表】

    知识点 - CORS Origin：
    - Origin: 请求来源的完整 URL（协议+域名+端口）
    - allow_origins: 允许访问的域名列表
    - 生产环境应该限制为实际的前端域名

    配置优先级：
    1. 环境变量 KB_CORS_ALLOW_ORIGINS 或 CORS_ALLOW_ORIGINS
    2. 如果未配置，使用默认值（本地开发环境）

    环境变量格式：
    - 多个域名用逗号分隔
    - 示例："https://app.example.com,https://admin.example.com"

    默认域名：
    - http://127.0.0.1:3000: Next.js 默认开发地址（IP形式）
    - http://localhost:3000: Next.js 默认开发地址（域名形式）
    - 为什么两个？某些浏览器对 localhost 和 127.0.0.1 的处理不同

    返回：
        list[str]: 允许的域名列表
    """
    # 从环境变量读取配置
    configured = os.getenv("KB_CORS_ALLOW_ORIGINS") or os.getenv("CORS_ALLOW_ORIGINS")

    if configured:
        # 如果配置了环境变量，解析逗号分隔的列表
        # 使用 strip() 去除每个域名两侧的空格
        return [item.strip() for item in configured.split(",") if item.strip()]

    # 如果未配置，返回默认开发环境域名
    return [
        "http://127.0.0.1:3000",  # IP 形式的本地地址
        "http://localhost:3000",   # 域名形式的本地地址
    ]


def _get_cors_headers() -> list[str]:
    """
    【获取 CORS 允许的 HTTP 头列表】

    知识点 - CORS Headers：
    - allow_headers: 允许浏览器发送的 HTTP 头列表
    - 某些自定义头（如 X-API-Key）需要在 CORS 中明确允许
    - 否则浏览器会在发送请求前报错

    配置优先级：
    1. 环境变量 KB_CORS_ALLOW_HEADERS 或 CORS_ALLOW_HEADERS
    2. 如果未配置，使用默认值

    环境变量格式：
    - 多个头用逗号分隔
    - 示例："Authorization,Content-Type,X-API-Key"

    返回：
        list[str]: 允许的 HTTP 头列表
    """
    # 从环境变量读取配置
    configured = os.getenv("KB_CORS_ALLOW_HEADERS") or os.getenv("CORS_ALLOW_HEADERS")

    if configured:
        # 如果配置了环境变量，解析逗号分隔的列表
        return [item.strip() for item in configured.split(",") if item.strip()]

    # 如果未配置，返回默认允许的头列表
    return _DEFAULT_CORS_HEADERS


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    """
    【应用生命周期管理器】

    知识点 - 上下文管理器：
    - @asynccontextmanager: 异步上下文管理器装饰器
    - yield 前的代码在应用启动时执行
    - yield 后的代码在应用关闭时执行
    - 管理应用的全局资源（调度器、连接池等）

    知识点 - FastAPI 生命周期：
    - FastAPI 0.115+ 推荐使用 lifespan 替代旧的 on_startup/on_shutdown
    - lifespan 是一个异步上下文管理器
    - 更优雅，支持异步资源管理

    这个生命周期管理器做什么？
    - 启动时：启动身份同步调度器（定期同步用户身份信息）
    - 关闭时：停止身份同步调度器（清理资源）

    参数：
        _app: FastAPI 应用实例（未使用，但必须接收）

    使用示例：
        # FastAPI 会自动调用这个管理器
        app = FastAPI(lifespan=_app_lifespan)

        # 启动时执行：
        # await start_identity_sync_scheduler()
        # yield
        # 应用运行期间...
        # 关闭时执行：
        # await stop_identity_sync_scheduler()
    """
    # 启动阶段：启动身份同步调度器
    await start_identity_sync_scheduler()

    try:
        # yield: 让应用运行，这里暂停执行
        # 当应用关闭时，会回到这里继续执行
        yield
    finally:
        # 关闭阶段：停止身份同步调度器
        # finally 保证即使启动失败也会执行清理
        await stop_identity_sync_scheduler()


def create_app() -> FastAPI:
    """
    【创建 FastAPI 应用实例】

    这是应用创建的主函数，负责：
    1. 加载配置
    2. 创建 FastAPI 实例
    3. 配置 CORS
    4. 注册路由
    5. 配置异常处理
    6. 设置静态文件服务

    知识点 - FastAPI 应用创建：
    - FastAPI 是一个 ASGI 应用（异步服务器网关接口）
    - 需要一个 ASGI 服务器运行（如 Uvicorn、Hypercorn）
    - 所有配置都在创建时指定

    返回：
        FastAPI: 配置好的应用实例

    使用示例：
        # 创建应用
        app = create_app()

        # 使用 Uvicorn 运行
        uvicorn.run(app, host="0.0.0.0", port=8000)
    """
    # 第一步：加载项目环境变量
    # 从 .env 文件加载配置到 os.environ
    load_project_env()

    # 应用运行时环境变量覆盖
    # 允许某些配置在运行时动态修改
    apply_runtime_env_overrides()

    # 第二步：创建 FastAPI 应用实例
    app = FastAPI(
        title="WiseWe RAG Console API",      # API 标题（显示在文档页）
        version="0.1.0",                     # API 版本
        default_response_class=_get_default_response_class(),  # 默认响应类
        lifespan=_app_lifespan,              # 生命周期管理器
    )

    # 第三步：添加 CORS 中间件
    # 中间件：在请求到达路由前/响应返回前执行的钩子
    app.add_middleware(
        CORSMiddleware,                     # CORS 中间件类
        allow_origins=_get_cors_origins(),  # 允许的域名列表
        allow_credentials=True,             # 允许发送 Cookie
        allow_methods=["*"],                # 允许所有 HTTP 方法（GET/POST/PUT/DELETE等）
        allow_headers=_get_cors_headers(),  # 允许的 HTTP 头列表
    )

    # 第四步：注册所有路由模块
    # 每个路由模块处理不同的功能域
    app.include_router(health.router)              # 健康检查路由
    app.include_router(identity.router)            # 身份认证路由
    app.include_router(knowledge_bases.router)     # 知识库管理路由
    app.include_router(console.router)             # 控制台路由
    app.include_router(parse.router)               # PDF 解析路由
    app.include_router(rag.router)                 # RAG 查询路由
    app.include_router(openapi_v1.router)          # OpenAPI V1 兼容路由
    app.include_router(eval.router)                # 评估路由
    app.include_router(ingestion.router)           # 数据导入路由
    app.include_router(dashboard.router)           # 仪表盘路由

    # 第五步：配置自定义异常处理器
    # 处理请求参数验证失败的异常
    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request, exc):
        """
        【请求验证错误异常处理器】

        知识点 - FastAPI 异常处理：
        - @app.exception_handler 注册异常处理器
        - RequestValidationError: 请求参数验证失败时抛出
        - 可以自定义错误响应格式

        特殊处理：
        - OpenAPI V1 接口需要特殊的错误格式（兼容旧版）
        - 其他接口使用默认的错误处理

        参数：
            request: 请求对象
            exc: 异常对象（包含错误详情）

        返回：
            JSONResponse: 错误响应
        """
        # 检查是否是 OpenAPI V1 接口的请求
        if str(request.url.path).startswith("/openapi/v1/"):
            # OpenAPI V1 接口使用特殊的错误格式
            return openapi_v1.validation_error_response(exc.errors())

        # 其他接口使用默认的错误处理
        # 调用 FastAPI 内置的验证错误处理器
        return await request_validation_exception_handler(request, exc)

    # 第六步：配置静态文件服务
    # 提供解析后的文件（图片、PDF 等）的访问
    output_dir = Path("data/output")          # 输出目录路径
    output_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

    # 挂载静态文件服务到 /api/assets/output 路径
    # 访问示例：http://localhost:8000/api/assets/output/images/page_5_img_2.png
    app.mount(
        "/api/assets/output",               # URL 路径前缀
        StaticFiles(directory=output_dir),  # 静态文件服务
        name="output-assets"                # 服务名称（用于反向解析）
    )

    return app


# ============================================================================
# 全局应用实例
# ============================================================================
# 创建全局应用实例，供 Uvicorn 或其他 ASGI 服务器使用
# 这是导入这个模块时立即创建的单例
app = create_app()
