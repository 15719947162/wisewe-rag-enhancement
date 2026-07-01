"""RAG Knowledge Base Builder - Compatibility shim.

New entry points:
    HTTP service:  python backend/serve.py [--host HOST] [--port PORT]
    CLI pipeline:  python backend/cli.py --pdf FILE [--strategy STRATEGY] [--clean] [--clean-llm]

This file is kept for backwards compatibility only.
"""
from __future__ import annotations

import sys


def _configure_utf8_io() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def main() -> None:
    _configure_utf8_io()

    if "--serve-api" in sys.argv:
        argv = [a for a in sys.argv[1:] if a != "--serve-api"]
        sys.argv = [sys.argv[0]] + argv
        print("[legacy] --serve-api detected; delegating to backend/serve.py", file=sys.stderr)
        from backend.serve import main as serve_main
        serve_main()
        return

    from backend.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
