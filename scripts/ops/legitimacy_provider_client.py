"""Acil durum bilet MESRUIYET kontrolu icin saglayici (provider)
istemcisi -- GERCEK (salt-okunur, "verify-only") Jira entegrasyonu.

**Modlar:** `none` (kontrol hic calistirilmaz -- bu modulun disinda,
bkz. `emergency_legitimacy_core.py`), `mock`/`jira_stub` (PILOT,
deterministik stub -- bu modulun DISINDA, `emergency_legitimacy_core.py::run_provider_check`'te
zaten mevcuttu, DEGISTIRILMEDI), `jira` (BU MODUL -- GERCEK, salt-okunur
bilet VARLIK kontrolu).

**Guvenlik ilkeleri (KESINLIKLE ZORUNLU):**
  - Kimlik bilgileri (`JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`)
    YALNIZCA ORTAM DEGISKENLERINDEN okunur -- hicbir yerde sabit
    kodlanmaz, hicbir config/proposal/review dosyasina YAZILMAZ.
  - Kimlik bilgisi DEGERLERI (email/token) HICBIR ZAMAN ham/tam olarak
    loglara/hata mesajlarina/raporlara YAZILMAZ -- her hata yolu
    `_redact()`'ten GECER.
  - `jira` yapilandirilmamissa (env var eksikse), GERCEK bir aga
    BAGLANMAYA CALISILMAZ -- `checked=False` ile ACIKCA "atlandi"
    raporlanir (fabrike bir basari/basarisizlik ASLA uretilmez).
  - Zaman asimi + sinirli yeniden deneme: yalnizca GECICI hatalarda
    (baglanti/zaman asimi) yeniden denenir; 404 (bilet yok) ve 401/403
    (kimlik dogrulama basarisiz) ASLA yeniden denenmez (yeniden
    denemenin faydasi yoktur, yalnizca gecikme ekler).
"""

from __future__ import annotations

import base64
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

PROVIDER_JIRA = "jira"

DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SEC = 1.0

REDACTED = "***REDACTED***"


@dataclass
class ProviderEvidence:
    mode: str
    checked: bool
    found: bool
    status_code: int | None
    detail: str
    attempts: int = 1


@dataclass
class JiraCredentials:
    base_url: str | None = None
    email: str | None = None
    api_token: str | None = None

    def is_complete(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)

    def secrets(self) -> list[str]:
        return [s for s in (self.api_token, self.email) if s]


def read_jira_credentials_from_env(env: dict[str, str]) -> JiraCredentials:
    """`env` (tipik olarak `os.environ`, ama testte SENTETIK bir dict
    de olabilir) icinden `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`'i
    okur. Kimlik bilgileri BASKA HICBIR YERDEN (dosya/CLI argumani)
    OKUNMAZ -- gorev kisiti: "credentials only from env vars"."""
    return JiraCredentials(
        base_url=env.get("JIRA_BASE_URL"),
        email=env.get("JIRA_EMAIL"),
        api_token=env.get("JIRA_API_TOKEN"),
    )


def redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for s in secrets:
        if s:
            redacted = redacted.replace(s, REDACTED)
    return redacted


def _build_auth_header(email: str, api_token: str) -> str:
    auth_str = f"{email}:{api_token}"
    return "Basic " + base64.b64encode(auth_str.encode("utf-8")).decode("ascii")


def check_ticket_via_jira(
    ticket_id: str,
    *,
    credentials: JiraCredentials,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_sec: float = DEFAULT_RETRY_BACKOFF_SEC,
    sleep_fn=time.sleep,
    opener=urllib.request.urlopen,
) -> ProviderEvidence:
    """`ticket_id`nin Jira'da GERCEKTEN var olup olmadigini, SALT-OKUNUR
    bir `GET /rest/api/2/issue/{ticket_id}` cagrisiyla dogrular.

    `sleep_fn`/`opener` test ENJEKSIYONU icindir (gercek `time.sleep`/
    `urllib.request.urlopen` yerine sahte/mock fonksiyonlar gecirilerek,
    testler GERCEK bir agi/uykuyu BEKLEMEDEN calisir)."""
    if not credentials.is_complete():
        return ProviderEvidence(
            mode=PROVIDER_JIRA, checked=False, found=False, status_code=None,
            detail="jira yapilandirilmadi (JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN eksik) -- kontrol atlandi",
        )

    secrets = credentials.secrets()
    url = f"{credentials.base_url.rstrip('/')}/rest/api/2/issue/{ticket_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _build_auth_header(credentials.email, credentials.api_token),
            "Accept": "application/json",
        },
    )

    last_error_detail = "bilinmeyen hata"
    total_tries = max_retries + 1
    for attempt in range(1, total_tries + 1):
        try:
            with opener(req, timeout=timeout_sec) as resp:
                status = resp.getcode()
                if status == 200:
                    return ProviderEvidence(
                        mode=PROVIDER_JIRA, checked=True, found=True, status_code=status,
                        detail=f"bilet bulundu (HTTP {status})", attempts=attempt,
                    )
                return ProviderEvidence(
                    mode=PROVIDER_JIRA, checked=True, found=False, status_code=status,
                    detail=f"beklenmeyen HTTP durumu: {status}", attempts=attempt,
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ProviderEvidence(
                    mode=PROVIDER_JIRA, checked=True, found=False, status_code=404,
                    detail="bilet BULUNAMADI (HTTP 404)", attempts=attempt,
                )
            if exc.code in (401, 403):
                return ProviderEvidence(
                    mode=PROVIDER_JIRA, checked=True, found=False, status_code=exc.code,
                    detail=redact(f"kimlik dogrulama basarisiz (HTTP {exc.code})", secrets), attempts=attempt,
                )
            last_error_detail = redact(f"HTTP hatasi: {exc.code} {exc.reason}", secrets)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error_detail = redact(f"baglanti hatasi: {exc}", secrets)

        if attempt < total_tries:
            sleep_fn(retry_backoff_sec * attempt)

    return ProviderEvidence(
        mode=PROVIDER_JIRA, checked=True, found=False, status_code=None,
        detail=f"jira'ya {total_tries} denemede ulasilamadi -- son hata: {last_error_detail}",
        attempts=total_tries,
    )
