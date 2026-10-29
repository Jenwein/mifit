"""The MCP server: ``mifit mcp``.

A read-only Model Context Protocol server. By default it runs on stdio, started by
an AI client (Claude Desktop, Claude Code, Cursor, VS Code, ...) on the user's own
computer (``run``). ``mifit mcp --transport http`` runs it as a long-lived service
instead, on streamable HTTP (``run_http``), for clients on other machines. It gives
them the B-layer records of each checked data domain and the profile, never ``raw``,
with local times and dense data cut down to at most 500 rows.

The HTTP server has no authentication: whoever reaches its port can read the data
and start a login. It listens on loopback unless told otherwise, and it always
checks the ``Host`` header (the SDK's DNS rebinding protection), against the names
``accepted_hosts`` lists.

This package is the only part of mifit that imports the ``mcp`` SDK, and only when
the server runs, so ``import mifit`` and the other commands never pay for it.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Iterable

import mifit

__all__ = [
    "HTTP_HOST",
    "HTTP_PATH",
    "HTTP_PORT",
    "LOOPBACK_HOSTS",
    "accepted_hosts",
    "run",
    "run_http",
]

HTTP_HOST = "127.0.0.1"
"""The address the HTTP server listens on unless ``--host`` says otherwise."""

HTTP_PORT = 8766
"""The HTTP server's default port."""

HTTP_PATH = "/mcp"
"""The HTTP server's default endpoint path."""

LOOPBACK_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
"""The ``Host`` headers of a request over loopback, on any port (an SSH tunnel's,
say), as the SDK accepts them by default."""


def run(*, allow_gps: bool = False) -> None:
    """Serve MCP on stdin/stdout until the client closes stdin (``mifit mcp``).

    ``allow_gps`` opens the server's GPS gate: only then can ``get_workout_detail``
    return a track, and only when asked. One line about the server goes to stderr;
    stdout carries the protocol alone.
    """
    from mifit.mcp_server.server import build_server  # the SDK is loaded only here

    gps = "on" if allow_gps else "off"
    print(
        f"mifit {mifit.__version__}: MCP server on stdio (GPS tracks {gps})",
        file=sys.stderr,
    )
    build_server(allow_gps=allow_gps).run()


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:  # a name
        return None


def _is_loopback(host: str) -> bool:
    address = _address(host)
    return host.lower() == "localhost" if address is None else address.is_loopback


def _in_url(host: str) -> str:
    """``host`` as a URL or a ``Host`` header writes it: an IPv6 address in brackets."""
    return f"[{host}]" if ":" in host else host


def accepted_hosts(host: str, port: int, extra: Iterable[str] = ()) -> list[str]:
    """The ``Host`` headers the HTTP server listening on ``host``:``port`` accepts.

    A server on loopback accepts ``LOOPBACK_HOSTS`` (and its own address, on any
    port, when that is another loopback address such as ``127.0.0.2``); one on every
    address (``0.0.0.0`` or ``::``) accepts ``LOOPBACK_HOSTS`` too, since loopback
    reaches it; one on any other address or name accepts that address with the port,
    as a client that dials it sends it. ``extra`` (``--allowed-host``) adds values,
    each compared with the header as given (``NAME:*`` matches any port). The SDK
    compares case-sensitively and clients send names in lower case, so every value is
    lowered. Order is kept, duplicates dropped.
    """
    address = _address(host)
    if _is_loopback(host) or (address is not None and address.is_unspecified):
        names = list(LOOPBACK_HOSTS)
        if address is not None and address.is_loopback:
            names.append(f"{_in_url(address.compressed)}:*")
    else:
        names = [f"{_in_url(host)}:{port}"]
    accepted: list[str] = []
    for value in (*names, *extra):
        if value.lower() not in accepted:
            accepted.append(value.lower())
    return accepted


def run_http(
    *,
    allow_gps: bool = False,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    path: str = HTTP_PATH,
    allowed_hosts: Iterable[str] = (),
) -> None:
    """Serve MCP on streamable HTTP at ``http://host:port/path`` until interrupted
    (``mifit mcp --transport http``).

    Requests whose ``Host`` header is not in ``accepted_hosts(host, port,
    allowed_hosts)`` are refused (HTTP 421), and so are browser requests from an
    origin other than those names, over HTTP or HTTPS (HTTP 403). The login tools
    save the session on this machine and open nothing here: the QR code reaches the
    user through the tool result. mifit's lines and the SDK's log go to stderr, with
    a warning when the server listens beyond loopback, since it has no
    authentication; the web server's access log goes to stdout.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    from mifit.mcp_server.server import build_server  # the SDK is loaded only here

    accepted = accepted_hosts(host, port, allowed_hosts)
    gps = "on" if allow_gps else "off"
    print(
        f"mifit {mifit.__version__}: MCP server on http://{_in_url(host)}:{port}{path} "
        f"(GPS tracks {gps})",
        file=sys.stderr,
    )
    print(
        f"mifit: accepted Host headers: {', '.join(accepted)} (requests with another "
        "Host header get HTTP 421; add a name with --allowed-host)",
        file=sys.stderr,
    )
    if not _is_loopback(host):
        print(
            "mifit: warning: this server has no authentication. Anyone who can reach "
            f"port {port} on {host} can read this Mi Fitness account's data and start a "
            "login. Keep it on a private network (a Tailscale tailnet, a firewall).",
            file=sys.stderr,
        )
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=accepted,
        allowed_origins=[
            f"{scheme}://{name}" for name in accepted for scheme in ("http", "https")
        ],
    )
    build_server(allow_gps=allow_gps, remote=True).run(
        "streamable-http",
        host=host,
        port=port,
        streamable_http_path=path,
        transport_security=security,
    )
