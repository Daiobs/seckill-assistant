"""Run the local web console."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Pocket 4P 本地抢购控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只监听本机")
    parser.add_argument("--port", type=int, default=8787, help="监听端口")
    args = parser.parse_args()

    uvicorn.run(
        "console_app.app:app",
        host=args.host,
        port=args.port,
        reload=False,
        app_dir=str(_PROJECT_ROOT),
    )


if __name__ == "__main__":
    main()
