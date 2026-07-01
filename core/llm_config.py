from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    system_prompt: str = ""
    cleaner_system_prompt: str = ""
    chunker_system_prompt: str = ""
    quality_gate_system_prompt: str = ""
    enhance_system_prompt: str = ""
    rag_system_prompt: str = ""
    subject_type: str = "general"
    layout_type: str = "single_column"


_global: LLMConfig = LLMConfig()


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
    """设置全局 LLM 配置，在 CLI/UI 入口处调用一次即可。"""
    global _global
    # If system_prompt not explicitly provided, build from subject/layout presets
    if not system_prompt and (subject_type != "general" or layout_type != "single_column"):
        from core.prompts import build_system_prompt
        system_prompt = build_system_prompt(subject_type, layout_type)
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
    return _global


def resolve_llm_param(
    local: str,
    global_attr: str,
    env_vars: list[str],
    default: str = "",
) -> str:
    """优先级：调用方传参 > 全局配置 > 环境变量 > 默认值。"""
    if local:
        return local
    global_val = getattr(_global, global_attr, "")
    if global_val:
        return global_val
    for var in env_vars:
        val = os.environ.get(var, "")
        if val:
            return val
    return default
