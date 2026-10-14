"""The object-store downloader (spec §4.4, §11, D8): nothing but the link is sent, the
link is checked before any request, no redirect is followed, only HTTP 200 counts,
the body is capped, and every download is paced."""

import pytest
import requests
from fds_fixtures import URL_MARK, StoreHttp, store_url
from support import exception_chain

import mifit.fds.download as download_module
from mifit.errors import MiFitError
from mifit.fds.download import MAX_BODY_BYTES, FdsDownloader, FdsUnavailable

URL = store_url(1)


def _downloader(http, **kwargs):
    return FdsDownloader(http, **{"delay_s": 0, **kwargs})


def test_fetch_sends_nothing_but_the_url():
    http = StoreHttp(chunks=[b'"abc', b"def", b'"'])

    body = _downloader(http).fetch(URL)

    assert body == b'"abcdef"'
    assert http.calls == [(URL, {"timeout": 30.0, "allow_redirects": False, "stream": True})]
    (response,) = http.responses
    assert response.chunk_sizes == [65536]
    assert response.closed


def test_timeout_is_configurable():
    http = StoreHttp()

    _downloader(http, timeout_s=5.0).fetch(URL)

    assert http.calls == [(URL, {"timeout": 5.0, "allow_redirects": False, "stream": True})]


def test_default_http_is_the_requests_module():
    # The module's ``get`` opens a fresh session per call: no cookie jar is shared.
    assert FdsDownloader()._http is requests


def test_defaults():
    assert MAX_BODY_BYTES == 16 * 1024 * 1024
    assert download_module.HOST_SUFFIXES == (".fds.api.xiaomi.com",)
    assert issubclass(FdsUnavailable, MiFitError)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_is_refused(status):
    http = StoreHttp(status=status)

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http).fetch(URL)

    assert str(excinfo.value) == f"redirect (HTTP {status}) not followed"
    assert len(http.calls) == 1
    assert http.responses[0].closed
    assert http.responses[0].served == 0


@pytest.mark.parametrize("status", [204, 403, 404, 500])
def test_non_200_is_refused(status):
    http = StoreHttp(status=status)

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http).fetch(URL)

    assert str(excinfo.value) == f"HTTP {status}"
    assert http.responses[0].closed


def test_body_over_the_cap_is_aborted():
    http = StoreHttp(chunks=[b"x" * 8, b"x" * 8, b"x" * 8])

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http, max_bytes=10).fetch(URL)

    assert str(excinfo.value).startswith("larger than ")
    assert http.responses[0].served == 2
    assert http.responses[0].closed


def test_body_at_the_cap_is_kept():
    http = StoreHttp(chunks=[b"x" * 8, b"x" * 8])

    assert _downloader(http, max_bytes=16).fetch(URL) == b"x" * 16


def test_cap_message_names_the_default_size():
    chunk = b"x" * (1024 * 1024)
    http = StoreHttp(chunks=[chunk] * 17)

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http).fetch(URL)

    assert str(excinfo.value) == "larger than 16 MiB"
    assert http.responses[0].served == 17


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://cnbj1.fds.api.xiaomi.com/a", "not an https link"),
        (
            "https://cnbj1.fds.api.xiaomi.com.evil.example/a",
            "unexpected host cnbj1.fds.api.xiaomi.com.evil.example",
        ),
        ("https://evil.example/?h=.fds.api.xiaomi.com", "unexpected host evil.example"),
        ("https://user:pw@cnbj1.fds.api.xiaomi.com/a", "link carries a user name"),
        ("https://@cnbj1.fds.api.xiaomi.com/a", "link carries a user name"),
        ("https://fds.api.xiaomi.com/a", "unexpected host fds.api.xiaomi.com"),
        # ``requests`` (urllib3) ends the host at a backslash and would connect to
        # evil.example, while ``urlsplit`` reads the whole as one host name.
        ("https://evil.example\\.fds.api.xiaomi.com/a", "malformed link"),
        ("https://evil.example%2f.fds.api.xiaomi.com/a", "malformed link"),
        ("https://cnbj1.fds.api.xiaomi.com/a b", "malformed link"),
        ("https://evil.exa\tmple.fds.api.xiaomi.com/a", "malformed link"),
        ("https://cnbj1.fds.api.xiaomi.com/ä", "malformed link"),
        ("https://[::1/a", "malformed link"),
        ("https://[::1]/a", "malformed link"),
        ("https://cnbj1.fds.api.xiaomi.com:99999/a", "malformed link"),
        ("https:///a", "malformed link"),
        ("", "malformed link"),
    ],
)
def test_only_listed_https_hosts_are_fetched(url, message):
    http = StoreHttp()

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http).fetch(url)

    assert str(excinfo.value) == message
    assert exception_chain(excinfo.value) == [excinfo.value]
    assert http.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://CNBJ1.FDS.API.XIAOMI.COM/a",
        "HTTPS://cnbj1.fds.api.xiaomi.com/a",
        "https://cnbj1.fds.api.xiaomi.com:443/a",
    ],
)
def test_listed_host_in_another_spelling_is_fetched(url):
    http = StoreHttp()

    assert _downloader(http).fetch(url) == b"body"
    assert len(http.calls) == 1


def test_host_suffixes_can_be_extended():
    http = StoreHttp()
    suffixes = (".fds.api.xiaomi.com", ".synthetic-store.example")
    downloader = _downloader(http, host_suffixes=suffixes)

    assert downloader.fetch("https://x.synthetic-store.example/a") == b"body"
    assert downloader.fetch(URL) == b"body"
    assert [url for url, _ in http.calls] == ["https://x.synthetic-store.example/a", URL]


def test_host_suffixes_are_matched_in_lower_case():
    http = StoreHttp()
    downloader = _downloader(http, host_suffixes=(".Synthetic-Store.Example",))

    downloader.fetch("https://x.synthetic-store.example/a")

    assert len(http.calls) == 1


def test_host_suffixes_given_as_one_string_are_refused():
    # ".fds.api.xiaomi.com" read as a sequence would accept any host ending in "m".
    with pytest.raises(TypeError):
        FdsDownloader(StoreHttp(), host_suffixes=".fds.api.xiaomi.com")


@pytest.mark.parametrize("suffix", ["", ".", "xiaomi.com"])
def test_host_suffix_without_its_dot_is_refused(suffix):
    with pytest.raises(ValueError):
        FdsDownloader(StoreHttp(), host_suffixes=(".fds.api.xiaomi.com", suffix))


@pytest.mark.parametrize(
    ("where", "error"),
    [
        ("get", requests.ConnectionError(f"failed {URL}")),
        ("get", requests.exceptions.ConnectTimeout(f"timed out {URL}")),
        ("iter", requests.exceptions.ChunkedEncodingError(f"broke {URL}")),
        ("get", OSError(f"plain {URL}")),
    ],
    ids=["get", "timeout", "iter_content", "oserror"],
)
def test_network_error_message_has_no_url_and_no_chain(where, error):
    http = StoreHttp(error=error) if where == "get" else StoreHttp(iter_error=error)

    with pytest.raises(FdsUnavailable) as excinfo:
        _downloader(http).fetch(URL)

    exc = excinfo.value
    assert str(exc) == f"network error ({type(error).__name__})"
    assert URL_MARK not in str(exc)
    assert URL_MARK not in repr(exc)
    assert exception_chain(exc) == [exc]
    assert len(http.responses) == (1 if where == "iter" else 0)
    assert all(response.closed for response in http.responses)


def _record_sleeps(monkeypatch, events, uniform):
    def sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr(download_module.time, "sleep", sleep)
    monkeypatch.setattr(download_module.random, "uniform", uniform)


def test_each_download_is_paced(monkeypatch):
    events = []
    _record_sleeps(monkeypatch, events, lambda low, high: 0.0)
    downloader = FdsDownloader(StoreHttp(events=events), delay_s=1.0)

    downloader.fetch(URL)
    downloader.fetch(URL)

    assert events == [("sleep", 1.0), ("get", URL)] * 2


def test_jitter_matches_the_transport(monkeypatch):
    events = []
    bounds = []

    def uniform(low, high):
        bounds.append((low, high))
        return high

    _record_sleeps(monkeypatch, events, uniform)

    FdsDownloader(StoreHttp(), delay_s=1.0).fetch(URL)

    assert bounds == [(0, 0.5)]
    assert events == [("sleep", 1.5)]


def test_refused_url_is_not_paced(monkeypatch):
    events = []
    _record_sleeps(monkeypatch, events, lambda low, high: 0.0)
    http = StoreHttp(events=events)

    with pytest.raises(FdsUnavailable):
        FdsDownloader(http, delay_s=1.0).fetch("https://evil.example/a")

    assert events == []


def test_no_pause_when_delay_is_zero(monkeypatch):
    def fail(*args):
        raise AssertionError("no pause and no jitter when delay_s == 0")

    monkeypatch.setattr(download_module.time, "sleep", fail)
    monkeypatch.setattr(download_module.random, "uniform", fail)

    assert _downloader(StoreHttp()).fetch(URL) == b"body"
