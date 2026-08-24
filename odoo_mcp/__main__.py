"""Entry point: run the odoo MCP server over stdio (the SDK default transport).

stdout is the transport - nothing here may print to it.
"""

import sys
from pathlib import Path

from odoo_mcp.config import load_env_file
from odoo_mcp.server import mcp

# .env lives in the project root next to odoo_tasks.py; resolve relative to
# the package so the server finds it regardless of the launcher's cwd.
_ENV_PATH = str(Path(__file__).resolve().parent.parent / ".env")


def main() -> int:
    load_env_file(_ENV_PATH)
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
