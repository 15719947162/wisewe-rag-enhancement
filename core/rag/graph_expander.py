from __future__ import annotations

from typing import Any

from core.db.connection import get_db_connection

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
    if not seeds:
        return []

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            visited = set(seeds)
            frontier = [(seed, 0, 1.0, []) for seed in seeds]
            results: list[dict[str, Any]] = []
            rel_priority = INTENT_REL_PRIORITY.get(intent, INTENT_REL_PRIORITY["general"])

            while frontier and len(results) < max_neighbors:
                chunk_id, hop, weight, path = frontier.pop(0)
                if hop >= max_hops:
                    continue
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
                for dst_id, rel_type, rel_weight in cur.fetchall():
                    if dst_id in visited:
                        continue
                    next_path = [*path, {"from": chunk_id, "to": dst_id, "rel_type": rel_type, "weight": rel_weight}]
                    score = float(weight) * float(rel_weight) * (0.6**hop) * rel_priority.get(rel_type, 0.5)

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
                            mention_path = [
                                *next_path,
                                {
                                    "from": dst_id,
                                    "to": mentioned_chunk_id,
                                    "rel_type": "mentioned_in",
                                    "weight": 1.0,
                                },
                            ]
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

                    visited.add(dst_id)
                    frontier.append((dst_id, hop + 1, score, next_path))
                    results.append({"id": dst_id, "score": score, "path": next_path, "channel": "graph_expand"})
                    if len(results) >= max_neighbors:
                        break
    finally:
        conn.close()
    return results
