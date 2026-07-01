"""HTTP service entry point.

Usage:
    python backend/serve.py
    python backend/serve.py --host 0.0.0.0 --port 8000
    uvicorn backend.app:app --reload
"""
from __future__ import annotations

import argparse


def _ensure_db_schema_before_serving() -> None:
    try:
        from core.db.init_db import ensure_db_schema

        ensure_db_schema()
    except Exception as exc:
        print(f"WARN Database schema auto-init skipped: {exc}")


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="WiseWe RAG HTTP Service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    _ensure_db_schema_before_serving()
    uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
