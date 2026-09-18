#!/usr/bin/env python3
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MCP_URL = os.environ.get("FC27D_MCP_URL", "http://127.0.0.1:3926/mcp")


def forward(message):
    request = Request(
        MCP_URL,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=65) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"fc27d is unavailable at {MCP_URL}: {error}") from error


def main():
    for raw in sys.stdin:
        if not raw.strip():
            continue
        message = None
        try:
            message = json.loads(raw)
            response = forward(message)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
        except Exception as error:
            print(f"[FC27 MCP] {error}", file=sys.stderr, flush=True)
            if isinstance(message, dict) and message.get("id") is not None:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32001, "message": str(error)},
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    main()
