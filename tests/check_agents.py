"""Agent behaviour that needs no API key and no docker."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.spec import SpecAgent
from board import Board


def main() -> None:
    agent = SpecAgent()
    assert agent.name == "spec_agent"
    assert agent.system.strip(), "every agent needs a system prompt"

    board = Board()
    result = agent.run(board)
    assert result.ok is False, result
    assert "request is empty" in (result.err or ""), result
    assert board.spec is None, "a failed extraction must not write a spec onto the Board"
    print("agents: spec agent refuses an empty request without touching the network")


if __name__ == "__main__":
    main()