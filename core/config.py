from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.runtime_settings import apply_runtime_env_overrides, merge_runtime_config_overrides


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env(override: bool = False) -> bool:
    """Load the repository .env file into the current process."""
    loaded = load_dotenv(_PROJECT_ROOT / ".env", override=override)
    apply_runtime_env_overrides()
    return loaded


def load_config(config_path: str = "config.yaml") -> dict:
    load_project_env()
    path = Path(config_path)
    if not path.exists():
        defaults = {
            "parser": {
                "mode": "cloud",
                "cloud": {
                    "parse_method": "auto",
                    "version": "2.0",
                    "timeout": 1800,
                    "poll_interval": 3,
                    "enable_formula": True,
                    "enable_table_html": True,
                    "language": "ch",
                    "is_ocr": False,
                    "model_version": "v2",
                    "sharding": {
                        "enabled": True,
                        "min_pages": 120,
                        "min_file_mb": 80,
                        "pages_per_shard": 20,
                        "max_concurrency": 2,
                        "text_sample_pages": 5,
                    },
                },
                "oss": {"prefix": "mineru-uploads", "url_expiry": 3600},
            },
            "output": {"dir": "data/output", "encoding": "utf-8-sig"},
        }
        return merge_runtime_config_overrides(defaults)
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    return merge_runtime_config_overrides(config)


def load_pgvector_config(config_path: str = "config.yaml") -> dict:
    config = load_config(config_path)
    pgv = config.get("pgvector", {})
    return {
        "enabled": bool(pgv.get("enabled", False)),
        "default_kb_id": str(pgv.get("default_kb_id", "default")),
    }
