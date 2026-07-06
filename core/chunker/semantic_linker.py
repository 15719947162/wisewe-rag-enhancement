"""
语义链接器 - 基于向量相似度建立切片关系

这个模块使用嵌入向量（embedding）计算切片之间的语义相似度，为语义相近的切片建立关联。

为什么需要语义链接？
==================
前面的链接器都是基于规则（引用、流程、因果等），但有些切片之间没有明确的规则关系，
却在语义上非常相似。比如：

切片A："机器学习是人工智能的一个分支"
切片B："机器学习属于人工智能领域"

这两句话表达的意思几乎一样，但没有引用、流程或因果关系。
通过语义链接，我们能让这两个切片关联起来，提高检索的召回率。

工作原理：
=========
1. 为每个切片计算嵌入向量（在其他模块完成）
2. 计算所有切片对之间的余弦相似度
3. 相似度超过阈值（默认0.85）的切片建立关联
4. 每个切片只保留相似度最高的topK个关联（避免关系爆炸）

关系类型：
=========
- duplicate_of: 相似度极高（>0.95），可能是重复内容
- semantic_similar: 相似度较高（>0.85），语义相关但不完全相同

性能优化：
=========
- 支持NumPy加速计算（自动检测）
- 使用分块计算避免内存爆炸
- 跳过同一章节的切片（通常已经通过其他规则关联）

使用示例：
=========
    from core.chunker.semantic_linker import link_semantic

    # 假设已经有嵌入向量
    added = link_semantic(chunks, embeddings, threshold=0.85, topk=10)
    print(f"建立了 {added} 条语义关系")
"""

import math
import os

from core.chunker.relation_utils import add_bidirectional_relation
from core.models.content_block import Chunk

# ============ 默认参数配置 ============
DEFAULT_THRESHOLD = 0.85      # 相似度阈值：超过此值才建立关系
DEFAULT_TOPK = 10             # 每个切片最多关联的相似切片数
DEFAULT_DUP_THRESHOLD = 0.95  # 重复内容阈值：超过此值认为是重复
DEFAULT_SKIP_SAME_PARENT = True  # 是否跳过同一章节的切片
DEFAULT_ENABLED = True        # 默认启用
DEFAULT_BLOCK_SIZE = 256      # NumPy分块计算时的块大小


def _cosine(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    余弦相似度 = (a·b) / (||a|| * ||b||)
    范围：[-1, 1]，越大表示越相似。

    Args:
        a: 向量A
        b: 向量B

    Returns:
        余弦相似度（-1到1之间）
    """
    denom_a = math.sqrt(sum(x * x for x in a)) or 1.0  # 向量A的模长
    denom_b = math.sqrt(sum(x * x for x in b)) or 1.0  # 向量B的模长
    return sum(x * y for x, y in zip(a, b)) / (denom_a * denom_b)


def link_semantic(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    topk: int = DEFAULT_TOPK,
    dup_threshold: float = DEFAULT_DUP_THRESHOLD,
    skip_same_parent: bool = DEFAULT_SKIP_SAME_PARENT,
    enabled: bool | None = None,
) -> int:
    """基于向量相似度建立切片关系（主入口函数）。

    工作流程：
    1. 检查是否启用（可通过环境变量控制）
    2. 尝试使用NumPy加速计算
    3. 如果NumPy不可用，降级到纯Python计算
    4. 为每个切片添加相似切片的关系

    Args:
        chunks: 切片列表
        embeddings: 嵌入向量列表，与chunks一一对应
        threshold: 相似度阈值，默认0.85（范围0-1）
        topk: 每个切片最多关联多少个相似切片，默认10
        dup_threshold: 重复内容阈值，默认0.95（超过此值认为是重复）
        skip_same_parent: 是否跳过同一章节的切片，默认True
        enabled: 是否启用，None时从环境变量读取

    Returns:
        新增的关系数量

    Raises:
        ValueError: 如果embeddings和chunks长度不匹配

    环境变量：
        LINKER_SEMANTIC_ENABLED: "false"或"off"可禁用
        LINKER_SEMANTIC_NUMPY_ENABLED: "false"可禁用NumPy加速
        LINKER_SEMANTIC_BLOCK_SIZE: NumPy分块大小，默认256
    """
    # 检查是否启用
    if enabled is None:
        enabled = os.getenv("LINKER_SEMANTIC_ENABLED", "true").lower() not in {"0", "false", "off"}
    if not enabled:
        return 0

    # 参数校验
    if len(embeddings) != len(chunks):
        raise ValueError("embeddings length must match chunks length")
    if topk <= 0:
        return 0

    # 只处理子切片
    child_pairs = [(idx, chunk) for idx, chunk in enumerate(chunks) if chunk.layer == "child"]
    if len(child_pairs) < 2:
        return 0

    # 尝试NumPy加速
    numpy_added = _link_semantic_numpy(
        child_pairs,
        embeddings,
        threshold=threshold,
        topk=topk,
        dup_threshold=dup_threshold,
        skip_same_parent=skip_same_parent,
    )
    if numpy_added is not None:
        return numpy_added

    # 降级到纯Python计算
    return _link_semantic_python(
        child_pairs,
        embeddings,
        threshold=threshold,
        topk=topk,
        dup_threshold=dup_threshold,
        skip_same_parent=skip_same_parent,
    )


def _link_semantic_python(
    child_pairs: list[tuple[int, Chunk]],
    embeddings: list[list[float]],
    *,
    threshold: float,
    topk: int,
    dup_threshold: float,
    skip_same_parent: bool,
) -> int:
    """纯Python实现的语义链接（较慢但无依赖）。

    时间复杂度：O(n²)，其中n是切片数量
    空间复杂度：O(n)

    Args:
        child_pairs: (全局索引, 切片对象) 的列表
        embeddings: 完整的嵌入向量列表
        threshold: 相似度阈值
        topk: 每个切片最多关联数
        dup_threshold: 重复阈值
        skip_same_parent: 是否跳过同父级

    Returns:
        新增的关系数量
    """
    # 预计算所有向量的模长（避免重复计算）
    norms = {
        global_idx: math.sqrt(sum(value * value for value in embeddings[global_idx])) or 1.0
        for global_idx, _chunk in child_pairs
    }

    # 收集每个切片的候选相似切片
    # candidates[i] = [(相似度, 切片位置), ...]
    candidates: list[list[tuple[float, int]]] = [[] for _global_idx, _chunk in child_pairs]

    # 计算所有切片对的相似度（只计算上三角，避免重复）
    for pos_i, (global_i, chunk_i) in enumerate(child_pairs[:-1]):
        emb_i = embeddings[global_i]
        norm_i = norms[global_i]
        for pos_j in range(pos_i + 1, len(child_pairs)):
            global_j, chunk_j = child_pairs[pos_j]
            # 跳过同一章节的切片（它们已经通过其他规则关联）
            if skip_same_parent and chunk_i.parent_id and chunk_i.parent_id == chunk_j.parent_id:
                continue
            # 计算余弦相似度
            score = sum(x * y for x, y in zip(emb_i, embeddings[global_j])) / (norm_i * norms[global_j])
            # 超过阈值才记录
            if score >= threshold:
                candidates[pos_i].append((score, pos_j))
                candidates[pos_j].append((score, pos_i))

    # 为每个切片添加topK关系
    return _add_top_semantic_relations(
        child_pairs,
        candidates,
        topk=topk,
        dup_threshold=dup_threshold,
    )


def _link_semantic_numpy(
    child_pairs: list[tuple[int, Chunk]],
    embeddings: list[list[float]],
    *,
    threshold: float,
    topk: int,
    dup_threshold: float,
    skip_same_parent: bool,
) -> int | None:
    """使用NumPy加速的语义链接（更快但需要依赖）。

    性能优势：
    - 使用矩阵运算批量计算相似度
    - 时间复杂度从O(n²)降低到接近O(n)
    - 适合处理大量切片（>1000）

    内存优化：
    - 使用分块计算避免一次性加载整个矩阵
    - 默认块大小256，可根据内存调整

    Args:
        child_pairs: (全局索引, 切片对象) 的列表
        embeddings: 完整的嵌入向量列表
        threshold: 相似度阈值
        topk: 每个切片最多关联数
        dup_threshold: 重复阈值
        skip_same_parent: 是否跳过同父级

    Returns:
        新增的关系数量，如果NumPy不可用则返回None
    """
    # 检查是否禁用NumPy
    if os.getenv("LINKER_SEMANTIC_NUMPY_ENABLED", "true").lower() in {"0", "false", "off"}:
        return None

    # 尝试导入NumPy
    try:
        import numpy as np
    except Exception:
        return None

    # 构建向量矩阵
    try:
        matrix = np.asarray([embeddings[global_idx] for global_idx, _chunk in child_pairs], dtype=np.float64)
    except (TypeError, ValueError):
        return None

    if matrix.ndim != 2:
        return None

    # 归一化（使余弦相似度 = 点积）
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    matrix = matrix / norms[:, None]

    # 预计算同父级的索引（用于过滤）
    parent_positions: dict[str, list[int]] = {}
    if skip_same_parent:
        for pos, (_global_idx, chunk) in enumerate(child_pairs):
            if chunk.parent_id:
                parent_positions.setdefault(chunk.parent_id, []).append(pos)

    # 分块计算相似度矩阵
    block_size = _semantic_block_size()
    added = 0
    for start in range(0, len(child_pairs), block_size):
        stop = min(start + block_size, len(child_pairs))
        # 计算当前块与所有向量的相似度（矩阵乘法）
        similarities = matrix[start:stop] @ matrix.T

        # 处理块内的每个切片
        for offset, scores in enumerate(similarities):
            pos_i = start + offset
            chunk_i = child_pairs[pos_i][1]

            # 创建过滤掩码
            mask = scores >= threshold
            mask[pos_i] = False  # 排除自己
            if skip_same_parent and chunk_i.parent_id:
                mask[parent_positions.get(chunk_i.parent_id, [])] = False  # 排除同父级

            # 获取候选位置
            candidate_positions = np.flatnonzero(mask)
            if candidate_positions.size == 0:
                continue

            # 选择topK个最相似的
            candidate_scores = scores[candidate_positions]
            order = np.argsort(-candidate_scores, kind="stable")[:topk]
            added += _add_semantic_relations_for_positions(
                child_pairs,
                pos_i,
                [(float(candidate_scores[item]), int(candidate_positions[item])) for item in order],
                dup_threshold=dup_threshold,
            )

    return added


def _add_top_semantic_relations(
    child_pairs: list[tuple[int, Chunk]],
    candidates: list[list[tuple[float, int]]],
    *,
    topk: int,
    dup_threshold: float,
) -> int:
    """为每个切片添加topK个最相似的语义关系。

    Args:
        child_pairs: (全局索引, 切片对象) 的列表
        candidates: 每个切片的候选列表（未排序）
        topk: 最多保留多少个
        dup_threshold: 重复阈值

    Returns:
        新增的关系数量
    """
    added = 0
    for pos_i, scored in enumerate(candidates):
        # 按相似度降序排序
        scored.sort(key=lambda item: item[0], reverse=True)
        # 只添加topK个
        added += _add_semantic_relations_for_positions(
            child_pairs,
            pos_i,
            scored[:topk],
            dup_threshold=dup_threshold,
        )
    return added


def _add_semantic_relations_for_positions(
    child_pairs: list[tuple[int, Chunk]],
    pos_i: int,
    scored: list[tuple[float, int]],
    *,
    dup_threshold: float,
) -> int:
    """为指定切片添加语义关系（底层实现）。

    关系类型判断：
    - 相似度 >= dup_threshold (0.95): duplicate_of（重复内容）
    - 相似度 >= threshold (0.85): semantic_similar（语义相似）

    Args:
        child_pairs: (全局索引, 切片对象) 的列表
        pos_i: 当前切片在child_pairs中的位置
        scored: 候选列表，每项是 (相似度, 切片位置)
        dup_threshold: 重复阈值

    Returns:
        新增的关系数量
    """
    chunk_i = child_pairs[pos_i][1]
    added = 0
    for score, pos_j in scored:
        chunk_j = child_pairs[pos_j][1]
        # 根据相似度判断关系类型
        rel_type = "duplicate_of" if score >= dup_threshold else "semantic_similar"
        before = len(chunk_i.relations)
        add_bidirectional_relation(
            chunk_i,
            chunk_j,
            rel_type=rel_type,
            weight=score,
            source="embedding",  # 标记来源为向量计算
            evidence=f"cos={score:.3f}",  # 记录相似度
        )
        if len(chunk_i.relations) > before:
            added += 1
    return added


def _semantic_block_size() -> int:
    """读取环境变量中的分块大小配置。

    用于控制NumPy计算时的内存使用。
    块越大越快，但内存占用越高。

    Returns:
        分块大小（正整数）
    """
    raw = os.getenv("LINKER_SEMANTIC_BLOCK_SIZE", str(DEFAULT_BLOCK_SIZE))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BLOCK_SIZE
    return max(1, value)
