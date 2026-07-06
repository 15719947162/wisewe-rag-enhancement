"""
LLM 配置管理模块

本模块提供全局 LLM（大语言模型）配置管理功能，用于统一管理项目中所有 LLM 调用的配置参数。

核心功能：
1. LLMConfig 数据类：封装 LLM 配置参数（API 地址、密钥、模型、系统提示词等）
2. 全局配置管理：支持在应用启动时设置一次，全局共享
3. 参数优先级解析：按优先级顺序解析参数（函数参数 > 全局配置 > 环境变量 > 默认值）

使用场景：
- CLI 入口处设置全局配置
- 各模块通过 get_global_llm_config() 或 resolve_llm_param() 获取配置
- 支持 pipeline 各阶段使用不同的系统提示词（清洗、切片、质量门控、增强、RAG）

示例：
    # 在应用启动时设置全局配置
    from core.llm_config import set_global_llm_config
    set_global_llm_config(
        base_url="https://api.openai.com/v1",
        api_key="sk-xxx",
        model="gpt-4",
        subject_type="technical",
        layout_type="two_column"
    )

    # 在具体模块中获取配置
    from core.llm_config import get_global_llm_config, resolve_llm_param
    config = get_global_llm_config()
    api_key = resolve_llm_param(
        local="",
        global_attr="api_key",
        env_vars=["LLM_API_KEY", "OPENAI_API_KEY"]
    )
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LLMConfig:
    """
    LLM 配置数据类

    存储大语言模型调用所需的所有配置参数，包括：
    - 连接信息：API 基础地址、密钥
    - 模型信息：模型名称
    - 提示词配置：通用系统提示词及各阶段专用提示词
    - 文档特性：主题类型、布局类型

    Attributes:
        base_url: LLM API 的基础 URL（如 "https://api.openai.com/v1"）
        api_key: LLM API 的访问密钥
        model: 使用的模型名称（如 "gpt-4"、"qwen-max"）
        system_prompt: 通用系统提示词，用于指导 LLM 的角色和行为
        cleaner_system_prompt: 文档清洗阶段的专用系统提示词
        chunker_system_prompt: 切片阶段的专用系统提示词
        quality_gate_system_prompt: 质量门控阶段的专用系统提示词
        enhance_system_prompt: 内容增强阶段的专用系统提示词
        rag_system_prompt: RAG 检索增强生成阶段的专用系统提示词
        subject_type: 文档主题类型（如 "general"、"technical"、"legal"），
                      用于自动构建系统提示词
        layout_type: 文档布局类型（如 "single_column"、"two_column"），
                     用于自动构建系统提示词

    Example:
        >>> config = LLMConfig(
        ...     base_url="https://api.openai.com/v1",
        ...     api_key="sk-xxx",
        ...     model="gpt-4",
        ...     system_prompt="你是一个专业的文档分析助手"
        ... )
        >>> print(config.model)
        gpt-4
    """

    # 连接配置
    base_url: str = ""           # LLM API 基础 URL
    api_key: str = ""            # LLM API 访问密钥
    model: str = ""              # 模型名称

    # 系统提示词配置
    system_prompt: str = ""                       # 通用系统提示词
    cleaner_system_prompt: str = ""               # 清洗阶段系统提示词
    chunker_system_prompt: str = ""               # 切片阶段系统提示词
    quality_gate_system_prompt: str = ""          # 质量门控阶段系统提示词
    enhance_system_prompt: str = ""               # 增强阶段系统提示词
    rag_system_prompt: str = ""                   # RAG 阶段系统提示词

    # 文档特性配置（用于自动构建系统提示词）
    subject_type: str = "general"                 # 主题类型（general/technical/legal 等）
    layout_type: str = "single_column"            # 布局类型（single_column/two_column 等）


# ============================================================================
# 全局配置实例
# ============================================================================

# 全局 LLM 配置实例，通过 set_global_llm_config() 设置，get_global_llm_config() 获取
# 作用：避免在每个模块中重复传递配置参数，实现配置的集中管理
_global: LLMConfig = LLMConfig()


# ============================================================================
# 配置管理函数
# ============================================================================

def set_global_llm_config(
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    system_prompt: str = "",
    cleaner_system_prompt: str = "",
    chunker_system_prompt: str = "",
    quality_gate_system_prompt: str = "",
    enhance_system_prompt: str = "",
    rag_system_prompt: str = "",
    subject_type: str = "general",
    layout_type: str = "single_column",
) -> None:
    """
    设置全局 LLM 配置

    在应用启动时调用一次，后续所有模块可通过 get_global_llm_config() 获取。
    支持智能系统提示词构建：当未显式提供 system_prompt 时，会根据 subject_type
    和 layout_type 自动构建适合的系统提示词。

    Args:
        base_url: LLM API 基础 URL（如 "https://api.openai.com/v1"）
        api_key: LLM API 访问密钥
        model: 模型名称（如 "gpt-4"、"qwen-max"）
        system_prompt: 系统提示词。若为空且 subject_type/layout_type 非默认值，
                      则自动调用 core.prompts.build_system_prompt() 构建
        cleaner_system_prompt: 文档清洗阶段的专用系统提示词
        chunker_system_prompt: 切片阶段的专用系统提示词
        quality_gate_system_prompt: 质量门控阶段的专用系统提示词
        enhance_system_prompt: 内容增强阶段的专用系统提示词
        rag_system_prompt: RAG 检索增强生成阶段的专用系统提示词
        subject_type: 文档主题类型（"general"、"technical"、"legal" 等）
        layout_type: 文档布局类型（"single_column"、"two_column" 等）

    Returns:
        None

    Example:
        >>> # 在 CLI 或 serve.py 入口处设置
        >>> set_global_llm_config(
        ...     base_url="https://api.openai.com/v1",
        ...     api_key="sk-xxx",
        ...     model="gpt-4",
        ...     subject_type="technical",
        ...     layout_type="two_column"
        ... )
        >>> # system_prompt 会自动构建

    Note:
        - 通常在 CLI 入口或 Web 服务启动时调用一次
        - 如果使用 DashScope，base_url 可省略，会自动设置
        - 系统提示词构建逻辑见 core/prompts.py
    """
    global _global

    # 如果未显式提供 system_prompt，且主题或布局类型非默认值，
    # 则从预设模板自动构建系统提示词
    if not system_prompt and (subject_type != "general" or layout_type != "single_column"):
        from core.prompts import build_system_prompt
        system_prompt = build_system_prompt(subject_type, layout_type)

    # 更新全局配置实例
    _global = LLMConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        cleaner_system_prompt=cleaner_system_prompt,
        chunker_system_prompt=chunker_system_prompt,
        quality_gate_system_prompt=quality_gate_system_prompt,
        enhance_system_prompt=enhance_system_prompt,
        rag_system_prompt=rag_system_prompt,
        subject_type=subject_type,
        layout_type=layout_type,
    )


def get_global_llm_config() -> LLMConfig:
    """
    获取全局 LLM 配置实例

    返回通过 set_global_llm_config() 设置的全局配置。如果未设置过，返回默认配置
    （所有字段为空字符串）。

    Returns:
        LLMConfig: 全局 LLM 配置实例

    Example:
        >>> config = get_global_llm_config()
        >>> print(config.model)
        gpt-4
        >>> print(config.api_key)
        sk-xxx

    Note:
        - 应在 set_global_llm_config() 调用后使用
        - 如果需要更灵活的参数解析，使用 resolve_llm_param()
    """
    return _global


def resolve_llm_param(
    local: str,
    global_attr: str,
    env_vars: list[str],
    default: str = "",
) -> str:
    """
    按优先级解析 LLM 参数

    实现四级参数解析优先级：
    1. 调用方显式传入的参数（local）
    2. 全局配置中的对应属性（global_attr）
    3. 环境变量（按 env_vars 列表顺序依次尝试）
    4. 默认值（default）

    这种设计允许：
    - CLI 启动时设置全局配置，避免重复传参
    - 特定调用可覆盖全局配置
    - 未配置时自动从环境变量读取（兼容直接使用 API Key 的场景）

    Args:
        local: 调用方显式传入的参数值。非空则直接返回
        global_attr: 全局配置 LLMConfig 中的属性名（如 "api_key"、"base_url"）
        env_vars: 环境变量名列表，按优先级排序（如 ["LLM_API_KEY", "OPENAI_API_KEY"]）
        default: 所有来源都未提供时的默认值

    Returns:
        str: 解析后的参数值

    Example:
        >>> # 解析 API Key
        >>> api_key = resolve_llm_param(
        ...     local="",  # 未显式传入
        ...     global_attr="api_key",
        ...     env_vars=["LLM_API_KEY", "OPENAI_API_KEY"],
        ...     default=""
        ... )
        >>> # 返回优先级：local > 全局配置.api_key > LLM_API_KEY 环境变量 > OPENAI_API_KEY 环境变量 > ""

        >>> # 解析 Base URL
        >>> base_url = resolve_llm_param(
        ...     local="https://custom.api.com/v1",
        ...     global_attr="base_url",
        ...     env_vars=["LLM_BASE_URL", "OPENAI_BASE_URL"],
        ...     default="https://api.openai.com/v1"
        ... )
        >>> # 返回 "https://custom.api.com/v1"（local 优先级最高）

    Note:
        - 常用于 embedding 客户端、LLM cleaner 等需要灵活配置的模块
        - 环境变量名称建议使用 LLM_ 前缀的通用名称，兼容多种 Provider
        - 参考 core/embedding/client.py 中的实际使用示例
    """
    # 优先级 1：调用方显式传入
    if local:
        return local

    # 优先级 2：全局配置
    global_val = getattr(_global, global_attr, "")
    if global_val:
        return global_val

    # 优先级 3：环境变量（按列表顺序依次尝试）
    for var in env_vars:
        val = os.environ.get(var, "")
        if val:
            return val

    # 优先级 4：默认值
    return default
