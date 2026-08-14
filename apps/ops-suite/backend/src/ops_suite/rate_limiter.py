"""Hiz sinirlama (rate limiting) -- auth-hassas uc noktalar icin (BACKLOG.md
B052, PLAN.md T51, SECURITY). Sabit-pencere (fixed-window) sayac; bellek-ici,
KALICILIK YOK -- bir restart pencereyi dogal olarak sifirlar, bu BEKLENEN ve
ZARARSIZDIR (rate limiting'in amaci kisa vadeli kotuye kullanimi engellemek,
uzun vadeli bir denetim izi tutmak DEGIL -- o is zaten audit_logger'in isi).

`HeartbeatTracker` ile AYNI enjekte-edilebilir `clock` deseni -- testler
GERCEK bir `time.sleep()` YAPMADAN, sahte bir saati ELLE ilerleterek
pencere-asimi davranisini deterministik dogrulayabilir."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


class RateLimitExceededError(Exception):
    """`key` icin yapilandirilmis pencerede izin verilen istek sayisi
    asildiginda firlatilir -- HTTP katmaninda 429'a eslenir."""

    def __init__(self, *, key: str, retry_after_seconds: float) -> None:
        super().__init__(f"'{key}' icin hiz siniri asildi -- {retry_after_seconds:.1f}s sonra tekrar deneyin")
        self.key = key
        self.retry_after_seconds = retry_after_seconds


@dataclass
class RateLimiter:
    """Sabit-pencere hiz sinirlayici. `key` cagiran tarafindan serbestce
    secilir (ornegin `f"{actor_id}:{category}"` -- bkz. app.py) boylece
    farkli actor'lar VEYA farkli eylem kategorileri (onay/red vs
    kimlik-yonetimi) birbirinin sinirini ETKILEMEZ."""

    max_requests: int = 20
    window_seconds: float = 60.0
    clock: Callable[[], float] = field(default=time.time)
    _hits: dict[str, list[float]] = field(default_factory=dict, init=False, repr=False)

    def check(self, key: str) -> None:
        """`key` bu pencerede zaten `max_requests`'e ulastiysa
        `RateLimitExceededError` firlatir. Aksi halde bu cagriyi
        GECERLI istek olarak KAYDEDER (basarili donus = istek izinli
        VE sayilmis, cagiran taraf ayrica bir "record" adimi
        YAPMAMALIDIR)."""
        now = self.clock()
        history = self._hits.setdefault(key, [])
        cutoff = now - self.window_seconds
        while history and history[0] < cutoff:
            history.pop(0)
        if len(history) >= self.max_requests:
            retry_after = self.window_seconds - (now - history[0])
            raise RateLimitExceededError(key=key, retry_after_seconds=max(0.0, retry_after))
        history.append(now)

    def reset(self, key: str) -> None:
        """Test edilebilirlik/tamlik icin -- `key`'in gecmisini siler."""
        self._hits.pop(key, None)
