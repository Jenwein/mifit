"""``mifit.mcp_server.access``, ``errors`` and ``cache``: the one client of the MCP
server, read one call at a time; the messages its failures become; the result cache.
Every credential here is synthetic."""

import threading
import time
from types import SimpleNamespace

import pytest
import requests

from mifit.errors import ApiError, AuthError, RenewalError
from mifit.mcp_server import errors
from mifit.mcp_server.access import Access
from mifit.mcp_server.cache import TtlCache
from mifit.mcp_server.errors import ToolFailure

SECRET = "SYNTH-SIGNED-URL-SECRET"


class FakeClient:
    def __init__(self, token):
        self.session = SimpleNamespace(service_token=token)
        self.calls = 0


class Factory:
    """Gives the clients (or raises the errors) in ``outcomes``, the last one again
    once they run out; counts the calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_client_opens_lazily_and_once():
    factory = Factory(FakeClient("A"))
    access = Access(factory)
    assert factory.calls == 0
    assert access.read(lambda c: c.session.service_token) == "A"
    assert access.read(lambda c: c.session.service_token) == "A"
    assert factory.calls == 1


def test_no_session_is_not_logged_in_and_is_not_remembered():
    factory = Factory(AuthError("No valid Mi Fitness login session was found."))
    access = Access(factory)
    with pytest.raises(ToolFailure) as err:
        access.read(lambda c: 1)
    assert str(err.value) == errors.NOT_LOGGED_IN
    with pytest.raises(ToolFailure):
        access.read(lambda c: 1)
    assert factory.calls == 2


def test_auth_error_retries_once_with_a_new_stored_session():
    a, b = FakeClient("A"), FakeClient("B")
    factory = Factory(a, b)
    seen = []

    def fn(client):
        seen.append(client.session.service_token)
        if client is a:
            raise AuthError("HTTP 401 from the service.")
        return "ok"

    access = Access(factory)
    assert access.read(fn) == "ok"
    assert seen == ["A", "B"]
    assert access.read(lambda c: c.session.service_token) == "B"
    assert factory.calls == 2


def test_auth_error_with_the_same_session_is_reported_without_a_retry():
    factory = Factory(FakeClient("A"), FakeClient("A"))
    calls = []

    def fn(client):
        calls.append(client)
        raise AuthError("HTTP 401 from the service.")

    access = Access(factory)
    with pytest.raises(ToolFailure) as err:
        access.read(fn)
    assert str(err.value).startswith("Mi Fitness refused the session. HTTP 401")
    assert str(err.value).endswith(errors.LOGIN_ADVICE)
    assert len(calls) == 1
    assert factory.calls == 2


def test_auth_error_when_the_stored_session_is_gone_is_not_logged_in():
    factory = Factory(FakeClient("A"), AuthError("No valid Mi Fitness login session."))

    def fn(client):
        raise AuthError("HTTP 401 from the service.")

    access = Access(factory)
    with pytest.raises(ToolFailure) as err:
        access.read(fn)
    assert str(err.value) == errors.NOT_LOGGED_IN
    with pytest.raises(ToolFailure):
        access.read(lambda c: 1)  # the client was dropped: the factory is asked again
    assert factory.calls == 3


def test_renewal_error_message():
    failure = errors.failure_for(RenewalError("Xiaomi refused the passToken (code 70016)."))
    assert str(failure) == (
        "The Mi Fitness session expired and could not be renewed. Xiaomi refused the "
        "passToken (code 70016). " + errors.LOGIN_ADVICE
    )


def test_api_error_keeps_its_message():
    assert str(errors.failure_for(ApiError("HTTP 500 from the service."))) == (
        "HTTP 500 from the service."
    )


def test_network_errors_show_only_their_type():
    exc = requests.ConnectionError(f"https://api.example/x?token={SECRET}")
    assert str(errors.failure_for(exc)) == "Network request failed (ConnectionError)."


def test_errors_of_other_kinds_are_not_mapped():
    assert errors.failure_for(RuntimeError("boom")) is None
    assert errors.failure_for(OSError("disk")) is None


def test_read_maps_the_errors_it_knows():
    access = Access(Factory(FakeClient("A")))

    def fn(client):
        raise requests.ReadTimeout(f"https://api.example/x?token={SECRET}")

    with pytest.raises(ToolFailure) as err:
        access.read(fn)
    assert str(err.value) == "Network request failed (ReadTimeout)."
    # Nothing leads back to the original, whose text holds the signed URL.
    assert err.value.__cause__ is None and err.value.__context__ is None


def test_unknown_errors_propagate():
    access = Access(Factory(FakeClient("A")))

    def fn(client):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        access.read(fn)


def test_reset_opens_the_stored_session_again():
    factory = Factory(FakeClient("A"), FakeClient("B"))
    access = Access(factory)
    assert access.read(lambda c: c.session.service_token) == "A"
    access.reset()
    assert access.read(lambda c: c.session.service_token) == "B"


def test_reads_never_overlap():
    access = Access(Factory(FakeClient("A")))
    active = 0
    most = 0
    guard = threading.Lock()

    def fn(client):
        nonlocal active, most
        with guard:
            active += 1
            most = max(most, active)
        time.sleep(0.02)
        with guard:
            active -= 1

    threads = [threading.Thread(target=access.read, args=(fn,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most == 1


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_cache_hit_until_ttl():
    clock = Clock()
    cache = TtlCache(clock=clock)
    assert cache.get("k") is None
    cache.put("k", "v")
    clock.now = 299.9
    assert cache.get("k") == "v"
    clock.now = 300.0
    assert cache.get("k") is None


def test_cache_evicts_least_recently_used():
    cache = TtlCache(max_entries=2, clock=Clock())
    cache.put("a", "1")
    cache.put("b", "2")
    assert cache.get("a") == "1"
    cache.put("c", "3")
    assert cache.get("b") is None
    assert (cache.get("a"), cache.get("c")) == ("1", "3")


def test_cache_clear_drops_every_entry():
    cache = TtlCache(clock=Clock())
    cache.put("a", "1")
    cache.put("b", "2")
    cache.clear()
    assert (cache.get("a"), cache.get("b")) == (None, None)


def test_cache_put_refreshes_age_and_recency():
    clock = Clock()
    cache = TtlCache(max_entries=2, clock=clock)
    cache.put("a", "1")
    cache.put("b", "2")
    clock.now = 200.0
    cache.put("a", "1'")
    cache.put("c", "3")  # evicts b, the least recently used now
    assert cache.get("b") is None
    clock.now = 450.0  # a was stored again at 200: still fresh
    assert cache.get("a") == "1'"
