from __future__ import annotations

import json
import sys

from .core import load_server_from_cwd


def main() -> int:
    server = load_server_from_cwd()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        tool = request.get("tool")
        arguments = request.get("arguments", {})

        if tool == "search_dependency":
            response = server.search_dependency(arguments.get("query", ""))
        elif tool == "install_dependency":
            response = server.install_dependency(arguments.get("dependency_id", ""))
        elif tool == "verify_bom_compatibility":
            response = server.verify_bom_compatibility(arguments.get("dependency_id", ""))
        else:
            response = {"status": "error", "error": f"Unknown tool: {tool}"}

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
