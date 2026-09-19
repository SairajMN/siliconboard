"""Every check that needs no docker. Toolchain checks live in check_toolchain.py."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

CHECKS = ["check_board", "check_parsers", "check_llm", "check_agents"]


def main() -> None:
    for name in CHECKS:
        importlib.import_module(name).main()
    print(f"\n{len(CHECKS)} check files passed")


if __name__ == "__main__":
    main()