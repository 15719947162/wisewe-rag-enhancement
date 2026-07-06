"""
【配置管理模块 - config.py】

本文件负责加载和管理整个项目的配置。

主要职责：
1. 加载 .env 环境变量文件
2. 加载 config.yaml 配置文件
3. 提供默认配置值
4. 合并运行时配置覆盖

配置优先级（从高到低）：
1. 运行时环境变量（最高优先级）
2. .env 文件中的变量
3. config.yaml 文件中的配置
4. 代码中的默认值（最低优先级）

知识点 - 配置管理最佳实践：
- 使用 .env 文件存储敏感信息（API Key、密码等）
- 使用 YAML 文件存储非敏感配置
- 环境变量优先级高于配置文件
- 提供合理的默认值

配置示例（config.yaml）：
    parser:
      mode: cloud
      cloud:
        parse_method: auto
        version: "2.0"
        timeout: 1800
    output:
      dir: data/output

作者：RAG 项目组
"""

from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.runtime_settings import apply_runtime_env_overrides, merge_runtime_config_overrides


# ============================================================================
# 项目根目录
# ============================================================================
# 获取项目根目录的绝对路径
# Path(__file__) 是当前文件的路径
# .parent 获取父目录（core 目录）
# .parent 再次获取父目录（项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env(override: bool = False) -> bool:
    """
    【加载项目环境变量】

    从项目根目录的 .env 文件加载环境变量到 os.environ。

    知识点 - python-dotenv：
    - dotenv 是 Python 环境变量管理库
    - .env 文件格式：KEY=VALUE
    - 自动忽略注释（以 # 开头的行）
    - 支持多行值

    参数：
        override: 是否覆盖已存在的环境变量
            - False（默认）: 不覆盖，保留系统环境变量
            - True: 用 .env 文件的值覆盖

    返回：
        bool: 是否成功加载 .env 文件

    .env 文件示例：
        # API 配置
        OPENAI_API_KEY=sk-xxxxx
        DASHSCOPE_API_KEY=sk-xxxxx

        # 数据库配置
        PGVECTOR_HOST=localhost
        PGVECTOR_PORT=5432

    使用场景：
        # 在应用启动时调用
        load_project_env()
        api_key = os.getenv("OPENAI_API_KEY")
    """
    # 加载 .env 文件
    # _PROJECT_ROOT / ".env" 构建 .env 文件的绝对路径
    loaded = load_dotenv(_PROJECT_ROOT / ".env", override=override)

    # 应用运行时环境变量覆盖
    # 某些环境变量可以在运行时动态修改
    apply_runtime_env_overrides()

    return loaded


def load_config(config_path: str = "config.yaml") -> dict:
    """
    【加载配置文件】

    从 YAML 配置文件加载配置，返回配置字典。
    如果配置文件不存在，返回默认配置。

    知识点 - YAML 配置文件：
    - YAML（YAML Ain't Markup Language）是一种配置文件格式
    - 比 JSON 更易读，支持注释
    - Python 使用 PyYAML 库解析 YAML

    配置结构：
        parser:
          mode: cloud
          cloud:        # 云端解析配置
            parse_method: auto
            version: "2.0"
            timeout: 1800
            poll_interval: 3
          oss:          # OSS 上传配置
            prefix: mineru-uploads
            url_expiry: 3600
        output:
          dir: data/output
          encoding: utf-8-sig

    参数：
        config_path: 配置文件路径（相对或绝对路径）
            - 默认值："config.yaml"
            - 如果是相对路径，相对于当前工作目录

    返回：
        dict: 配置字典

    使用示例：
        config = load_config()
        parser_mode = config["parser"]["mode"]
        output_dir = config["output"]["dir"]
    """
    # 首先加载环境变量
    load_project_env()

    # 将配置文件路径转换为 Path 对象
    path = Path(config_path)

    # 检查配置文件是否存在
    if not path.exists():
        # 配置文件不存在，使用默认配置
        defaults = {
            "parser": {
                "mode": "cloud",  # 解析模式：cloud（云端）或 local（本地）
                "cloud": {
                    # MinerU 云端解析配置
                    "parse_method": "auto",  # 解析方法：auto（自动检测）
                    "version": "2.0",        # MinerU 版本
                    "timeout": 1800,         # 超时时间（秒）：30 分钟
                    "poll_interval": 3,      # 轮询间隔（秒）
                    "enable_formula": True,  # 是否启用公式识别
                    "enable_table_html": True,  # 是否启用表格 HTML 提取
                    "language": "ch",        # 文档语言：ch（中文）
                    "is_ocr": False,         # 是否启用 OCR
                    "model_version": "v2",   # 模型版本
                    # PDF 分片配置（大文件处理）
                    "sharding": {
                        "enabled": True,         # 是否启用分片
                        "min_pages": 120,        # 最小页数阈值
                        "min_file_mb": 80,       # 最小文件大小阈值（MB）
                        "pages_per_shard": 20,   # 每个分片的页数
                        "max_concurrency": 2,    # 最大并发数
                        "text_sample_pages": 5,  # 文本采样页数
                    },
                },
                "oss": {
                    # OSS 上传配置
                    "prefix": "mineru-uploads",  # OSS 文件前缀
                    "url_expiry": 3600,          # 签名 URL 过期时间（秒）：1 小时
                },
            },
            "output": {
                "dir": "data/output",     # 输出目录
                "encoding": "utf-8-sig",  # CSV 编码（带 BOM，Excel 兼容）
            },
        }

        # 合并运行时配置覆盖
        # 允许通过环境变量动态修改配置
        return merge_runtime_config_overrides(defaults)

    # 配置文件存在，读取并解析
    with open(path, "r", encoding="utf-8") as f:
        # yaml.safe_load 安全地加载 YAML（不执行任意 Python 代码）
        config = yaml.safe_load(f) or {}

    # 合并运行时配置覆盖
    return merge_runtime_config_overrides(config)


def load_pgvector_config(config_path: str = "config.yaml") -> dict:
    """
    【加载 pgvector 配置】

    从配置文件中提取 pgvector 相关配置。

    知识点 - pgvector：
    - pgvector 是 PostgreSQL 的向量扩展
    - 允许在数据库中存储和查询向量
    - 用于向量相似度检索

    pgvector 配置示例（config.yaml）：
        pgvector:
          enabled: true
          default_kb_id: "kb_001"

    参数：
        config_path: 配置文件路径

    返回：
        dict: pgvector 配置字典
            - enabled: 是否启用 pgvector
            - default_kb_id: 默认知识库 ID

    使用示例：
        pgv_config = load_pgvector_config()
        if pgv_config["enabled"]:
            # 连接到 pgvector
            connect_pgvector()
    """
    # 加载完整配置
    config = load_config(config_path)

    # 提取 pgvector 配置部分
    pgv = config.get("pgvector", {})

    # 返回标准化的配置字典
    return {
        "enabled": bool(pgv.get("enabled", False)),       # 是否启用，默认 False
        "default_kb_id": str(pgv.get("default_kb_id", "default")),  # 默认知识库 ID
    }
