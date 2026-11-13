from meeting_test_runtime.models import SessionKey
from meeting_test_runtime.session_pool import MeetingSessionPool


class FakeRuntime:
    def __init__(self): self.closed = False
    def is_healthy(self): return not self.closed
    def close_all(self): self.closed = True


def test_invalidate_conflicts_closes_same_accounts_in_same_room():
    pool = MeetingSessionPool()
    ab = SessionKey("basic", "fm->fm", 10000, ("operator", "observer"))
    abc = SessionKey("targeted", "fm->fm", 10000,
                     ("operator", "observer", "witness"))
    runtime = pool.acquire(ab, FakeRuntime)

    invalidated = pool.invalidate_conflicts(abc)

    assert runtime.closed
    assert invalidated == 1


def test_invalidate_conflicts_keeps_exact_runtime_when_requested():
    pool = MeetingSessionPool()
    key = SessionKey("basic", "fm->fm", 10000, ("operator", "observer"))
    runtime = pool.acquire(key, FakeRuntime)

    invalidated = pool.invalidate_conflicts(key, include_exact=False)

    assert not runtime.closed
    assert invalidated == 0
    assert pool.acquire(key, FakeRuntime) is runtime
