"""
RAG 评分模块 - 评估检索和回答的质量

【为什么需要评分】
RAG 系统的效果好不好，需要量化指标：
1. 相关性：检索到的片段和问题有多相关？
2. 忠实度：生成的回答是否真的来自检索片段？还是 LLM 在胡编？

【两种评分方式】
1. 规则评分（_rule_score）
   - 相关性：检索片段的平均分数
   - 忠实度：回答引用了多少片段
   - 优点：快、可解释、不花钱
   - 缺点：不够智能

2. LLM 评分（_llm_score）
   - 让 LLM 看问答对，打 1-5 分
   - 优点：更智能
   - 缺点：慢、花钱

【应用场景】
- 质量监控：评分过低时报警
- A/B 测试：对比不同检索策略
- 用户反馈：评分低时让用户确认
"""

from __future__ import annotations

import re

from core.llm_usage import extract_response_usage


def _rule_score(contexts: list[dict], answer_dict: dict) -> dict:
    """
    规则评分：根据检索分数和引用情况计算质量分数

    【相关性分数】
    检索片段的平均分数
    分数越高，说明检索越精准

    【忠实度分数】
    回答中引用的片段数量 / 总片段数量
    如果 LLM 回答时引用了很多片段，说明回答有据可依
    如果 LLM 标记"无法回答"或没有引用，说明检索片段不够

    Args:
        contexts: 检索到的片段列表（包含分数、ID 等）
        answer_dict: LLM 生成的回答（包含 answer, citations, cannot_answer）

    Returns:
        评分结果字典：
        - relevance_score: 相关性分数（0-1）
        - faithfulness_score: 忠实度分数（0-1）
        - llm_score: None（规则评分没有 LLM 评分）
        - details: 详细信息（平均分、引用数等）
    """
    # 相关性分数：取 rerank_score（如果有）或 dense_score
    # rerank_score 是重排序后的分数，更准确
    # BM25 单路命中的片段没有 dense_score，所以优先用 rerank_score
    relevance_scores = [
        float(context.get("rerank_score") or context.get("dense_score") or 0.0)
        for context in contexts
    ]
    relevance_score = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0

    # 忠实度分数：看回答引用了多少片段
    citations = answer_dict.get("citations", []) or []
    cited_count = len(citations)
    total_count = len(contexts)

    # 特殊情况：无法回答或没有片段
    if total_count <= 0 or answer_dict.get("cannot_answer", False):
        faithfulness_score = 0.0
        valid_cited_count = 0
    else:
        # 检查引用是否有效（引用的片段 ID 或索引是否存在）
        context_ids = {str(context.get("id", "")) for context in contexts if context.get("id")}
        valid_indices = set(range(1, total_count + 1))
        valid_cited_count = 0
        for citation in citations:
            chunk_id = str(citation.get("chunk_id", "") or "")
            raw_index = citation.get("index", None)
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                index = 0
            # 引用有效：要么 ID 匹配，要么索引在范围内
            if (chunk_id and chunk_id in context_ids) or index in valid_indices:
                valid_cited_count += 1

        # 忠实度 = 有效引用数 / 总引用数
        faithfulness_score = valid_cited_count / cited_count if cited_count > 0 else 0.0

    return {
        "relevance_score": relevance_score,
        "faithfulness_score": faithfulness_score,
        "llm_score": None,
        "details": {
            "avg_dense_score": relevance_score,
            "cited_chunks": cited_count,
            "valid_cited_chunks": valid_cited_count,
            "total_chunks": total_count,
        },
    }


def _llm_score(query: str, answer: str) -> tuple[float | None, dict[str, int]]:
    """
    LLM 评分：让 LLM 对问答质量打分

    原理：
    1. 构造提示词：把问题和答案发给 LLM
    2. 让 LLM 打 1-5 分
    3. 解析 LLM 返回的数字

    注意：
    - 答案只取前 500 字，避免 token 太多
    - 提示词要简单明确，让 LLM 只返回数字

    Args:
        query: 用户问题
        answer: LLM 生成的回答

    Returns:
        (分数, token 使用量)
        - 分数：0.2, 0.4, 0.6, 0.8, 1.0（对应 1-5 分归一化）
        - token 使用量：prompt_tokens, completion_tokens, total_tokens
    """
    # 获取 LLM 客户端
    try:
        from core.rag.generator import _get_rag_llm_client, _get_rag_llm_model

        client = _get_rag_llm_client()
        model = _get_rag_llm_model()
    except Exception:
        return None, {}

    # 构造提示词
    prompt = (
        "请对以下问答质量打分（1-5分整数）：\n"
        f"问题：{query}\n"
        f"答案：{answer[:500]}\n"  # 只取前 500 字
        "只返回数字 1-5，不要其他内容"
    )

    try:
        from core.llm_config import resolve_llm_param
        system_prompt = resolve_llm_param("", "system_prompt", [])
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # 调用 LLM
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,  # 低温度，让输出更确定
        )
        usage = extract_response_usage(response)
        text = response.choices[0].message.content or ""
    except Exception:
        return None, {}

    # 解析分数（从返回文本中提取 1-5 的数字）
    match = re.search(r"[1-5]", text)
    if not match:
        return None, {}

    # 归一化到 0-1 范围
    return float(match.group()) / 5.0, {
        "scoreRequests": 1,
        "scorePromptTokens": usage["prompt_tokens"],
        "scoreCompletionTokens": usage["completion_tokens"],
        "scoreTotalTokens": usage["total_tokens"],
    }


class RAGScorer:
    """
    RAG 评分器 - 统一的评分接口

    使用方法：
    ```python
    scorer = RAGScorer()
    result = scorer.score(
        query="如何配置数据库？",
        contexts=[{"dense_score": 0.8, "id": "chunk_1"}, ...],
        answer_dict={"answer": "...", "citations": [...]},
        use_llm_score=True  # 可选，是否使用 LLM 评分
    )
    ```
    """

    def score(
        self,
        query: str,
        contexts: list[dict],
        answer_dict: dict,
        use_llm_score: bool = False,
    ) -> dict:
        """
        计算检索和回答的质量分数

        Args:
            query: 用户问题
            contexts: 检索到的片段列表
            answer_dict: LLM 生成的回答字典
            use_llm_score: 是否使用 LLM 评分（默认 False）

        Returns:
            评分结果：
            - relevance_score: 相关性（0-1）
            - faithfulness_score: 忠实度（0-1）
            - llm_score: LLM 评分（0-1，可选）
            - llm_usage: LLM token 使用量（可选）
            - details: 详细信息
        """
        # 先用规则评分
        result = _rule_score(contexts, answer_dict)

        # 可选：使用 LLM 评分
        if use_llm_score:
            try:
                llm_result = _llm_score(query, answer_dict.get("answer", ""))
                if isinstance(llm_result, tuple):
                    score, usage = llm_result
                else:
                    score, usage = llm_result, {}
                result["llm_score"] = score
                result["llm_usage"] = usage
            except Exception:
                result["llm_score"] = None
                result["llm_usage"] = {}

        return result
