"""
质量门控模块 - 切片质量过滤器

本模块是 RAG 管道的"质检员"，负责在切片后过滤掉低质量的内容。
就好比工厂里的质检流水线，把不合格的产品剔除出去。

【为什么需要质量门控？】
在 PDF 解析和切片过程中，会产生一些"垃圾内容"：
- 只有标点符号的片段（比如 "......" 或 "，，，，"）
- 毫无意义的碎片（比如列表符号、"见图 XX" 等）
- 内容空洞的段落（没有实际知识价值）

这些低质量内容如果进入知识库，会：
1. 浪费存储空间和向量化成本
2. 干扰检索结果（噪音变多）
3. 降低 RAG 系统的回答质量

【质量门控的工作流程】
1. 规则过滤（快速）：按标点比例判断是否为垃圾内容
2. LLM 评分（可选）：用大模型给内容打分，判断知识价值

【核心概念】
- 丢弃（Discarded）：直接剔除，不进入知识库
- 低质量（Low Quality）：分数低但可能保留（用于分析）
- 豁免（Exempt）：表格和图片切片跳过质量检查
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.http_client import create_openai_client
from core.llm_config import resolve_llm_param
from core.llm_usage import TokenUsage
from core.models.content_block import Chunk


@dataclass
class DiscardedChunk:
    """被丢弃的切片记录

    记录每个被质量门控过滤掉的切片的详细信息，方便后续分析和调试。
    就像质检员填写的"不合格产品报告单"。

    Attributes:
        chunk_index: 切片的序号（在原始列表中的位置）
        reason: 丢弃原因，用大白话解释为什么这个切片不合格
                例如："纯标点/符号（占比 95%）" 或 "LLM 质量评分不足（2/5 < 3/5）"
        preview: 切片内容的预览（前 60 个字符），方便快速查看被丢弃的内容
        score: 如果使用了 LLM 评分，记录大模型给的分数（1-5 分），默认为 0 表示未评分
    """
    chunk_index: int
    reason: str
    preview: str
    score: int = 0  # LLM score if used


@dataclass
class QualityGateResult:
    """质量门控的结果报告

    这是质量门控的"质检报告"，记录了整个过滤过程的统计信息。
    包含通过质检的切片、被丢弃的切片，以及各种统计数据。

    Attributes:
        chunks: 通过质检的切片列表，这些切片将被保留并进入后续流程（向量化、入库等）
        discarded_count: 被丢弃的切片总数（包括规则过滤和 LLM 评分不达标的）
        low_quality_count: 低质量切片数量（已废弃，目前未使用）
        details: 质检过程的详细说明列表，每条说明记录一次重要的过滤操作
                 例如：["质量门控丢弃: 5 个低质量切片"]
        discarded_chunks: 被丢弃切片的详细记录列表，每个元素都是 DiscardedChunk 对象
                          方便开发者排查问题或调优过滤规则
        scores: LLM 评分字典，记录每个切片的得分（key 是 chunk_index，value 是分数 1-5）
                如果没有启用 LLM 评分，这个字典为空
        metrics: 统计指标字典，记录 Token 使用量等性能数据
                 例如：{"qualityLlm_input_tokens": 1500, "qualityLlm_output_tokens": 300}
    """
    chunks: list[Chunk]
    discarded_count: int = 0
    low_quality_count: int = 0
    details: list[str] = field(default_factory=list)
    discarded_chunks: list[DiscardedChunk] = field(default_factory=list)
    scores: dict[int, int] = field(default_factory=dict)  # chunk_index → LLM score
    metrics: dict[str, int] = field(default_factory=dict)


def apply_quality_gate(
    chunks: list[Chunk],
    max_punct_ratio: float = 0.9,
    min_score: int = 0,
    score_only: bool = False,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
) -> QualityGateResult:
    """应用质量门控 - 过滤低质量切片

    这是质量门控的主入口函数，负责执行切片质量检查并过滤不合格的内容。
    就像安检通道，只有符合要求的切片才能通过。

    【两道关卡】
    第一关：规则过滤（快速、低成本）
        - 检查标点符号占比，如果太高就丢弃
        - 表格和图片切片直接放行（豁免检查）

    第二关：LLM 评分（可选、高精度）
        - 只有当 min_score > 0 时才启用
        - 用大模型判断内容的知识价值（1-5 分）
        - 分数低于阈值的切片会被丢弃

    【使用场景】
    1. 快速模式：只启用规则过滤（min_score=0）
       适合大规模处理，速度快、成本低
    2. 精细模式：同时启用 LLM 评分（min_score>0）
       适合高质量要求，过滤更精准，但会增加 API 调用成本
    3. 分析模式：启用评分但不丢弃（score_only=True）
       用于评估切片质量，生成报告，不实际过滤

    Args:
        chunks: 待检查的切片列表（通常是 chunker 模块的输出）
        max_punct_ratio: 标点符号占比阈值，默认 0.9（90%）
                        如果切片中 90% 以上都是标点符号/非字母数字，就丢弃
                        可以理解为：内容要有至少 10% 的"干货"
        min_score: LLM 评分的最低合格线（1-5 分），默认 0 表示不启用 LLM 评分
                  推荐值：3（基本合格）、4（较高质量）
        score_only: 是否只评分不过滤，默认 False
                   True = 给所有切片打分，但全部保留（用于质量分析）
                   False = 分数低的切片会被丢弃
        llm_base_url: LLM API 的基础 URL（可选，不填则使用环境变量）
        llm_api_key: LLM API 的密钥（可选，不填则使用环境变量）
        llm_model: LLM 模型名称（可选，默认 qwen-plus）
        llm_system_prompt: 自定义系统提示词（可选，不填则使用默认提示词）

    Returns:
        QualityGateResult: 质检结果对象，包含：
            - 通过质检的切片列表
            - 被丢弃的切片数量和详情
            - LLM 评分结果（如果启用）
            - Token 使用统计

    Example:
        # 快速模式 - 只过滤纯标点
        result = apply_quality_gate(chunks, max_punct_ratio=0.9)

        # 精细模式 - 启用 LLM 评分
        result = apply_quality_gate(chunks, min_score=3)

        # 分析模式 - 只评分不过滤
        result = apply_quality_gate(chunks, min_score=3, score_only=True)
        print(f"平均质量分: {sum(result.scores.values()) / len(result.scores):.1f}")
    """
    # ========== 第一关：规则过滤 ==========
    # 初始化结果容器
    kept: list[Chunk] = []          # 通过质检的切片
    discarded: list[DiscardedChunk] = []  # 被丢弃的切片
    details: list[str] = []         # 过程记录

    # 先对文本切片应用规则过滤（表格和图片豁免）
    text_chunks = []
    for chunk in chunks:
        # 表格和图片切片直接放行，不检查质量
        # 因为它们的"内容"可能是 HTML 或路径，不适合用文本规则判断
        if chunk.is_table_chunk or chunk.is_image_chunk:
            kept.append(chunk)
            continue

        text = chunk.content.strip()

        # 标点符号占比检查：计算非字母数字、非空格的字符比例
        # 如果大部分都是标点符号，说明这个切片没有实质内容
        punct_count = sum(1 for c in text if not c.isalnum() and not c.isspace())
        if len(text) > 0 and punct_count / len(text) > max_punct_ratio:
            # 标点占比过高，丢弃该切片
            discarded.append(DiscardedChunk(
                chunk_index=chunk.chunk_index,
                reason=f"纯标点/符号（占比 {punct_count/len(text):.0%}）",
                preview=text[:60],
            ))
            continue

        # 通过第一关规则过滤，准备进入第二关（LLM 评分）
        text_chunks.append(chunk)

    # ========== 第二关：LLM 评分（可选） ==========
    # 只有当 min_score > 0 时才启用 LLM 评分
    llm_scores: dict[int, int] = {}  # 记录每个切片的分数
    metrics: dict[str, int] = {}     # 记录 Token 使用量等指标

    if min_score > 0 and text_chunks:
        # 调用 LLM 对所有文本切片进行评分
        scored, llm_metrics = _llm_score_chunks(
            text_chunks, min_score,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_system_prompt=llm_system_prompt,
        )
        metrics.update(llm_metrics)

        # 根据评分结果决定是否保留切片
        for chunk, score in scored:
            llm_scores[chunk.chunk_index] = score

            if score_only:
                # 分析模式：只记录分数，不丢弃任何切片
                kept.append(chunk)
            elif score < min_score:
                # 分数不达标，丢弃切片
                discarded.append(DiscardedChunk(
                    chunk_index=chunk.chunk_index,
                    reason=f"LLM 质量评分不足（{score}/5 < {min_score}/5）",
                    preview=chunk.content[:60],
                    score=score,
                ))
            else:
                # 分数达标，保留切片
                kept.append(chunk)
    else:
        # 未启用 LLM 评分，所有通过规则过滤的切片都保留
        kept.extend(text_chunks)

    # ========== 生成结果报告 ==========
    # 统计被丢弃的切片数量
    n_discarded = len(discarded)
    if n_discarded:
        details.append(f"质量门控丢弃: {n_discarded} 个低质量切片")

    # 返回质检结果
    return QualityGateResult(
        chunks=kept,                 # 通过质检的切片
        discarded_count=n_discarded, # 被丢弃的数量
        details=details,             # 过程记录
        discarded_chunks=discarded,  # 被丢弃的详情（用于调试）
        scores=llm_scores,           # LLM 评分结果
        metrics=metrics,             # Token 使用统计
    )


def _llm_score_chunks(
    chunks: list[Chunk],
    min_score: int,
    llm_base_url: str = "",
    llm_api_key: str = "",
    llm_model: str = "",
    llm_system_prompt: str = "",
) -> tuple[list[tuple[Chunk, int]], dict[str, int]]:
    """使用 LLM 对切片进行质量评分（内部函数）

    这是质量门控的"高级质检员"，用大模型来判断切片的知识价值。
    比简单的规则过滤更智能，但也会消耗 API 调用成本。

    【评分标准（1-5 分）】
    5分（优秀）：内容完整、信息丰富、有明确知识点
        例如："机器学习是人工智能的一个分支，它使计算机能够从数据中学习..."

    4分（良好）：有一定价值，信息基本完整
        例如："深度学习是机器学习的子领域，使用神经网络进行特征学习。"

    3分（合格）：内容有限，但有一定参考价值
        例如："详见第 5 章内容。" 或 "如图所示。"

    2分（较差）：信息碎片化，价值较低
        例如："（续上表）" 或 "......"

    1分（不合格）：无实质内容
        例如："•" 或 "，，，" 或 "注："

    【工作流程】
    1. 配置检查：从环境变量或参数获取 API Key、模型等配置
    2. 批量处理：每批处理 10 个切片（节省 API 调用次数）
    3. LLM 调用：将切片内容发送给大模型，要求返回 JSON 格式的评分
    4. 异常处理：如果 LLM 调用失败，给该批次切片打默认分（3 分）
    5. 统计汇总：记录 Token 使用量，返回评分结果

    Args:
        chunks: 待评分的切片列表（已经过规则过滤，不含表格/图片）
        min_score: 最低合格分数（1-5），低于此分数的切片将被标记为低质量
        llm_base_url: LLM API 基础 URL（可选）
        llm_api_key: LLM API 密钥（可选）
        llm_model: LLM 模型名称（可选，默认 qwen-plus）
        llm_system_prompt: 自定义系统提示词（可选）

    Returns:
        tuple: 包含两个元素：
            - list[tuple[Chunk, int]]: 切片和分数的配对列表
              例如：[(chunk1, 5), (chunk2, 3), (chunk3, 4)]
            - dict[str, int]: Token 使用统计
              例如：{"qualityLlm_input_tokens": 1500, "qualityLlm_output_tokens": 300}

    Note:
        - 如果没有配置 API Key，所有切片都会被评 5 分（默认通过）
        - 每个切片只发送前 300 个字符给 LLM（节省 Token）
        - 批量大小为 10，即一次 API 调用评分 10 个切片
        - 默认使用 qwen-plus 模型，可通过环境变量 LLM_CLEANER_MODEL 修改
    """
    import json

    # ========== 第一步：解析配置参数 ==========
    # 按优先级获取 API Key：函数参数 > 环境变量
    api_key = resolve_llm_param(
        llm_api_key, "api_key",
        ["LLM_API_KEY", "DASHSCOPE_API_KEY"],
    )
    if not api_key:
        # 没有配置 API Key，直接给所有切片打高分（默认通过）
        return [(c, 5) for c in chunks], {}

    # 按优先级获取 Base URL：函数参数 > 环境变量 > 默认值（DashScope）
    base_url = resolve_llm_param(
        llm_base_url, "base_url",
        ["LLM_BASE_URL"],
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    # 按优先级获取模型名称：函数参数 > 环境变量 > 默认值（qwen-plus）
    model = resolve_llm_param(
        llm_model, "model",
        ["LLM_CLEANER_MODEL"],
        "qwen-plus",
    )

    # 按优先级获取系统提示词：函数参数 > 环境变量 > 默认提示词
    system_prompt = llm_system_prompt or resolve_llm_param(
        "", "quality_gate_system_prompt", ["LLM_QUALITY_GATE_SYSTEM_PROMPT"],
        "",
    ) or resolve_llm_param(
        "", "system_prompt", [],
        (
            "你是知识库质量评估助手。对每个文本片段评估其作为检索知识库条目的价值，打分 1-5：\n"
            "5分：内容完整、信息丰富、有明确知识点\n"
            "4分：有一定价值，信息基本完整\n"
            "3分：内容有限，但有一定参考价值\n"
            "2分：信息碎片化，价值较低\n"
            "1分：无实质内容（列表符号、空洞描述、无意义片段）\n\n"
            "返回 JSON 数组，每项：{\"index\": 序号, \"score\": 分数, \"reason\": \"一句话理由\"}"
        ),
    )

    # ========== 第二步：创建 LLM 客户端 ==========
    try:
        client = create_openai_client(api_key=api_key, base_url=base_url)
    except ImportError:
        # 如果缺少依赖库，默认给所有切片打中等分数
        return [(c, 5) for c in chunks], {}

    # ========== 第三步：批量调用 LLM 进行评分 ==========
    batch_size = 10  # 每批处理 10 个切片，平衡 API 调用次数和响应延迟
    results: dict[int, int] = {}  # 存储评分结果：{切片在批次中的索引 -> 分数}
    token_usage = TokenUsage()    # 统计 Token 使用量

    # 分批处理切片
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]  # 取出当前批次的切片

        # 构建评分请求数据
        # 每个切片只发送前 300 个字符（节省 Token，大部分情况已足够判断质量）
        items = [{"index": j, "text": c.content[:300]} for j, c in enumerate(batch)]

        try:
            # 调用 LLM API 进行评分
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},  # 评分标准提示词
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},  # 待评分的切片列表
                ],
                temperature=0,  # 温度设为 0，确保评分结果稳定可重复
            )
            token_usage.add_response(response)  # 记录 Token 使用量

            # 解析 LLM 返回的评分结果
            content = response.choices[0].message.content or "[]"
            content = content.strip()

            # 如果 LLM 返回了代码块格式（如 ```json ... ```），需要提取其中的 JSON
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            scored = json.loads(content)  # 解析 JSON 数组

            # 将评分结果存入字典（索引需要加上批次偏移量）
            for item in scored:
                results[i + item["index"]] = item.get("score", 3)  # 默认分数为 3

        except Exception:
            # 如果 LLM 调用失败或解析出错，给该批次所有切片打中等分数（3 分）
            # 这样可以避免因为个别切片问题导致整个流程失败
            for j in range(len(batch)):
                results[i + j] = 3

    # ========== 第四步：返回结果 ==========
    # 将切片和分数配对，分数默认为 3（中等质量）
    return [(chunk, results.get(idx, 3)) for idx, chunk in enumerate(chunks)], token_usage.to_metrics("qualityLlm")
