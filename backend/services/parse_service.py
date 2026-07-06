"""
解析服务模块 (Parse Service)

本模块封装了 PDF 文档解析的业务逻辑，为 HTTP API 层提供统一的解析预览接口。
作为后端服务层的一部分，本模块负责协调底层解析器适配器，并将原始解析结果
转换为前端友好的数据格式。

业务逻辑概述：
    1. 接收 PDF 文件路径作为输入
    2. 通过适配器调用底层解析器（当前为 MinerU 云端 API）
    3. 将解析器返回的 ContentBlock 领域模型转换为 API 响应格式
    4. 为每个内容块分配唯一标识符，便于前端追踪和管理

数据流向：
    HTTP 请求 → get_parse_preview() → fetch_parse_adapter() →
    MinerU 解析器 → ContentBlock 列表 → 格式转换 → JSON 响应

架构位置：
    backend/services/parse_service.py（本模块，应用服务层）
        ↓ 依赖
    backend/adapters/parse_adapter.py（适配器层）
        ↓ 依赖
    core/parser/mineru_parser.py（领域解析能力）

设计原则：
    - 服务层不直接处理 HTTP 请求/响应细节（由 routes 层负责）
    - 服务层不直接调用底层解析器，而是通过适配器解耦
    - 保持服务函数的无状态性，便于测试和复用
"""

from __future__ import annotations

from backend.adapters.parse_adapter import fetch_parse_preview
from core.models.content_block import ContentBlock


def get_parse_preview(pdf_path: str | None) -> list[dict]:
    """
    获取 PDF 文档的解析预览结果。

    这是解析服务的主入口函数，用于获取 PDF 文件解析后的内容块列表。
    该函数适用于以下场景：
    - 前端预览解析结果，用户确认后再执行完整管道
    - 调试和验证解析器输出
    - 快速检查 PDF 内容结构

    业务流程：
        1. 调用适配器获取原始解析结果（ContentBlock 列表）
        2. 遍历内容块，转换为前端所需的数据格式
        3. 为每个块分配递增的唯一 ID（block-001, block-002, ...）

    Args:
        pdf_path: PDF 文件的绝对路径。如果为 None，适配器将返回预定义的
                  Mock 数据（用于开发和测试）。在 Mock 模式下，无需真实的
                  PDF 文件或云端 API 调用。

    Returns:
        list[dict]: 格式化后的内容块列表，每个字典包含以下字段：
            - id (str): 内容块唯一标识符，格式为 "block-XXX"
            - type (str): 块类型（text/table/image 等）
            - text (str): 块的文本内容
            - page (int): 所在页码（从 1 开始，便于用户理解）
            - level (int | None): 文本层级（标题层级，用于语义分析）
            - sourceFile (str | None): 来源文件名
            - tableHtml (str | None): 表格的 HTML 渲染代码（仅表格块）
            - imagePath (str | None): 图片的相对路径（仅图片块）

    Example:
        >>> blocks = get_parse_preview("data/input/sample.pdf")
        >>> print(blocks[0])
        {
            "id": "block-001",
            "type": "text",
            "text": "第一章 引言",
            "page": 1,
            "level": 1,
            ...
        }

    Note:
        - 页码从 1 开始（用户视角），而底层 ContentBlock.page_idx 从 0 开始
        - 如果解析失败，适配器会抛出异常，调用方需要捕获处理
        - Mock 模式下返回固定的示例数据，便于前端开发和测试
    """
    # 调用适配器获取原始解析结果
    # 适配器负责协调 OSS 上传、MinerU 任务提交、轮询和结果下载
    blocks = fetch_parse_preview(pdf_path)

    # 将领域模型转换为 API 响应格式
    # enumerate 为每个块分配递增索引，用于生成唯一 ID
    return [_block_to_payload(block, i) for i, block in enumerate(blocks)]


def _block_to_payload(block: ContentBlock, index: int) -> dict:
    """
    将 ContentBlock 领域模型转换为前端 API 响应格式。

    这是一个内部辅助函数，负责将核心领域模型转换为适合 HTTP 传输的字典格式。
    该函数执行以下转换：
        - 类型枚举转换为字符串值（便于 JSON 序列化）
        - 页码索引从 0-based 转换为 1-based（用户视角）
        - 为每个块生成格式化的唯一标识符

    数据转换规则：
        - id: 使用 "block-{index:03d}" 格式，例如 "block-001"
        - type: 从 BlockType 枚举提取字符串值（如 "text"、"table"）
        - page: 将 0-based 的 page_idx 加 1，转换为用户友好的页码
        - 其他字段直接映射，保持原始类型

    Args:
        block: ContentBlock 实例，包含解析器输出的原始数据。
               包含 type、text、page_idx、text_level、source_file、
               table_html、image_path 等字段。
        index: 内容块在列表中的索引位置（从 0 开始），用于生成唯一 ID。

    Returns:
        dict: 转换后的字典，字段名采用驼峰命名法（camelCase）以符合
              JavaScript/TypeScript 前端的命名惯例。

    Note:
        - 此函数不处理 None 值，假设 block 参数始终有效
        - 字段名使用驼峰命名（如 tableHtml），适配前端 TypeScript 接口
        - 保留所有字段，即使某些字段为 None，确保响应结构一致性
    """
    return {
        # 生成格式化的唯一 ID：block-001, block-002, ...
        "id": f"block-{index + 1:03d}",

        # 将 BlockType 枚举转换为字符串值
        "type": block.type.value,

        # 文本内容（表格块包含纯文本表示，图片块可能为空）
        "text": block.text,

        # 页码从 0-based 转换为 1-based（用户视角）
        "page": int(block.page_idx) + 1,

        # 文本层级（用于标题结构分析，可能为 None）
        "level": block.text_level,

        # 来源文件名（多文件解析场景）
        "sourceFile": block.source_file,

        # 表格 HTML 渲染代码（仅表格块有效）
        "tableHtml": block.table_html,

        # 图片相对路径（仅图片块有效）
        "imagePath": block.image_path,
    }
