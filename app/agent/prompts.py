from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT

PROMPT_DIR = PROJECT_ROOT / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (Path(PROMPT_DIR) / name).read_text(encoding="utf-8").strip()
