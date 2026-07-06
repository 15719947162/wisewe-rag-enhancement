"""
知识图谱扩展模块 - 沿实体关系"顺藤摸瓜"找更多相关内容

【核心作用】
想象你在看一本书，找到了一个关键词，但只看这个词所在的段落可能不够。
这个模块就像"顺藤摸瓜"：
1. 从你找到的片段（种子）出发
2. 通过实体关系（"A 提到了 B"、"B 和 C 是兄弟节点"等）跳转
3. 找到更多虽然字面上不匹配、但语义上相关的片段

【举个例子】
用户问："如何配置数据库连接？"
1. 向量检索找到了第 5 页的"数据库配置"段落
2. 图谱扩展发现：这个段落提到了"ConnectionPool"这个实体
3. 继续查找：其他段落也提到了"ConnectionPool"
4. 于是把第 12 页关于连接池优化的内容也找出来了

【关键技术点】
- 意图感知：不同意图（查概念、查流程、查数据）对关系的权重不同
- 跳数控制：最多跳 N 步，防止跑偏太远
- 分数衰减：每跳一步，相关性打折扣（越远越不靠谱）
"""

from __future__ import annotations

from typing import Any

from core.db.connection import get_db_connection

# 不同意图下，不同关系的优先级权重
# 这就像"不同场景下，不同线索的重要性不同"
# - 概念查询：直接提到最靠谱(1.0)，兄弟概念次之(0.8)
# - 流程查询：下一步最靠谱(1.0)，上一步次之(0.9)
# - 数据查询：引用关系最靠谱(0.9)
# - 视觉内容：直接引用最靠谱(1.0)
INTENT_REL_PRIORITY = {
    "concept": {"mentions": 1.0, "sibling": 0.8, "semantic_similar": 0.7},
    "procedure": {"next_step": 1.0, "prev_step": 0.9, "sibling": 0.6},
    "data": {"refers_to": 0.9, "sibling": 0.7},
    "visual": {"refers_to": 1.0, "adjacent": 0.7},
    "general": {"adjacent": 0.6, "sibling": 0.6, "semantic_similar": 0.6},
}


def graph_expand(
    seeds: list[str],
    kb_id: str,
    intent: str,
    *,
    max_hops: int = 2,
    max_neighbors: int = 50,
) -> list[dict[str, Any]]:
    """
    从种子节点出发，沿知识图谱扩展找到更多相关片段

    就像社交网络里的"朋友的朋友"：
    - 你认识 A（种子）
    - A 认识 B（一跳）
    - B 认识 C（两跳）
    - 通过这种关系链，找到更多可能相关的人

    Args:
        seeds: 种子节点 ID 列表，通常是向量检索命中的片段
        kb_id: 知识库 ID，限定搜索范围
        intent: 用户意图（concept/procedure/data/visual/general），决定关系权重
        max_hops: 最大跳数，防止跑太远（默认 2 跳）
        max_neighbors: 最多返回多少个结果（默认 50 个）

    Returns:
        扩展出的候选片段列表，每个包含：
        - id: 片段 ID
        - score: 相关性分数（越近分数越高）
        - path: 从种子到这里的"路径"（经过了哪些关系）
        - channel: 来源标记（都是 "graph_expand"）

    实现原理：
    1. BFS 广度优先搜索（一层层向外扩散）
    2. 每跳一步，分数打折（0.6^hop，越远越不靠谱）
    3. 根据 intent 选择关系权重（概念查询优先"提到"，流程查询优先"下一步"）
    4. 遇到"实体"节点时，会额外查找"提到该实体的片段"
    """
    if not seeds:
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 已访问的节点（防止重复查找）
            visited = set(seeds)
            # 待处理队列：(节点ID, 当前跳数, 当前分数, 路径)
            frontier = [(seed, 0, 1.0, []) for seed in seeds]
            results: list[dict[str, Any]] = []
            # 根据意图选择关系优先级
            rel_priority = INTENT_REL_PRIORITY.get(intent, INTENT_REL_PRIORITY["general"])

            # BFS 广度优先搜索
            while frontier and len(results) < max_neighbors:
                chunk_id, hop, weight, path = frontier.pop(0)
                if hop >= max_hops:
                    continue

                # 查询当前节点的所有出边关系
                # 比如当前片段提到了哪些实体、连接到哪些其他片段
                cur.execute(
                    """
                    SELECT dst_id::text, rel_type, weight
                    FROM chunk_relations
                    WHERE kb_id = %s AND src_id::text = %s
                    ORDER BY weight DESC
                    LIMIT %s
                    """,
                    (kb_id, chunk_id, max_neighbors),
                )

                # 遍历所有关系边
                for dst_id, rel_type, rel_weight in cur.fetchall():
                    if dst_id in visited:
                        continue

                    # 记录路径（经过了哪些节点和关系）
                    next_path = [*path, {"from": chunk_id, "to": dst_id, "rel_type": rel_type, "weight": rel_weight}]

                    # 计算分数：上一跳分数 × 关系权重 × 距离衰减(0.6^hop) × 意图优先级
                    score = float(weight) * float(rel_weight) * (0.6**hop) * rel_priority.get(rel_type, 0.5)

                    # 特殊处理：如果关系是"mentions"（提到了某个实体）
                    # 则继续查找"提到该实体的其他片段"
                    # 这就是"顺藤摸瓜"的核心逻辑
                    if rel_type == "mentions":
                        cur.execute(
                            """
                            SELECT chunk_id::text
                            FROM entity_mentions
                            WHERE kb_id = %s AND entity_id::text = %s
                            LIMIT %s
                            """,
                            (kb_id, dst_id, max_neighbors),
                        )
                        for (mentioned_chunk_id,) in cur.fetchall():
                            if mentioned_chunk_id in visited:
                                continue
                            visited.add(mentioned_chunk_id)

                            # 构建完整路径：chunk → entity → mentioned_chunk
                            mention_path = [
                                *next_path,
                                {
                                    "from": dst_id,
                                    "to": mentioned_chunk_id,
                                    "rel_type": "mentioned_in",
                                    "weight": 1.0,
                                },
                            ]
                            # 提到同一实体的片段，分数略低
                            mention_score = score * 0.9
                            frontier.append((mentioned_chunk_id, hop + 1, mention_score, mention_path))
                            results.append(
                                {
                                    "id": mentioned_chunk_id,
                                    "score": mention_score,
                                    "path": mention_path,
                                    "channel": "graph_expand",
                                }
                            )
                            if len(results) >= max_neighbors:
                                break
                        if len(results) >= max_neighbors:
                            break
                        continue

                    # 普通关系：直接加入结果
                    visited.add(dst_id)
                    frontier.append((dst_id, hop + 1, score, next_path))
                    results.append({"id": dst_id, "score": score, "path": next_path, "channel": "graph_expand"})
                    if len(results) >= max_neighbors:
                        break
    finally:
        conn.close()
    return results
