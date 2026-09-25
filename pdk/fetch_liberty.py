"""Fetch the sky130hd Liberty once, cache it in pdk/, and print the first line as proof."""

from __future__ import annotations

import ssl
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm import _CAFILE

URL = (
    "https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/"
    "master/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
)
TARGET = Path(__file__).resolve().parent / "sky130hd_tt_025C_1v80.lib"


def main() -> None:
    context = ssl.create_default_context(cafile=_CAFILE)
    if TARGET.exists() and TARGET.stat().st_size > 1_000_000:
        print(f"cached {TARGET} ({TARGET.stat().st_size} bytes)")
    else:
        print(f"fetching {URL}")
        req = urllib.request.Request(URL, headers={"User-Agent": "siliconboard/0.1"})
        with urllib.request.urlopen(req, timeout=60, context=context) as resp, TARGET.open("wb") as out:
            out.write(resp.read())
        print(f"saved  {TARGET} ({TARGET.stat().st_size} bytes)")
    first = TARGET.read_text()[:60].splitlines()[0]
    assert first.startswith("library"), f"not a liberty file: {first!r}"
    print(f"head   {first}")


if __name__ == "__main__":
    main()