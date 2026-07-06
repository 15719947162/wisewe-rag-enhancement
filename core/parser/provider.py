"""
解析器提供者管理器

这个模块负责管理和切换不同的 PDF 解析器。
通过环境变量 PDF_PARSER_PROVIDER 可以选择使用哪个解析器。

支持的解析器:
1. mineru (默认) - 302AI 托管的 MinerU 云端解析服务
2. mineru_official - MinerU 官方 Precision API
3. ali_document_mind - 阿里云 Document Mind 智能文档解析

工作原理:
- 根据环境变量 PDF_PARSER_PROVIDER 选择解析器
- 动态加载对应的解析器模块
- 提供统一的 parse_pdf 接口

使用场景:
- 不同项目可能需要使用不同的解析服务
- 某个服务故障时可以快速切换到备用服务
- 测试不同解析器的效果对比

环境变量:
- PDF_PARSER_PROVIDER: 解析器名称(mineru/mineru_official/ali_document_mind)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from core.models.content_block import ContentBlock

PDF_PARSER_PROVIDER_ENV = "PDF_PARSER_PROVIDER"
DEFAULT_PDF_PARSER_PROVIDER = "mineru"


@dataclass(frozen=True)
class ParserChannel:
    """
    解析器通道配置

    定义一个解析器的基本信息:
    - key: 解析器唯一标识
    - label: 解析器显示名称
    - module: 解析器模块路径
    - description: 解析器描述

    frozen=True 表示不可变对象。

    示例:
        ParserChannel(
            key="mineru",
            label="302AI MinerU",
            module="core.parser.mineru_parser",
            description="Existing 302AI-hosted MinerU cloud parser."
        )
    """
    key: str
    label: str
    module: str
    description: str


PDF_PARSER_CHANNELS: dict[str, ParserChannel] = {
    """
    解析器通道字典

    存储所有可用的解析器配置。

    包含三个解析器:
    1. mineru: 302AI 托管的 MinerU
       - 特点: 稳定可靠,已有成熟的使用经验
       - 适合: 一般 PDF 解析需求

    2. mineru_official: MinerU 官方 API
       - 特点: 功能更新快,支持更多选项
       - 适合: 需要最新功能的场景

    3. ali_document_mind: 阿里云 Document Mind
       - 特点: 阿里云原生服务,集成方便
       - 适合: 已有阿里云基础设施的项目

    通过环境变量 PDF_PARSER_PROVIDER 选择:
        export PDF_PARSER_PROVIDER=mineru_official
    """
    "mineru": ParserChannel(
        key="mineru",
        label="302AI MinerU",
        module="core.parser.mineru_parser",
        description="Existing 302AI-hosted MinerU cloud parser.",
    ),
    "mineru_official": ParserChannel(
        key="mineru_official",
        label="MinerU Official Precision API",
        module="core.parser.mineru_official_parser",
        description="Official MinerU Precision API via mineru.net.",
    ),
    "ali_document_mind": ParserChannel(
        key="ali_document_mind",
        label="Alibaba Document Mind",
        module="core.parser.document_mind_parser",
        description="Alibaba Document Mind parser provider.",
    ),
}
SUPPORTED_PDF_PARSER_PROVIDERS = set(PDF_PARSER_CHANNELS)


def get_pdf_parser_provider() -> str:
    """
    获取当前配置的解析器提供者

    从环境变量读取 PDF_PARSER_PROVIDER,返回解析器名称。

    如果环境变量未设置,返回默认值 "mineru"。
    如果设置了不支持的解析器名称,抛出 ValueError。

    返回:
        解析器名称字符串

    异常:
        ValueError: 不支持的解析器名称

    示例:
        provider = get_pdf_parser_provider()  # 返回 "mineru" 或其他支持的名称
    """
    provider = os.getenv(PDF_PARSER_PROVIDER_ENV, DEFAULT_PDF_PARSER_PROVIDER).strip().lower()
    if not provider:
        provider = DEFAULT_PDF_PARSER_PROVIDER
    if provider not in SUPPORTED_PDF_PARSER_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PDF_PARSER_PROVIDERS))
        raise ValueError(f"Unsupported PDF parser provider '{provider}'. Allowed values: {allowed}")
    return provider


def parse_pdf(
    pdf_path: str,
    output_dir: str = "data/output",
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> list[ContentBlock]:
    """
    使用配置的解析器解析 PDF

    这是统一的解析入口,根据配置自动选择合适的解析器。

    工作流程:
    1. 读取环境变量确定使用哪个解析器
    2. 动态加载对应的解析器模块
    3. 调用该模块的 parse_pdf 函数
    4. 返回解析结果

    参数:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        log_fn: 日志回调函数
        original_name: 原始文件名

    返回:
        ContentBlock 列表

    示例:
        # 使用默认解析器(mineru)
        blocks = parse_pdf("document.pdf")

        # 切换解析器(通过环境变量)
        # export PDF_PARSER_PROVIDER=ali_document_mind
        blocks = parse_pdf("document.pdf")  # 自动使用阿里云解析器
    """
    provider = get_pdf_parser_provider()
    channel = PDF_PARSER_CHANNELS[provider]
    module = __import__(channel.module, fromlist=["parse_pdf"])
    parse_with_channel = getattr(module, "parse_pdf")
    return parse_with_channel(
        pdf_path,
        output_dir=output_dir,
        log_fn=log_fn,
        original_name=original_name,
    )
