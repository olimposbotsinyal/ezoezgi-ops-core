import asyncio
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any

from telegram.error import RetryAfter, TimedOut, NetworkError, BadRequest, TelegramError

logger = logging.getLogger(__name__)


class TelegramRateLimiter:
    """
    Basit global oran sınırlayıcı + RetryAfter/backoff yöneticisi.
    - max_messages_per_second: saniyede izin verilen mesaj sayısı.
    - max_concurrent: aynı anda kaç gönderim yapılabilir (genelde 1-2 tutmak güvenli).
    """

    def __init__(self, max_messages_per_second: float = 0.8, max_concurrent: int = 1):
        self.max_messages_per_second = max_messages_per_second
        self.interval = 1.0 / max_messages_per_second if max_messages_per_second > 0 else 1.0
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._last_send_ts = 0.0

    @asynccontextmanager
    async def slot(self):
        """
        Public concurrency gate. Linter'ın "protected member" uyarısını önler.
        """
        async with self._semaphore:
            yield

    async def throttle(self) -> None:
        now = time.time()
        elapsed = now - self._last_send_ts
        sleep_needed = self.interval - elapsed
        if sleep_needed > 0:
            await asyncio.sleep(sleep_needed)
        self._last_send_ts = time.time()


rate_limiter = TelegramRateLimiter(max_messages_per_second=0.8, max_concurrent=1)


async def safe_send_message(
    bot,
    *,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
    disable_web_page_preview: Optional[bool] = None,
    protect_content: Optional[bool] = None,
    max_retries: int = 5,
):
    """
    Telegram'a güvenli mesaj gönderimi:
    - Global rate limit
    - RetryAfter yakalama ve bekleme
    - TimedOut/NetworkError için exponential backoff
    - BadRequest parse entity hatasında Markdown/HTML'den düz metne fallback (1 kez)
    """
    attempt = 0
    backoff = 1.0

    async with rate_limiter.slot():
        while attempt < max_retries:
            attempt += 1
            try:
                await rate_limiter.throttle()
                return await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=disable_web_page_preview,
                    protect_content=protect_content,
                )

            except RetryAfter as e:
                wait_s = getattr(e, "retry_after", None)

                if wait_s is None:
                    # "Flood control exceeded. Retry in X seconds" gibi stringlerden de çıkarabilsin
                    try:
                        s = str(e)
                        if "Retry in" in s:
                            wait_s = float(s.split("Retry in", 1)[1].strip().split()[0])
                    except (ValueError, IndexError, AttributeError):
                        wait_s = None

                wait_s = min(max(float(wait_s or 5.0), 1.0), 60.0)
                logger.warning(f"[TG_RetryAfter] retry_in={wait_s}s attempt={attempt}/{max_retries}")
                await asyncio.sleep(wait_s)

            except (TimedOut, NetworkError) as e:
                logger.warning(f"[TG_TemporalError] {e} backoff={backoff}s attempt={attempt}/{max_retries}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

            except BadRequest as e:
                msg = str(e)

                # Markdown/HTML entity parse hatası: retry döngüsüne sokma.
                # Eğer parse_mode verilmişse, 1 kez parse_mode=None ile fallback dene.
                if "Can't parse entities" in msg or "can't parse entities" in msg:
                    logger.warning(f"[TG_ParseError] {msg} (fallback: parse_mode=None)")

                    if parse_mode is None:
                        # Zaten düz metin; tekrar denemenin anlamı yok
                        return None

                    try:
                        await rate_limiter.throttle()
                        return await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            parse_mode=None,  # düz metin
                            reply_markup=reply_markup,
                            disable_web_page_preview=disable_web_page_preview,
                            protect_content=protect_content,
                        )
                    except TelegramError as e2:
                        logger.error(f"[TG_ParseFallback_Fail] {e2}", exc_info=True)
                        return None

                # İçerik hatası vb. (chat not found, message is too long, vb.) -> retry etme
                logger.error(f"[TG_BadRequest] {e}")
                return None

            except TelegramError as e:
                # Telegram kaynaklı ama yukarıdaki spesifik sınıflara girmeyen hatalar:
                logger.error(f"[TG_SendMessage_TelegramError] {e}", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

        # Maks deneme aşıldı: sahte RetryAfter fırlatma (Flood control gibi görünmesin)
        logger.error(f"[TG_SendMessage_GiveUp] max_retries={max_retries} chat_id={chat_id}")
        return None
