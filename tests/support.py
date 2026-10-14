"""Helpers shared by several test modules (imported as ``from support import ...``;
pytest puts ``tests/`` on ``sys.path``)."""


def exception_chain(exc):
    """``exc`` and every exception reachable from it through ``__cause__``/``__context__``."""
    seen = []
    pending = [exc]
    while pending:
        link = pending.pop()
        if link is None or any(link is s for s in seen):
            continue
        seen.append(link)
        pending += [link.__cause__, link.__context__]
    return seen
