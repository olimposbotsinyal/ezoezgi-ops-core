"""BACKLOG.md B052, PLAN.md T51 (SECURITY) -- `ops_suite.rate_limiter`
testleri. Tamami SAHTE bir saat (`clock` parametresi) ile deterministik --
GERCEK bir `time.sleep()` HICBIR YERDE kullanilmaz (flaky testlerden
kacinma ilkesi, T38/T44 ile AYNI standart)."""

from __future__ import annotations

import pytest

from ops_suite.rate_limiter import RateLimitExceededError, RateLimiter


class _FakeClock:
    """Elle ilerletilebilir sahte saat -- `HeartbeatTracker` testlerinin
    `clock` enjeksiyon deseniyle AYNI."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_check_allows_requests_under_the_limit():
    limiter = RateLimiter(max_requests=3, window_seconds=60.0, clock=_FakeClock())
    limiter.check("actor1")
    limiter.check("actor1")
    limiter.check("actor1")  # 3. istek -- sinira ULASIR ama ASMAZ


def test_check_raises_on_the_request_that_exceeds_the_limit():
    limiter = RateLimiter(max_requests=2, window_seconds=60.0, clock=_FakeClock())
    limiter.check("actor1")
    limiter.check("actor1")
    with pytest.raises(RateLimitExceededError) as excinfo:
        limiter.check("actor1")
    assert excinfo.value.key == "actor1"
    assert excinfo.value.retry_after_seconds > 0


def test_different_keys_have_independent_limits():
    """Farkli actor/kategori kombinasyonlari birbirini ETKILEMEMELI --
    bkz. app.py'nin `f'{actor_id}:{category}'` anahtarlama deseni."""
    limiter = RateLimiter(max_requests=1, window_seconds=60.0, clock=_FakeClock())
    limiter.check("actor1:approval_decision")
    with pytest.raises(RateLimitExceededError):
        limiter.check("actor1:approval_decision")
    # AYNI actor, FARKLI kategori -- KENDI sinirina sahip, ETKILENMEZ.
    limiter.check("actor1:identity_admin")


def test_window_expiry_allows_requests_again_without_sleep():
    """Pencere GERCEKTEN dolunca (sahte saat ilerletilerek, sleep YOK)
    istekler tekrar izinli olmali -- deterministik zaman-pencere testi."""
    clock = _FakeClock()
    limiter = RateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    limiter.check("actor1")
    limiter.check("actor1")
    with pytest.raises(RateLimitExceededError):
        limiter.check("actor1")

    clock.advance(61.0)  # pencerenin TAMAMEN disina cik
    limiter.check("actor1")  # artik GERCEKTEN izinli olmali


def test_partial_window_expiry_only_frees_the_expired_hits():
    """Sabit-pencere semantigi -- yalnizca pencereden CIKAN eski
    istekler dusurulur, hala pencerede olanlar SAYILMAYA devam eder."""
    clock = _FakeClock(start=0.0)
    limiter = RateLimiter(max_requests=2, window_seconds=10.0, clock=clock)
    limiter.check("actor1")  # t=0
    clock.advance(5.0)
    limiter.check("actor1")  # t=5 -- iki istek de HALA pencerede (0-10)
    with pytest.raises(RateLimitExceededError):
        limiter.check("actor1")  # t=5 -- 3. istek, sinir asildi

    clock.advance(5.5)  # t=10.5 -- ilk istek (t=0) artik pencerenin DISINDA
    limiter.check("actor1")  # izinli -- yalnizca 1 istek (t=5) hala pencerede


def test_retry_after_seconds_reflects_remaining_window_time():
    clock = _FakeClock(start=0.0)
    limiter = RateLimiter(max_requests=1, window_seconds=10.0, clock=clock)
    limiter.check("actor1")  # t=0
    clock.advance(3.0)  # t=3
    with pytest.raises(RateLimitExceededError) as excinfo:
        limiter.check("actor1")
    # Pencere t=0'da basladi, 10sn surer -> t=10'da biter -> simdi t=3, kalan ~7sn.
    assert 6.9 <= excinfo.value.retry_after_seconds <= 7.1


def test_reset_clears_history_for_a_key():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0, clock=_FakeClock())
    limiter.check("actor1")
    with pytest.raises(RateLimitExceededError):
        limiter.check("actor1")
    limiter.reset("actor1")
    limiter.check("actor1")  # sifirlandiktan sonra tekrar izinli


def test_default_clock_uses_real_time_and_does_not_crash():
    """Varsayilan `clock=time.time` GERCEKTEN calisir (yalnizca testlerde
    override edilmiyor olsa bile) -- production DI'nin dogru calistigina
    dair minimal bir duman testi."""
    limiter = RateLimiter(max_requests=5, window_seconds=60.0)
    limiter.check("actor1")  # cokmemeli
