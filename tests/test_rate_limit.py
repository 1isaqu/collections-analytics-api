from collections_api.core.ratelimit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_reports_retry_after():
    clock = Clock()
    limiter = RateLimiter(3, 60, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    clock.now = 20
    assert limiter.hit("a") == 40


def test_window_slides():
    clock = Clock()
    limiter = RateLimiter(2, 10, clock=clock)
    limiter.hit("a")
    clock.now = 6
    limiter.hit("a")
    clock.now = 11
    assert limiter.hit("a") is None  # the first hit left the window


def test_keys_are_independent():
    limiter = RateLimiter(1, 60, clock=Clock())
    assert limiter.hit("a") is None
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None


def test_bucket_map_is_pruned_past_the_ceiling():
    clock = Clock()
    limiter = RateLimiter(5, 10, max_buckets=20, clock=clock)
    for i in range(30):
        limiter.hit(f"client-{i}")
    clock.now = 100  # every window has expired
    limiter.hit("fresh")
    assert len(limiter) < 5
