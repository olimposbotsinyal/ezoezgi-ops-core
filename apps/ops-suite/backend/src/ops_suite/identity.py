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

import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_IDENTITY_CONFIG_PATH = Path("config/ops_suite_identities.json")

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


class AuthenticationError(Exception):
    """Token eksik/gecersiz -- HTTP katmaninda 401'e eslenir."""


class AuthorizationError(Exception):
    """Token GECERLI ama istenen eylem icin kapsam/yetki YETERSIZ --
    HTTP katmaninda 403'e eslenir."""


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
    def __init__(self, identities: dict[str, Identity]) -> None:
        self._identities = dict(identities)

    @classmethod
    def from_config_path(cls, config_path: str | Path = DEFAULT_IDENTITY_CONFIG_PATH, *, env: dict[str, str] | None = None) -> "IdentityStore":
        env = env if env is not None else os.environ
        try:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        return cls(load_identities(config, env))

    @property
    def is_owner_configured(self) -> bool:
        return any(identity.authority_source == AUTHORITY_OWNER for identity in self._identities.values())

    def authenticate(self, token: str | None) -> Identity:
        """`token` bos/None VEYA hicbir kimlikle eslesmiyorsa
        `AuthenticationError` firlatir. Zamanlama-saldirisina dayanikli
        karsilastirma icin `hmac.compare_digest` kullanilir (dogrudan
        `==` degil)."""
        if not token:
            raise AuthenticationError("kimlik dogrulama token'i saglanmadi (Authorization: Bearer <token> bekleniyor)")
        for candidate_token, identity in self._identities.items():
            if hmac.compare_digest(candidate_token, token):
                return identity
        raise AuthenticationError("token hicbir taninan kimlikle eslesmiyor")


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
                "SAHIBI-ONLY bir kok (root) eylemdir -- delegate kapsami GECERSIZDIR"
            )
        if not identity.has_scope(required_scope):
            raise AuthorizationError(f"'{identity.actor_id}' ({identity.authority_source}) '{required_scope}' kapsamina sahip DEGIL")
        return DECISION_SCOPE_OWNER_ROOT if identity.authority_source == AUTHORITY_OWNER else required_scope

    if decision == "rejected":
        if not identity.has_scope(SCOPE_REJECT):
            raise AuthorizationError(f"'{identity.actor_id}' ({identity.authority_source}) '{SCOPE_REJECT}' kapsamina sahip DEGIL")
        return DECISION_SCOPE_OWNER_ROOT if identity.authority_source == AUTHORITY_OWNER else SCOPE_REJECT

    raise ValueError(f"gecersiz decision: {decision!r}")
