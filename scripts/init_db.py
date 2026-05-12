"""SQLite DB 스키마를 생성한다. 이미 존재하면 그대로 둠 (idempotent)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH
from src.db import init_schema


def main() -> int:
    init_schema()
    print(f"OK — schema ready at {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
