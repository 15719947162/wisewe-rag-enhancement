from __future__ import annotations

import argparse
import json
from collections import defaultdict

from core.db.connection import get_db_connection


def migrate_for_kb(conn, kb_id: str, dry_run: bool) -> int:
    parent_map: dict[str, str | None] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, parent_id FROM chunks WHERE kb_id=%s", (kb_id,))
        for chunk_id, parent_id in cur.fetchall():
            parent_map[str(chunk_id)] = str(parent_id) if parent_id else None

    migrated = 0
    with conn.cursor() as cur:
        cur.execute("SELECT id, related_ids FROM chunks WHERE kb_id=%s", (kb_id,))
        rows = cur.fetchall()
        for chunk_id, related_ids_raw in rows:
            if not related_ids_raw:
                continue
            try:
                related_ids = json.loads(related_ids_raw)
            except Exception:
                continue
            for target_id in related_ids:
                rel_type = "adjacent"
                if parent_map.get(str(chunk_id)) and parent_map.get(str(chunk_id)) == parent_map.get(str(target_id)):
                    rel_type = "sibling"
                migrated += 1
                if dry_run:
                    continue
                cur.execute(
                    """
                    INSERT INTO chunk_relations(kb_id, src_id, dst_id, rel_type, weight, source, evidence)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(kb_id, src_id, dst_id, rel_type) DO NOTHING
                    """,
                    (kb_id, chunk_id, target_id, rel_type, 1.0, "rule", "legacy migration"),
                )
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if args.all:
                cur.execute("SELECT id FROM knowledge_bases")
                kb_ids = [row[0] for row in cur.fetchall()]
            elif args.kb_id:
                kb_ids = [args.kb_id]
            else:
                raise SystemExit("Either --kb-id or --all is required")

        total = 0
        for kb_id in kb_ids:
            count = migrate_for_kb(conn, kb_id, args.dry_run)
            print(f"{kb_id}: {count} relations")
            total += count
        if not args.dry_run:
            conn.commit()
        print(f"total: {total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
