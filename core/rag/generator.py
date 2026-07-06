“””
RAG 答案生成模块

本模块实现了基于检索增强生成（Retrieval-Augmented Generation）的答案生成功能。

答案生成原理：
================
1. 接收检索到的上下文片段（contexts）和用户问题（query）
2. 构建 System Prompt，约束 LLM 只从上下文中提取答案，不能编造
3. 构建 User Prompt，将上下文片段编号后提供给 LLM
4. 调用 LLM 生成答案，要求标注引用编号 [1][2]...
5. 解析 LLM 输出，提取引用信息并构建结构化响应

答案生成流程：
================
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  检索上下文   │ -> │  构建 Prompt  │ -> │  LLM 生成    │
└──────────────┘    └──────────────┘    └──────────────┘
                                               │
                                               v
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  返回结果     │ <- │  后备提取     │ <- │  解析引用     │
└──────────────┘    └──────────────┘    └──────────────┘

核心设计：
================
- 严格约束：LLM 只能从上下文提取答案，避免幻觉
- 引用标注：答案中必须标注来源编号 [N]
- 后备机制：当 LLM 无法回答但存在高分上下文时，使用抽取式后备方案
- 多环境支持：支持多种 API Key 配置方式（RAG_LLM_API_KEY、LLM_API_KEY 等）
“””

from __future__ import annotations

import os
import re
from typing import Any

import openai

from core.http_client import create_openai_client
from core.llm_usage import extract_response_usage

# =============================================================================
# RAG 系统提示词
# =============================================================================
# 这个提示词定义了 LLM 的角色和约束：
# 1. 角色定义：基于文档的问答助手
# 2. 行为约束：只能从上下文提取信息，不能编造
# 3. 引用格式：使用 [数字] 格式标注来源
# 4. 默认回复：信息不足时返回标准回复
# 5. 来源列出：答案末尾必须列出参考来源
RAG_SYSTEM_PROMPT = “””你是一个严格基于文档的问答助手。
规则：
1. 只能从提供的上下文中提取答案，不得编造任何信息。
2. 引用来源时使用 [数字] 格式，如 [1][2]。
3. 如果上下文中没有足够信息回答问题，回答：”根据现有文档无法回答该问题”。
4. 答案末尾必须列出参考来源，格式：[N] 文档名，位置。”””

# 无法回答时的标准回复文本
_CANNOT_ANSWER_TEXT = “根据现有文档无法回答该问题”


# =============================================================================
# 配置获取函数
# =============================================================================

def _get_rag_system_prompt() -> str:
    """
    获取 RAG 系统提示词。

    配置优先级：
    1. 环境变量 RAG_SYSTEM_PROMPT（通过 resolve_llm_param）
    2. 环境变量 SYSTEM_PROMPT（通用系统提示词）
    3. 模块默认值 RAG_SYSTEM_PROMPT

    Returns:
        str: 最终使用的系统提示词
    """
    from core.llm_config import resolve_llm_param

    return (
        resolve_llm_param("", "rag_system_prompt", ["RAG_SYSTEM_PROMPT"])
        or resolve_llm_param("", "system_prompt", [])
        or RAG_SYSTEM_PROMPT
    )


def _get_rag_llm_client() -> openai.OpenAI:
    """
    创建并返回 RAG LLM 客户端。

    API Key 解析优先级：
    1. RAG_LLM_API_KEY / RAG_LLM_BASE_URL - RAG 专用配置
    2. LLM_API_KEY / LLM_BASE_URL - 通用 LLM 配置
    3. DASHSCOPE_API_KEY - 阿里云 DashScope，自动设置 base URL
    4. OPENAI_API_KEY - OpenAI 默认配置

    Returns:
        openai.OpenAI: 配置好的 OpenAI 兼容客户端

    Raises:
        ValueError: 没有配置任何 API Key 时抛出
    """
    # 读取各种环境变量配置
    rag_key = os.environ.get("RAG_LLM_API_KEY", "").strip()
    rag_url = os.environ.get("RAG_LLM_BASE_URL", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()
    llm_url = os.environ.get("LLM_BASE_URL", "").strip()
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    # 按优先级选择 API Key
    key = rag_key or llm_key or dashscope_key or openai_key
    if not key:
        raise ValueError("No RAG LLM API key configured.")

    # 根据 Key 类型确定 base_url
    base_url = ""
    if rag_key:
        base_url = rag_url
    elif llm_key:
        base_url = llm_url
    elif dashscope_key:
        # DashScope 使用 OpenAI 兼容模式
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 创建客户端
    kwargs: dict[str, Any] = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return create_openai_client(**kwargs)


def _get_rag_llm_model() -> str:
    """
    获取 RAG LLM 模型名称。

    模型名称解析优先级：
    1. 环境变量 RAG_LLM_MODEL - RAG 专用模型
    2. 环境变量 LLM_CLEANER_MODEL - 清洗模型（复用）
    3. 默认值 "qwen-max"

    Returns:
        str: 模型名称
    """
    return (
        os.environ.get("RAG_LLM_MODEL", "").strip()
        or os.environ.get("LLM_CLEANER_MODEL", "").strip()
        or "qwen-max"
    )


# =============================================================================
# 辅助函数
# =============================================================================

def _format_location(context: dict) -> str:
    """
    格式化上下文位置信息。

    将页码和切片索引格式化为易读的位置字符串。

    Args:
        context: 上下文字典，包含 page 和 chunk_index 字段

    Returns:
        str: 格式化的位置字符串，如 "P.5" 或 "P.5 · #3"

    Examples:
        >>> _format_location({"page": 5})
        "P.5"
        >>> _format_location({"page": 5, "chunk_index": 2})
        "P.5 · #3"
    """
    page = int(context.get("page", 0) or 0)
    chunk_index = context.get("chunk_index", None)
    if chunk_index is None:
        return f"P.{page}"
    return f"P.{page} · #{int(chunk_index) + 1}"


# =============================================================================
# Prompt 构建函数
# =============================================================================

def _build_rag_prompt(query: str, contexts: list[dict]) -> tuple[str, str]:
    """
    构建 RAG Prompt（系统提示词和用户提示词）。

    Prompt 构建逻辑：
    =================
    1. 为每个上下文片段分配编号 [1], [2], ...
    2. 提取上下文内容（context_window 或 content 字段）
    3. 截断到 500 字符，避免 Prompt 过长
    4. 添加来源和位置信息
    5. 拼接成格式化的用户提示词

    输出格式示例：
    =================
    [1] 来源：文档A.pdf，位置：P.5 · #2
    这是第一个上下文片段的内容...

    [2] 来源：文档B.pdf，位置：P.10
    这是第二个上下文片段的内容...

    Args:
        query: 用户问题
        contexts: 检索到的上下文列表，每个上下文包含：
                  - context_window 或 content: 文本内容
                  - document_name 或 source: 文档名
                  - page: 页码
                  - chunk_index: 切片索引（可选）

    Returns:
        tuple[str, str]: (系统提示词, 用户提示词)
    """
    context_blocks: list[str] = []
    for index, context in enumerate(contexts, start=1):
        # 提取文本内容（优先使用 context_window，其次 content）
        text = (context.get("context_window") or context.get("content") or "")[:500]
        # 提取来源文档名
        source = context.get("document_name") or context.get("source", "")
        # 构建带编号的上下文块
        context_blocks.append(f"[{index}] 来源：{source}，位置：{_format_location(context)}\n{text}")

    # 拼接所有上下文块
    joined_contexts = "\n\n".join(context_blocks)

    # 构建用户提示词
    user_prompt = (
        f"问题：{query}\n\n"
        "上下文：\n"
        f"{joined_contexts}\n\n"
        "请基于以上上下文回答问题，并在答案中标注引用编号 [1][2]。"
    )
    return _get_rag_system_prompt(), user_prompt


# =============================================================================
# 答案解析函数
# =============================================================================

def _parse_answer_with_citations(raw_answer: str, contexts: list[dict]) -> dict:
    """
    解析 LLM 生成的答案，提取引用信息。

    解析流程：
    =================
    1. 使用正则表达式提取答案中的 [数字] 引用标记
    2. 根据编号找到对应的上下文
    3. 构建结构化的引用信息列表
    4. 检测是否包含"无法回答"标记

    Args:
        raw_answer: LLM 生成的原始答案文本
        contexts: 传入的上下文列表（用于匹配引用）

    Returns:
        dict: 解析后的结果，包含：
              - answer: 原始答案文本
              - citations: 引用列表，每个引用包含：
                           - index: 引用编号
                           - source: 来源文档名
                           - document_name: 同 source
                           - document_id: 文档 ID
                           - page: 页码
                           - chunk_index: 切片索引
                           - location: 格式化的位置字符串
                           - snippet: 引用的文本片段（前100字符）
                           - chunk_id: 切片 ID
              - cannot_answer: 是否无法回答（包含标准回复文本）

    Examples:
        >>> answer = "根据文档，答案是 A [1]。"
        >>> contexts = [{"source": "doc.pdf", "page": 1}]
        >>> result = _parse_answer_with_citations(answer, contexts)
        >>> result["citations"][0]["source"]
        "doc.pdf"
    """
    seen: set[int] = set()
    citations = []

    # 提取所有 [数字] 格式的引用
    for match in re.findall(r"\[(\d+)\]", raw_answer):
        index = int(match)
        # 跳过重复引用和越界引用
        if index in seen or index - 1 >= len(contexts):
            continue
        seen.add(index)
        context = contexts[index - 1]

        # 提取引用片段
        snippet_source = context.get("context_window") or context.get("content", "")
        source = context.get("document_name") or context.get("source", "")

        # 构建引用信息
        citations.append(
            {
                "index": index,
                "source": source,
                "document_name": source,
                "document_id": context.get("document_id", ""),
                "page": context.get("page", 0),
                "chunk_index": context.get("chunk_index", None),
                "location": _format_location(context),
                "snippet": snippet_source[:100],
                "chunk_id": context.get("id", ""),
            }
        )

    return {
        "answer": raw_answer,
        "citations": citations,
        "cannot_answer": _CANNOT_ANSWER_TEXT in raw_answer,
    }


# =============================================================================
# 后备提取机制
# =============================================================================

def _context_score(context: dict) -> float:
    """
    获取上下文的置信度分数。

    分数来源优先级：
    1. rerank_score - 重排序分数（最准确）
    2. score - 通用分数
    3. dense_score - 稠密向量分数

    Args:
        context: 上下文字典

    Returns:
        float: 置信度分数，范围 0-1
    """
    return float(context.get("rerank_score") or context.get("score") or context.get("dense_score") or 0.0)


def _build_extract_answer_from_contexts(query: str, contexts: list[dict], min_score: float = 0.65) -> dict | None:
    """
    从上下文中抽取式构建答案（后备方案）。

    后备机制触发条件：
    =================
    1. LLM 返回"无法回答"，但存在高分上下文
    2. LLM 答案中没有有效引用
    3. LLM 调用失败（异常情况）

    抽取逻辑：
    =================
    1. 筛选置信度 >= min_score 的上下文
    2. 从问题中提取关键词（中文词语、英文单词）
    3. 将上下文按句子分割
    4. 对每个句子打分（关键词命中数 + 上下文分数）
    5. 选择每个高分上下文中得分最高的句子
    6. 拼接成归纳式答案

    Args:
        query: 用户问题
        contexts: 上下文列表
        min_score: 最小置信度阈值，默认 0.65

    Returns:
        dict | None: 抽取式答案结果，如果没有高分上下文则返回 None
                     结构与 _parse_answer_with_citations 相同
    """
    strong_contexts = [
        (index, context)
        for index, context in enumerate(contexts, start=1)
        if _context_score(context) >= min_score
    ]
    if not strong_contexts:
        return None

    query_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", query))

    def sentence_score(sentence: str, context: dict) -> tuple[int, float, int]:
        hit_count = sum(1 for term in query_terms if term and term in sentence)
        return hit_count, _context_score(context), len(sentence)

    evidence: list[tuple[int, str]] = []
    for index, context in strong_contexts[:4]:
        text = (context.get("context_window") or context.get("content") or "").strip()
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])", text) if item.strip()]
        if not sentences and text:
            sentences = [text[:220]]
        if not sentences:
            continue
        best = max(sentences, key=lambda sentence: sentence_score(sentence, context))
        evidence.append((index, best[:220]))

    if not evidence:
        return None

    answer_lines = [f"根据当前召回证据，{query}可归纳为："]
    for index, sentence in evidence[:3]:
        answer_lines.append(f"- {sentence} [{index}]")
    parsed = _parse_answer_with_citations("\n".join(answer_lines), contexts)
    if not parsed["citations"]:
        return None
    parsed["cannot_answer"] = False
    parsed["fallback"] = "extractive_high_confidence"
    return parsed


# =============================================================================
# RAG 生成器类
# =============================================================================

class RAGGenerator:
    """
    RAG 答案生成器。

    这是 RAG 流程的核心类，负责基于检索到的上下文生成答案。

    主要功能：
    =================
    1. 调用 LLM 生成答案
    2. 解析答案中的引用
    3. 后备提取机制（当 LLM 无法回答时）
    4. Token 使用统计

    使用示例：
    =================
    >>> generator = RAGGenerator()
    >>> result = generator.generate(
    ...     query="什么是 RAG？",
    ...     contexts=[
    ...         {"content": "RAG 是检索增强生成技术...", "source": "doc.pdf", "page": 1}
    ...     ]
    ... )
    >>> print(result["answer"])
    """

    def generate(
        self,
        query: str,
        contexts: list[dict],
        temperature: float = 0.1,
    ) -> dict:
        """
        生成答案。

        生成流程：
        =================
        1. 检查上下文是否为空（空则返回无法回答）
        2. 构建 System Prompt 和 User Prompt
        3. 调用 LLM 生成答案
        4. 解析答案中的引用
        5. 如果 LLM 无法回答或无引用，尝试后备提取
        6. 返回结构化结果

        后备机制触发条件：
        =================
        - LLM 返回"无法回答"，但存在高分上下文
        - LLM 答案中没有有效引用
        - LLM 调用异常

        Args:
            query: 用户问题
            contexts: 检索到的上下文列表，每个上下文应包含：
                      - content 或 context_window: 文本内容
                      - source 或 document_name: 文档名
                      - page: 页码
                      - chunk_index: 切片索引（可选）
                      - rerank_score / score / dense_score: 置信度分数（可选）
            temperature: LLM 温度参数，默认 0.1（低温度保证答案稳定）

        Returns:
            dict: 生成结果，包含：
                  - answer: 生成的答案文本
                  - citations: 引用列表，每个引用包含来源信息
                  - cannot_answer: 是否无法回答
                  - llm_usage: Token 使用统计（如果 LLM 调用成功）
                  - fallback: 如果使用了后备提取，标记为 "extractive_high_confidence"
                  - error: 错误信息（如果发生异常）
        """
        # 空上下文检查
        if not contexts:
            return {
                "answer": _CANNOT_ANSWER_TEXT,
                "citations": [],
                "cannot_answer": True,
            }

        try:
            # 构建 Prompt
            system_prompt, user_prompt = _build_rag_prompt(query, contexts)

            # 获取 LLM 客户端和模型
            client = _get_rag_llm_client()
            model = _get_rag_llm_model()

            # 调用 LLM 生成答案
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )

            # 提取 Token 使用统计
            usage = extract_response_usage(response)
            raw_answer = response.choices[0].message.content or ""

            # 解析答案中的引用
            parsed = _parse_answer_with_citations(raw_answer, contexts)

            # 添加 Token 使用统计
            parsed["llm_usage"] = {
                "generateRequests": 1,
                "generatePromptTokens": usage["prompt_tokens"],
                "generateCompletionTokens": usage["completion_tokens"],
                "generateTotalTokens": usage["total_tokens"],
            }

            # 后备机制：LLM 返回无法回答
            if parsed["cannot_answer"]:
                fallback = _build_extract_answer_from_contexts(query, contexts)
                if fallback:
                    fallback["llm_usage"] = parsed["llm_usage"]
                    return fallback

            # 后备机制：LLM 答案中没有有效引用
            if not parsed["citations"]:
                fallback = _build_extract_answer_from_contexts(query, contexts)
                if fallback:
                    fallback["llm_usage"] = parsed["llm_usage"]
                    return fallback

            return parsed

        except Exception as exc:
            # 异常时尝试后备提取
            fallback = _build_extract_answer_from_contexts(query, contexts)
            if fallback:
                fallback["error"] = str(exc)
                return fallback

            # 后备提取也失败，返回无法回答
            return {
                "answer": _CANNOT_ANSWER_TEXT,
                "citations": [],
                "cannot_answer": True,
                "error": str(exc),
            }
