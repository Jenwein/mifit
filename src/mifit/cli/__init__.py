"""Command-line interface: ``mifit login``, ``mifit export`` and ``mifit profile``.

``login`` runs the QR login, shows the code in the terminal and stores the
session. ``export`` reads one domain (steps, heart rate, heart rate daily, consumption,
sleep, spo2, spo2 daily, weight, workouts) for a date range and writes ``{records, raw, notes}``
as JSON. ``profile`` reads the account profile (height, weight, daily step and
calorie goals) and writes it the same way; its ``raw`` is the whole profile,
personal details included, for the user's own eyes.

A range longer than one chunk (``mifit.chunks``: a week for raw heart rate and SpO2,
31 days for the rest, or ``--chunk``) is read chunk by chunk through one client, one
request at a time, with progress on stderr; a range that fits in one chunk is the one
call it always was. A range over 92 days needs ``--long``. ``heart_rate --full`` is a
single backward read and is never chunked. With ``--json PATH``, each finished chunk
is appended to ``PATH.partial`` (``mifit.cli.partial``), so an interrupted export
continues with ``--resume`` instead of reading everything again; that file holds only
the export's own data and arguments, never a credential or session field, and is
deleted once the output is written.

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
import math
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
from mifit.chunks import CHUNK_DAYS, Chunk, chunk_size_for, merge_chunks, plan_chunks
from mifit.cli.partial import (
    PartialError,
    append_part,
    export_header,
    load_partial,
    partial_path,
    start_partial,
)
from mifit.client import to_epoch_s
from mifit.domains.base import REGISTRY, DomainResult
from mifit.domains.days import parse_day
from mifit.errors import AuthError, MiFitError, NotificationRequiredError

__all__ = ["main"]

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_INTERRUPTED = 130  # the shell convention for "stopped by Ctrl+C"

# Domains whose MiFit method takes ``full=``; ``--full`` means nothing elsewhere.
_FULL_DOMAINS = frozenset({"heart_rate"})

# The longest range an export reads without ``--long`` (about a quarter).
_LONG_RANGE_DAYS = 92


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
    """argparse ``type=`` for ``--from`` / ``--to``: a real ``YYYY-MM-DD`` date whose
    first and last second this platform can represent (a read needs both)."""
    try:
        to_epoch_s(value)
        to_epoch_s(value, end_of_day=True)
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


def _report(exc: MiFitError | requests.RequestException, client: Any, where: str = "") -> None:
    """Report a failed read on stderr: an ``AuthError`` with the session's ages (when
    a client was opened) and the advice to log in, any other SDK error with its
    message, a network failure by its type only. ``where`` ends the error line."""
    if isinstance(exc, requests.RequestException):
        _eprint(f"error: network request failed ({type(exc).__name__}){where}.")
        return
    _eprint(f"error: {exc}{where}")
    if isinstance(exc, AuthError):
        if client is not None:
            # Ages and the renewal state only; the session itself is never printed.
            _eprint(f"session: {describe_session(client.session)}")
        _eprint("Run `mifit login` to sign in.")


def _read(fetch: Callable[[Any], DomainResult]) -> DomainResult | None:
    """Open the client and return ``fetch(client)``; a failure is reported on
    stderr (``_report``) and gives ``None``."""
    client = None
    try:
        client = _open_client()
        return fetch(client)
    except (MiFitError, requests.RequestException) as exc:
        _report(exc, client)
        return None


def _finite(value: Any) -> tuple[Any, int]:
    """A copy of ``value`` with every non-finite float (NaN, +Infinity, -Infinity)
    replaced by ``None``, at any depth of dicts, lists and tuples (the latter
    become lists), and how many were replaced. ``bool`` and ``int`` are untouched.
    """
    if isinstance(value, float):
        return (None, 1) if not math.isfinite(value) else (value, 0)
    if isinstance(value, dict):
        replaced = 0
        out = {}
        for key, item in value.items():
            out[key], count = _finite(item)
            replaced += count
        return out, replaced
    if isinstance(value, (list, tuple)):
        replaced = 0
        items = []
        for item in value:
            clean, count = _finite(item)
            items.append(clean)
            replaced += count
        return items, replaced
    return value, 0


def _json_ready(result: DomainResult) -> DomainResult:
    """``result`` as strict-JSON data: records as plain dicts and no NaN/Infinity.

    JSON has no such numbers (``json.dumps`` would write the non-standard ``NaN``
    and ``Infinity`` literals), so each one becomes ``null``; the notes say how
    many, so the change is never silent.
    """
    records, in_records = _finite([_plain(record) for record in result.records])
    raw, in_raw = _finite(result.raw)
    notes = list(result.notes)
    replaced = in_records + in_raw
    if replaced:
        notes.append(
            f"Replaced {replaced} non-finite number(s) (NaN or Infinity) with null: "
            "strict JSON has no such values."
        )
    return DomainResult(records=records, raw=raw, notes=notes)


def _write_result(result: DomainResult, json_path: str | None) -> int:
    """Show the notes on stderr and write ``{records, raw, notes}`` as strict JSON
    to ``json_path``, or to stdout when it is ``None``; return the exit code."""
    result = _json_ready(result)
    for note in result.notes:
        _eprint(f"note: {note}")

    text = json.dumps(
        {"records": result.records, "raw": result.raw, "notes": result.notes},
        indent=2,
        allow_nan=False,
    )
    if json_path is None:
        # Keep stdout to the JSON alone so it can be piped.
        print(text)
        return _EXIT_OK

    try:
        Path(json_path).write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        _eprint(f"error: could not write {json_path} ({exc.strerror or type(exc).__name__}).")
        return _EXIT_ERROR
    print(f"Wrote {len(result.records)} record(s) to {json_path}.")
    return _EXIT_OK


def _export_usage_error(args: argparse.Namespace) -> str | None:
    """Why these ``export`` arguments are refused before anything is read, or ``None``."""
    # Both are validated YYYY-MM-DD strings, so text order is date order.
    if args.start > args.end:
        return "export: --from must not be later than --to"
    if args.resume and args.json_path is None:
        return "export: --resume needs --json (the progress is kept next to that file)"
    full = args.full and args.domain in _FULL_DOMAINS
    if full and (args.long or args.chunk or args.resume):
        return (
            "export: heart_rate --full is one backward read and cannot be chunked; "
            "drop --long, --chunk and --resume"
        )
    days = (parse_day(args.end) - parse_day(args.start)).days + 1
    if days > _LONG_RANGE_DAYS and not args.long and not full:  # --full ignores --from
        return (
            f"export: the range is {days} days long; a range over {_LONG_RANGE_DAYS} days "
            "takes many requests and needs --long"
        )
    return None


def _plan(args: argparse.Namespace) -> tuple[list[Chunk], str, bool]:
    """The chunks of this export in reading order, its chunk size, and whether it is
    ``heart_rate --full``: one backward read, so a single chunk, never split."""
    first, last = parse_day(args.start), parse_day(args.end)
    size = args.chunk or chunk_size_for(args.domain)
    if args.full and args.domain in _FULL_DOMAINS:
        return [Chunk(first, last)], size, True
    return plan_chunks(args.domain, first, last, size), size, False


def _resume_hint(partial: Path, auth: bool) -> str:
    """How to go on with an export whose finished chunks are kept in ``partial``."""
    login = "after `mifit login`, " if auth else ""
    return (
        f"Completed chunks are saved in {partial}; {login}rerun the same command "
        "with --resume to continue."
    )


def _holds_no_chunk(partial: Path) -> bool:
    """Whether ``partial`` has no complete line. Chunks are only written after the
    header line, so such a file broke while it was being created: it holds no data."""
    try:
        return b"\n" not in partial.read_bytes()
    except OSError:
        return False


def _load_progress(
    partial: Path, header: dict, plan: list[Chunk], resume: bool
) -> dict[str, DomainResult] | None:
    """The chunks of this export already finished, by label: with ``resume``, those
    that ``partial`` holds; else none.

    Gives ``None`` after saying on stderr why the export cannot start: ``partial``
    exists but ``resume`` was not asked for (the file is left untouched, so hours of
    progress are never overwritten), or it cannot be loaded.
    """
    if not resume:
        if partial.exists():
            _eprint(
                f"error: {partial} holds an unfinished export; rerun with --resume to "
                "continue it, or delete it to start over."
            )
            return None
        return {}
    try:
        parts = load_partial(partial, header, [chunk.label for chunk in plan])
    except FileNotFoundError:
        _eprint(f"nothing to resume at {partial}; starting from the first chunk")
        return {}
    except PartialError as exc:
        _eprint(f"error: {exc}")
        if _holds_no_chunk(partial):
            _eprint("It holds no finished chunk, so deleting it loses no data.")
        return None
    except OSError as exc:
        _eprint(f"error: could not load {partial} ({exc.strerror or type(exc).__name__}).")
        return None
    _eprint(f"resuming {partial}: {len(parts)} of {len(plan)} chunk(s) already done")
    return parts


class _SaveError(Exception):
    """A finished chunk could not be written to the partial file. The message is the
    reason only (an OS error's text or an exception's type), never the chunk's data."""


def _save_part(
    partial: Path, header: dict, label: str, part: DomainResult, *, create: bool
) -> None:
    """Append the finished chunk ``label`` to ``partial``, creating the file with
    ``header`` first when ``create``. Raises ``_SaveError`` if it cannot be written."""
    try:
        if create:
            start_partial(partial, header)
        append_part(partial, label, part)
        return
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
    except ValueError as exc:  # a value strict JSON cannot hold; nothing was written
        reason = type(exc).__name__
    raise _SaveError(reason)


def _remove_partial(partial: Path) -> None:
    """Delete ``partial`` once the output is written; a failure is only a note."""
    try:
        partial.unlink(missing_ok=True)
    except OSError as exc:
        _eprint(
            f"note: could not delete {partial} ({exc.strerror or type(exc).__name__}); "
            "the output is complete, so it is no longer needed."
        )


def _cmd_export(args: argparse.Namespace) -> int:
    if args.full and args.domain not in _FULL_DOMAINS:
        _eprint(f"note: --full only affects heart_rate; ignored for {args.domain}.")

    plan, size, full = _plan(args)
    chunked = len(plan) > 1
    header = export_header(args.domain, args.start, args.end, size)
    # Progress is kept on disk only for a chunked export to a file.
    partial = partial_path(args.json_path) if chunked and args.json_path is not None else None
    parts = {} if partial is None else _load_progress(partial, header, plan, args.resume)
    if parts is None:
        return _EXIT_ERROR
    stored = partial is not None and partial.exists()  # resumed: the file holds this export
    if chunked:
        days = f"{CHUNK_DAYS[size]} day{'' if CHUNK_DAYS[size] == 1 else 's'}"
        _eprint(f"export: {len(plan)} chunks of up to {days}, read one at a time.")

    todo = [(index, chunk) for index, chunk in enumerate(plan, 1) if chunk.label not in parts]
    client = None
    step = ""  # "<i>/<n> <label>" of the chunk being read
    try:
        if todo:
            client = _open_client()  # one client, so one renewal budget, for the whole export
        for index, chunk in todo:
            step = f"{index}/{len(plan)} {chunk.label}"
            if chunked:
                start, end = chunk.first.isoformat(), chunk.last.isoformat()
            else:  # the whole range: exactly the call an unchunked export makes
                start, end = args.start, args.end
            # The same strict-JSON part is stored and merged, so a resumed chunk is
            # exactly a freshly read one.
            part = _json_ready(_fetch(client, args.domain, start, end, full))
            if partial is not None:
                _save_part(partial, header, chunk.label, part, create=not stored)
                stored = True
            parts[chunk.label] = part
            if chunked:
                _eprint(f"chunk {step}: {len(part.records)} record(s)")
    except (MiFitError, requests.RequestException) as exc:
        _report(exc, client, f" (chunk {step})" if chunked and step else "")
        if stored:
            _eprint(_resume_hint(partial, auth=isinstance(exc, AuthError)))
        return _EXIT_ERROR
    except _SaveError as exc:
        _eprint(f"error: could not save chunk {step} to {partial} ({exc}).")
        if stored:
            _eprint(_resume_hint(partial, auth=False))
        return _EXIT_ERROR
    except KeyboardInterrupt:
        if stored:
            _eprint(_resume_hint(partial, auth=False))
        raise  # main says "Interrupted." and exits 130

    result = merge_chunks([(chunk, parts[chunk.label]) for chunk in plan])
    code = _write_result(result, args.json_path)
    if partial is not None:
        if code == _EXIT_OK:
            _remove_partial(partial)
        else:  # the output could not be written; every chunk is still in the file
            _eprint(_resume_hint(partial, auth=False))
    return code


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
    export.add_argument(
        "--chunk",
        choices=list(CHUNK_DAYS),
        help="chunk size for a range longer than one chunk (default: per domain)",
    )
    export.add_argument(
        "--long",
        action="store_true",
        help=(
            f"allow a range over {_LONG_RANGE_DAYS} days: read in chunks, one request at a "
            "time; can take many minutes"
        ),
    )
    export.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted export from PATH.partial (needs --json)",
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
        problem = _export_usage_error(args) if args.command == "export" else None
        if problem is not None:
            parser.error(problem)
    except SystemExit as exc:  # --help (0) and usage errors (2): report, don't raise
        return exc.code if isinstance(exc.code, int) else (_EXIT_OK if exc.code is None else 2)

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        _eprint("Interrupted.")
        return _EXIT_INTERRUPTED
