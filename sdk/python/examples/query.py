from __future__ import annotations

import argparse
import json

from wisewe_rag_client import WiseWeRagClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Call WiseWe RAG OpenAPI with the Python SDK.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--graph", action="store_true")
    args = parser.parse_args()

    client = WiseWeRagClient(base_url=args.base_url, api_key=args.api_key)
    if args.graph:
        result = client.graph_query(kb_id=args.kb_id, query=args.query)
    else:
        result = client.query(kb_id=args.kb_id, query=args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
