"""Command-line interface: ``mifit login``, ``mifit export`` and ``mifit profile``.

``login`` runs the QR login, shows the code in the terminal and stores the
session. ``export`` reads one domain (steps, heart rate, heart rate daily, consumption,
sleep, spo2, spo2 daily, weight, workouts) for a date range and writes ``{records, raw, notes}``
as JSON. ``profile`` reads the account profile (height, weight, daily step and
calorie goals) and writes it the same way; its ``raw`` is the whole profile,
personal details included, for the user's own eyes.

Nothing here prints a credential. Messages come from the SDK's own errors (which
are written to keep secrets out) and, for network failures, from the exception's
*type* only, because the text of a ``requests`` error contains the URL it was
fetching and a URL can carry a token.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

import qrcode
import requests

# ``MiFit``, ``login_qr``, ``save_session`` and ``record_event`` are looked up through
# this module at call time (not captured), so tests can patch them here and stay
# offline and off the real session files.
from mifit import MiFit
from mifit.auth.lifetime import describe_session, record_event
from mifit.auth.qr import login_qr
from mifit.auth.session import save_session
from mifit.client import to_epoch_s
from mifit.domains.base import REGISTRY, DomainResult
from mifit.errors import AuthError, MiFitError, NotificationRequiredError

__all__ = ["main"]

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_INTERRUPTED = 130  # the shell convention for "stopped by Ctrl+C"

# Domains whose MiFit method takes ``full=``; ``--full`` means nothing elsewhere.
_FULL_DOMAINS = frozenset({"heart_rate"})


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


def _render_qr(content: str, out: TextIO) -> None:
    """Draw ``content`` as a QR code of block characters on ``out``.

    The blocks are drawn so that the *light* modules are the bright ones, which is
    the right polarity for the usual light-text-on-dark terminal. If ``out``'s
    encoding cannot represent block characters (a redirected stdout on a legacy
    code page), a plain ``#`` rendering is written instead of failing mid-login.
    """
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2)
    code.add_data(content)
    code.make(fit=True)

    blocks = io.StringIO()
    code.print_ascii(out=blocks, invert=True)
    text = blocks.getvalue()
    try:
        text.encode(getattr(out, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        text = "".join(
            "".join("  " if cell else "##" for cell in row) + "\n"
            for row in code.get_matrix()
        )
    out.write(text)


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _show_qr(login_url: str, qr_payload: str) -> None:
    """``on_qr`` callback for ``login_qr``: show the code, then the plain URL."""
    # The service's ``qr`` field is the address of a QR *image*, not something to
    # encode again; the code to scan is the login URL that image stands for.
    print("Approve this login by scanning the QR code below with the Xiaomi / Mi Fitness")
    print("app, or by opening the login URL.")
    print()
    _render_qr(login_url, sys.stdout)
    print()
    print(f"Login URL: {login_url}")
    if _is_url(qr_payload) and qr_payload != login_url:
        print(f"QR image:  {qr_payload}")
    print()
    print("Waiting for approval (Ctrl+C to cancel)...")
    sys.stdout.flush()  # the call below blocks until the person approves


def _cmd_login(args: argparse.Namespace) -> int:
    # Deliberately not ``MiFit().login_qr``: a first-time login has no stored
    # session, and constructing ``MiFit`` without one raises ``AuthError``.
    try:
        session = login_qr(on_qr=_show_qr)
    except NotificationRequiredError as exc:
        _eprint("error: Xiaomi requires a security confirmation before this login can finish.")
        _eprint("Open this URL, complete the confirmation, then run `mifit login` again:")
        _eprint(f"  {exc.notification_url}")
        return _EXIT_ERROR
    except MiFitError as exc:
        _eprint(f"error: {exc}")
        return _EXIT_ERROR
    except requests.RequestException as exc:
        _eprint(f"error: network request failed ({type(exc).__name__}).")
        return _EXIT_ERROR

    try:
        path = save_session(session)
    except OSError as exc:
        _eprint(f"error: could not save the session ({exc.strerror or type(exc).__name__}).")
        return _EXIT_ERROR
    record_event("login", session)  # best effort: it never raises

    # Only where the session went; never the session itself.
    where = f" to {path}" if path else ""
    print(f"Login successful. Session saved{where}.")
    return _EXIT_OK


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _date_arg(value: str) -> str:
    """argparse ``type=`` for ``--from`` / ``--to``: a real ``YYYY-MM-DD`` date."""
    try:
        to_epoch_s(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return value


def _open_client() -> Any:
    try:
        return MiFit()
    except ValueError as exc:
        # ``load_session`` raises this for a session file it cannot parse; logging
        # in again replaces it, so treat it like a missing session. Raised after
        # the block, so the original is not left on ``__context__`` (``from None``
        # would only hide it from the printed traceback).
        message = str(exc)
    raise AuthError(message)


def _fetch(client: Any, domain: str, start: str, end: str, full: bool) -> DomainResult:
    if domain in _FULL_DOMAINS:
        return getattr(client, domain)(start, end, full=full)
    return getattr(client, domain)(start, end)


def _plain(record: Any) -> Any:
    """A record as JSON-ready data: dataclasses become dicts, the rest is as it is."""
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        return dataclasses.asdict(record)
    return record


def _read(fetch: Callable[[Any], DomainResult]) -> DomainResult | None:
    """Open the client and return ``fetch(client)``.

    A failure is reported on stderr and gives ``None``: an ``AuthError`` with the
    session's ages (when a client was opened) and the advice to log in, any other
    SDK error with its message, a network failure by its type only.
    """
    client = None
    try:
        client = _open_client()
        return fetch(client)
    except AuthError as exc:
        _eprint(f"error: {exc}")
        if client is not None:
            # Ages and the renewal state only; the session itself is never printed.
            _eprint(f"session: {describe_session(client.session)}")
        _eprint("Run `mifit login` to sign in.")
        return None
    except MiFitError as exc:
        _eprint(f"error: {exc}")
        return None
    except requests.RequestException as exc:
        _eprint(f"error: network request failed ({type(exc).__name__}).")
        return None


def _write_result(result: DomainResult, json_path: str | None) -> int:
    """Show the notes on stderr and write ``{records, raw, notes}`` as JSON to
    ``json_path``, or to stdout when it is ``None``; return the exit code."""
    for note in result.notes:
        _eprint(f"note: {note}")

    records = [_plain(record) for record in result.records]
    text = json.dumps({"records": records, "raw": result.raw, "notes": result.notes}, indent=2)
    if json_path is None:
        # Keep stdout to the JSON alone so it can be piped.
        print(text)
        return _EXIT_OK

    try:
        Path(json_path).write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        _eprint(f"error: could not write {json_path} ({exc.strerror or type(exc).__name__}).")
        return _EXIT_ERROR
    print(f"Wrote {len(records)} record(s) to {json_path}.")
    return _EXIT_OK


def _cmd_export(args: argparse.Namespace) -> int:
    if args.full and args.domain not in _FULL_DOMAINS:
        _eprint(f"note: --full only affects heart_rate; ignored for {args.domain}.")

    result = _read(lambda client: _fetch(client, args.domain, args.start, args.end, args.full))
    if result is None:
        return _EXIT_ERROR
    return _write_result(result, args.json_path)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


def _cmd_profile(args: argparse.Namespace) -> int:
    result = _read(lambda client: client.profile())
    if result is None:
        return _EXIT_ERROR
    return _write_result(result, args.json_path)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mifit", description="Read your own Mi Fitness data from the Xiaomi cloud."
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    login = commands.add_parser(
        "login", help="log in by QR code and store the session for later commands"
    )
    login.set_defaults(handler=_cmd_login)

    export = commands.add_parser(
        "export", help="export one kind of data for a date range as JSON"
    )
    export.add_argument(
        "domain",
        choices=list(REGISTRY),
        metavar="domain",
        help=f"what to export: {', '.join(REGISTRY)}",
    )
    export.add_argument(
        "--from",
        dest="start",
        required=True,
        type=_date_arg,
        metavar="YYYY-MM-DD",
        help="first day to export (local time)",
    )
    export.add_argument(
        "--to",
        dest="end",
        required=True,
        type=_date_arg,
        metavar="YYYY-MM-DD",
        help="last day to export, inclusive (local time)",
    )
    _add_json_option(export)
    export.add_argument(
        "--full",
        action="store_true",
        help="heart_rate only: page back through the full history instead of one window",
    )
    export.set_defaults(handler=_cmd_export)

    profile = commands.add_parser(
        "profile", help="export your profile (height, weight, daily goals) as JSON"
    )
    _add_json_option(profile)
    profile.set_defaults(handler=_cmd_profile)
    return parser


def _add_json_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="write the JSON to this file instead of stdout",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code (0 on success)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        # Both are validated YYYY-MM-DD strings, so text order is date order.
        if args.command == "export" and args.start > args.end:
            parser.error("export: --from must not be later than --to")
    except SystemExit as exc:  # --help (0) and usage errors (2): report, don't raise
        return exc.code if isinstance(exc.code, int) else (_EXIT_OK if exc.code is None else 2)

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        _eprint("Interrupted.")
        return _EXIT_INTERRUPTED
