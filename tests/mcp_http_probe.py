"""Talk to ``mifit mcp --transport http`` as a client on another machine does.

``test_mcp_cli`` runs this as a child process, since the network guard of
``conftest.py`` covers the test process only. Usage::

    python mcp_http_probe.py URL [HOST[,ORIGIN] ...]

It waits up to 60 seconds for the server to accept connections, then connects with
the SDK's own client: ``initialize``, ``tools/list`` and a ``get_status`` call. Then
it sends one bare ``initialize`` per HOST with that ``Host`` header (and that
``Origin`` header, when one follows a comma). It prints one JSON object: ``server``
(name and version), ``tools`` (name to description), ``status`` (``get_status``'s
payload) and ``hosts`` (each HOST[,ORIGIN] argument to its HTTP status code).
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from urllib.parse import urlsplit

import httpx2
from mcp import Client

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "mcp_http_probe", "version": "0"},
    },
}


def wait_for(url: str, timeout_s: float = 60) -> None:
    parts = urlsplit(url)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with socket.create_connection((parts.hostname, parts.port), timeout=1):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


async def probe(url: str, hosts: list[str]) -> dict:
    async with Client(url) as client:
        info = client.server_info
        listed = (await client.list_tools()).tools
        status = await client.call_tool("get_status", {})
    codes = {}
    async with httpx2.AsyncClient(trust_env=False) as http:
        for argument in hosts:
            host, _, origin = argument.partition(",")
            headers = {"Host": host, "Accept": "application/json, text/event-stream"}
            if origin:
                headers["Origin"] = origin
            reply = await http.post(url, json=INITIALIZE, headers=headers)
            codes[argument] = reply.status_code
    return {
        "server": {"name": info.name, "version": info.version},
        "tools": {tool.name: tool.description for tool in listed},
        "status": json.loads(status.content[0].text),
        "hosts": codes,
    }


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    url, hosts = argv[0], argv[1:]
    wait_for(url)
    print(json.dumps(asyncio.run(probe(url, hosts)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
