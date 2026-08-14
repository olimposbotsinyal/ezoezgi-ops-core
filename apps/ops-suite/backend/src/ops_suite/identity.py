"""Kimlik dogrulama + kapsam-tabanli yetkilendirme (PLAN.md T28,
DECISIONS.md ADR-019, BACKLOG.md B044 -- SECURITY P0).

**Sahibi kok koruyucu (owner root guard):** `authority_source="owner"`
olan kimlik HER ZAMAN TUM kapsamlara (scopes) sahiptir -- bu, kod
seviyesinde sabittir (bkz. `Identity.has_scope`), config'teki hicbir
`scopes` alaniyla degistirilemez. `risk_level="irreversible"` (veya
bilinmeyen/eslesmeyen bir risk seviyesi) onaylari, delegate'in config'te
ne yazdigina BAKILMAKSIZIN YALNIZCA sahibi tarafindan verilebilir --
defense-in-depth: yanlis yapilandirilmis bir delegate kaydi bile bu
korumayi ATLATAMAZ (bkz. `authorize_decision`).

**Sirlar YALNIZCA ortam degiskenlerinden okunur** (ADR-010 ile ayni
ilke, bkz. `legitimacy_provider_client.py`'nin Jira credential deseni)
-- token DEGERLERI `config/ops_suite_identities.json`'a ASLA yazilmaz,
yalnizca HANGI ortam degiskeninin token'i tuttugu (`token_env_var`)
belirtilir. Env degiskeni SET EDILMEMISSE o kimlik icin HICBIR token
eslesmesi kurulmaz -- fail-closed, fail-open DEGIL: sahibi token'i
tanimlanmamissa hicbir onay/red eylemi GERCEKLESTIRILEMEZ (sessizce
izin verilmez)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_IDENTITY_CONFIG_PATH = Path("config/ops_suite_identities.json")
DEFAULT_TOKEN_REVOCATION_LOG_PATH = Path("data/identity/token_revocations.jsonl")

AUTHORITY_OWNER = "owner"
AUTHORITY_DELEGATE = "delegate"
VALID_AUTHORITY_SOURCES = (AUTHORITY_OWNER, AUTHORITY_DELEGATE)

SCOPE_APPROVE_LOW = "approve:low"
SCOPE_APPROVE_MEDIUM = "approve:medium"
SCOPE_APPROVE_HIGH = "approve:high"
SCOPE_APPROVE_IRREVERSIBLE = "approve:irreversible"
SCOPE_REJECT = "reject"
ALL_SCOPES = (
    SCOPE_APPROVE_LOW,
    SCOPE_APPROVE_MEDIUM,
    SCOPE_APPROVE_HIGH,
    SCOPE_APPROVE_IRREVERSIBLE,
    SCOPE_REJECT,
)

RISK_LEVEL_TO_APPROVE_SCOPE = {
    "low": SCOPE_APPROVE_LOW,
    "medium": SCOPE_APPROVE_MEDIUM,
    "high": SCOPE_APPROVE_HIGH,
    "irreversible": SCOPE_APPROVE_IRREVERSIBLE,
}

AUTH_METHOD_BEARER = "bearer"

DECISION_SCOPE_OWNER_ROOT = "owner_root"

# B053 (BACKLOG.md B053, PLAN.md T52) -- auth karar gozlemlenebilirligi
# icin standardize, makine-ayristirilabilir neden kodlari. Onceden
# yalnizca serbest-metin bir istisna mesaji vardi -- audit katmani
# (auth_audit.py) bunlari GUVENILIR sekilde ayirt edebilmek icin
# sabit bir taksonomiye ihtiyac duyuyordu.
REASON_CODE_OK = "OK"
REASON_CODE_AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
REASON_CODE_AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
REASON_CODE_AUTH_TOKEN_REVOKED = "AUTH_TOKEN_REVOKED"
REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE = "AUTHZ_INSUFFICIENT_SCOPE"
REASON_CODE_AUTHZ_OWNER_ONLY = "AUTHZ_OWNER_ONLY"
REASON_CODE_RATE_LIMITED = "RATE_LIMITED"


class AuthenticationError(Exception):
    """Token eksik/gecersiz/iptal-edilmis -- HTTP katmaninda 401'e eslenir.
    `reason_code` (B053) audit katmaninin serbest-metin mesaji AYRISTIRMADAN
    NEDENI guvenilir sekilde kaydedebilmesi icindir."""

    def __init__(self, message: str, *, reason_code: str = REASON_CODE_AUTH_TOKEN_INVALID) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class AuthorizationError(Exception):
    """Token GECERLI ama istenen eylem icin kapsam/yetki YETERSIZ --
    HTTP katmaninda 403'e eslenir. `reason_code` icin bkz. AuthenticationError."""

    def __init__(self, message: str, *, reason_code: str = REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class UnknownActorError(Exception):
    """B051 -- `rotate_token()`/`revoke_actor()`'a bilinmeyen bir
    `actor_id` verildiginde firlatilir -- HTTP katmaninda 404'e eslenir."""


def hash_token(token: str) -> str:
    """Token'in GERI DONDURULEMEZ (SHA-256) ozeti. Iptal listesi
    (`TokenRevocationStore`) diske YALNIZCA bu ozeti yazar -- ham token
    DEGERI hicbir dosyaya ASLA yazilmaz (modul dokustringindeki "sirlar
    diske yazilmaz" ilkesiyle AYNI)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenRevocationStore:
    """B051 (BACKLOG.md B051, PLAN.md T50) -- iptal edilmis token'larin
    KALICI (append-only, JSONL -- ADR-009/016/022 ile AYNI desen) kaydi.
    Sunucu yeniden baslatildiginda, ilgili ortam degiskeni HALA eski
    (iptal edilmis) token degerini tasiyor olsa BILE, bu liste sayesinde
    o token GECERSIZ kalmaya devam eder -- rotasyon/iptalin KALICILIGI
    bu dosyaya BAGLIDIR, yalnizca bellek-ici DEGIL.

    **v0 sinirlamasi (durustce isaretli):** dosya sinirsiz buyur
    (rotasyon/budama YOK -- ADR-022'nin presence_store'u ile AYNI
    gerekce, bu olcekte pratik bir sorun degil); `is_revoked()` HER
    cagrida dosyayi bastan okur (ApprovalQueueStore/PresenceStore ile
    AYNI "replay-based read" basitligi, v0 olcek icin yeterli)."""

    def __init__(self, log_path: str | Path = DEFAULT_TOKEN_REVOCATION_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def log_path(self) -> Path:
        return self._log_path

    def revoke(self, token: str, *, actor_id: str, revoked_by: str, reason: str) -> dict[str, Any]:
        """`token`'in hash'ini kalici iptal listesine ekler. Donen kayitta
        da yalnizca HASH vardir -- cagiran taraf (app.py) bunu ASLA ham
        token DEGERIYLE loglamamalidir."""
        record: dict[str, Any] = {
            "token_hash": hash_token(token),
            "actor_id": actor_id,
            "revoked_by": revoked_by,
            "reason": reason,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def is_revoked(self, token: str) -> bool:
        return hash_token(token) in self._load_revoked_hashes()

    def _load_revoked_hashes(self) -> set[str]:
        hashes: set[str] = set()
        try:
            raw = self._log_path.read_text(encoding="utf-8")
        except OSError:
            return hashes
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # bozuk satir -- sessizce atla (ApprovalQueueStore/PresenceStore ile AYNI tolerans)
            token_hash = record.get("token_hash")
            if token_hash:
                hashes.add(token_hash)
        return hashes


@dataclass(frozen=True)
class Identity:
    actor_id: str
    display_name: str
    authority_source: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.authority_source not in VALID_AUTHORITY_SOURCES:
            raise ValueError(f"gecersiz authority_source: {self.authority_source!r} (gecerli: {VALID_AUTHORITY_SOURCES})")

    def has_scope(self, scope: str) -> bool:
        """Sahibi (owner) HER ZAMAN True doner -- `scopes` alanindan
        BAGIMSIZ, kod seviyesinde sabit kok-koruyucu (root guard)."""
        if self.authority_source == AUTHORITY_OWNER:
            return True
        return scope in self.scopes


def _load_identity_from_entry(entry: dict[str, Any], *, authority_source: str, env: dict[str, str]) -> tuple[str, Identity] | None:
    token_env_var = entry.get("token_env_var", "")
    token = env.get(token_env_var) if token_env_var else None
    if not token:
        return None
    actor_id = entry["actor_id"]
    display_name = entry.get("display_name", actor_id)
    if authority_source == AUTHORITY_OWNER:
        scopes = frozenset(ALL_SCOPES)
    else:
        scopes = frozenset(s for s in entry.get("scopes", []) if s in ALL_SCOPES)
    return token, Identity(actor_id=actor_id, display_name=display_name, authority_source=authority_source, scopes=scopes)


def load_identities(config: dict[str, Any], env: dict[str, str]) -> dict[str, Identity]:
    """`config/ops_suite_identities.json` icerigi + ortam degiskenlerinden
    TOKEN -> `Identity` esleme sozlugu insa eder. Token degeri config'te
    HICBIR YERDE tutulmaz -- yalnizca `env[token_env_var]` okunur."""
    identities: dict[str, Identity] = {}

    owner_entry = config.get("owner")
    if isinstance(owner_entry, dict) and "actor_id" in owner_entry:
        result = _load_identity_from_entry(owner_entry, authority_source=AUTHORITY_OWNER, env=env)
        if result is not None:
            token, identity = result
            identities[token] = identity

    for delegate_entry in config.get("delegates", []):
        if not isinstance(delegate_entry, dict) or "actor_id" not in delegate_entry:
            continue
        result = _load_identity_from_entry(delegate_entry, authority_source=AUTHORITY_DELEGATE, env=env)
        if result is not None:
            token, identity = result
            identities[token] = identity

    return identities


class IdentityStore:
    def __init__(self, identities: dict[str, Identity], *, revocation_store: TokenRevocationStore | None = None) -> None:
        self._identities = dict(identities)
        # `revocation_store=None` -- GERIYE UYUMLU varsayilan: mevcut
        # `IdentityStore({token: Identity})` cagirilarindan (bkz.
        # test_ops_suite_identity.py/test_ops_suite_api.py) hicbiri
        # etkilenmez, disk erisimi/iptal kontrolu YALNIZCA acikca bir
        # store verildiginde (veya `from_config_path()` ile) devreye girer.
        self._revocation_store = revocation_store

    @classmethod
    def from_config_path(
        cls,
        config_path: str | Path = DEFAULT_IDENTITY_CONFIG_PATH,
        *,
        env: dict[str, str] | None = None,
        revocation_store: TokenRevocationStore | None = None,
    ) -> "IdentityStore":
        env = env if env is not None else os.environ
        try:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        return cls(load_identities(config, env), revocation_store=revocation_store or TokenRevocationStore())

    @property
    def is_owner_configured(self) -> bool:
        return any(identity.authority_source == AUTHORITY_OWNER for identity in self._identities.values())

    def authenticate(self, token: str | None) -> Identity:
        """`token` bos/None VEYA iptal-edilmis VEYA hicbir kimlikle
        eslesmiyorsa `AuthenticationError` (uygun `reason_code` ile)
        firlatir. Zamanlama-saldirisina dayanikli karsilastirma icin
        `hmac.compare_digest` kullanilir (dogrudan `==` degil).

        **Iptal kontrolu KIMLIK ESLESMESINDEN ONCE yapilir** (B051) --
        boylece bir token, sunucu yeniden baslatildiktan sonra ortam
        degiskeninde HALA eski/iptal-edilmis degeriyle mevcut olsa BILE
        (operator henuz env'i guncellemediyse) reddedilmeye devam eder."""
        if not token:
            raise AuthenticationError(
                "kimlik dogrulama token'i saglanmadi (Authorization: Bearer <token> bekleniyor)",
                reason_code=REASON_CODE_AUTH_TOKEN_MISSING,
            )
        if self._revocation_store is not None and self._revocation_store.is_revoked(token):
            raise AuthenticationError("token iptal edilmis (revoked)", reason_code=REASON_CODE_AUTH_TOKEN_REVOKED)
        for candidate_token, identity in self._identities.items():
            if hmac.compare_digest(candidate_token, token):
                return identity
        raise AuthenticationError("token hicbir taninan kimlikle eslesmiyor", reason_code=REASON_CODE_AUTH_TOKEN_INVALID)

    def _find_token_for_actor(self, actor_id: str) -> str | None:
        for token, identity in self._identities.items():
            if identity.actor_id == actor_id:
                return token
        return None

    def rotate_token(self, actor_id: str, *, revoked_by: str) -> str:
        """B051 -- `actor_id`'nin GUNCEL token'ini bulur, KALICI iptal
        listesine ekler, YENI rastgele bir token uretir (`secrets.token_urlsafe`
        -- kriptografik olarak guvenli), bellek-ici eslemeyi gunceller,
        yeni token DEGERINI doner. **Bu deger TEK SEFERLIK gosterilir**
        -- `IdentityStore` onu bir daha DUZ METIN olarak asla
        GORUNTULEMEZ/LOGLAMAZ, cagiran taraf GUVENLI sekilde saklamalidir.
        Bilinmeyen `actor_id` icin `UnknownActorError` firlatir."""
        old_token = self._find_token_for_actor(actor_id)
        if old_token is None:
            raise UnknownActorError(f"bilinmeyen actor_id: {actor_id!r}")
        identity = self._identities.pop(old_token)
        new_token = secrets.token_urlsafe(32)
        self._identities[new_token] = identity
        if self._revocation_store is not None:
            self._revocation_store.revoke(old_token, actor_id=actor_id, revoked_by=revoked_by, reason="rotated")
        return new_token

    def revoke_actor(self, actor_id: str, *, revoked_by: str) -> None:
        """B051 -- `actor_id`'nin GUNCEL token'ini KALICI olarak iptal
        eder, YENI bir token URETMEZ (acil "erisimi tamamen kes"
        senaryosu -- ornegin bir delegate'e artik guvenilmiyorsa).
        Bilinmeyen `actor_id` icin `UnknownActorError` firlatir."""
        old_token = self._find_token_for_actor(actor_id)
        if old_token is None:
            raise UnknownActorError(f"bilinmeyen actor_id: {actor_id!r}")
        del self._identities[old_token]
        if self._revocation_store is not None:
            self._revocation_store.revoke(old_token, actor_id=actor_id, revoked_by=revoked_by, reason="revoked")


def authorize_decision(identity: Identity, *, decision: str, risk_level: str | None) -> str:
    """Bir onay/red kararinin `identity` icin izinli olup olmadigini
    dogrular; izinliyse audit izine yazilacak `decision_scope` degerini
    doner (`"owner_root"` veya ornegin `"approve:high"`/`"reject"`).
    Yetersizse `AuthorizationError` firlatir.

    KOK KORUYUCU: `risk_level` bilinen 4 seviyeden biri DEGILSE (None,
    bos, tanimsiz) gereken kapsam en kisitlayici olan
    `approve:irreversible`'a duser -- bu, `identity.authority_source`
    sahibi olmadigi surece HER ZAMAN reddedilir, delegate'in `scopes`
    alaninda ne yazdigindan BAGIMSIZ (config hatasi/yanlis
    yapilandirmaya karsi savunma)."""
    if decision == "approved":
        required_scope = RISK_LEVEL_TO_APPROVE_SCOPE.get(risk_level or "", SCOPE_APPROVE_IRREVERSIBLE)
        if required_scope == SCOPE_APPROVE_IRREVERSIBLE and identity.authority_source != AUTHORITY_OWNER:
            raise AuthorizationError(
                f"'{identity.actor_id}' ({identity.authority_source}) risk_level={risk_level!r} onayi "
                "SAHIBI-ONLY bir kok (root) eylemdir -- delegate kapsami GECERSIZDIR",
                reason_code=REASON_CODE_AUTHZ_OWNER_ONLY,
            )
        if not identity.has_scope(required_scope):
            raise AuthorizationError(
                f"'{identity.actor_id}' ({identity.authority_source}) '{required_scope}' kapsamina sahip DEGIL",
                reason_code=REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE,
            )
        return DECISION_SCOPE_OWNER_ROOT if identity.authority_source == AUTHORITY_OWNER else required_scope

    if decision == "rejected":
        if not identity.has_scope(SCOPE_REJECT):
            raise AuthorizationError(
                f"'{identity.actor_id}' ({identity.authority_source}) '{SCOPE_REJECT}' kapsamina sahip DEGIL",
                reason_code=REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE,
            )
        return DECISION_SCOPE_OWNER_ROOT if identity.authority_source == AUTHORITY_OWNER else SCOPE_REJECT

    raise ValueError(f"gecersiz decision: {decision!r}")
