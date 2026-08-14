"""PLAN.md T28, BACKLOG.md B044 (SECURITY P0) -- ops_suite.identity testleri:
kimlik yukleme (`load_identities`), token dogrulama (`IdentityStore.authenticate`),
kapsam/owner-root-guard yetkilendirmesi (`authorize_decision`). Tamami ag/dosya
sistemi olmadan calisan saf birim testleridir (env sozlugu elle insa edilir)."""

from __future__ import annotations

import pytest

from ops_suite.identity import (
    AUTHORITY_DELEGATE,
    AUTHORITY_OWNER,
    REASON_CODE_AUTH_TOKEN_INVALID,
    REASON_CODE_AUTH_TOKEN_MISSING,
    REASON_CODE_AUTH_TOKEN_REVOKED,
    REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE,
    REASON_CODE_AUTHZ_OWNER_ONLY,
    SCOPE_APPROVE_HIGH,
    SCOPE_APPROVE_IRREVERSIBLE,
    SCOPE_APPROVE_LOW,
    SCOPE_APPROVE_MEDIUM,
    SCOPE_REJECT,
    AuthenticationError,
    AuthorizationError,
    Identity,
    IdentityStore,
    TokenRevocationStore,
    UnknownActorError,
    authorize_decision,
    hash_token,
    load_identities,
)

_CONFIG = {
    "owner": {"actor_id": "serkan_eryilmaz", "display_name": "Serkan Eryılmaz", "token_env_var": "OPS_SUITE_OWNER_TOKEN"},
    "delegates": [
        {
            "actor_id": "asistan_1", "display_name": "Asistan Delegesi", "token_env_var": "OPS_SUITE_DELEGATE_TOKEN_1",
            "scopes": ["approve:low", "approve:medium", "reject"],
        }
    ],
}


# --- Identity.has_scope --------------------------------------------------


def test_owner_has_scope_is_unconditionally_true():
    owner = Identity(actor_id="x", display_name="x", authority_source=AUTHORITY_OWNER, scopes=frozenset())
    assert owner.has_scope(SCOPE_APPROVE_IRREVERSIBLE) is True


def test_delegate_has_scope_only_if_in_scopes_set():
    delegate = Identity(actor_id="d1", display_name="D1", authority_source=AUTHORITY_DELEGATE, scopes=frozenset({SCOPE_APPROVE_LOW}))
    assert delegate.has_scope(SCOPE_APPROVE_LOW) is True
    assert delegate.has_scope(SCOPE_APPROVE_HIGH) is False


def test_identity_rejects_invalid_authority_source():
    with pytest.raises(ValueError, match="gecersiz authority_source"):
        Identity(actor_id="x", display_name="x", authority_source="superuser")


# --- load_identities -------------------------------------------------------


def test_load_identities_empty_when_owner_env_var_unset():
    identities = load_identities(_CONFIG, env={})
    assert identities == {}


def test_load_identities_loads_owner_when_env_var_set():
    identities = load_identities(_CONFIG, env={"OPS_SUITE_OWNER_TOKEN": "owner-secret"})
    assert "owner-secret" in identities
    owner = identities["owner-secret"]
    assert owner.actor_id == "serkan_eryilmaz"
    assert owner.authority_source == AUTHORITY_OWNER
    # Sahibi TUM scope'lara sahip olmali (kod-seviyesinde, config'te scopes alani bile yok).
    assert owner.has_scope(SCOPE_APPROVE_IRREVERSIBLE)


def test_load_identities_loads_delegate_when_its_env_var_set():
    identities = load_identities(_CONFIG, env={"OPS_SUITE_DELEGATE_TOKEN_1": "delegate-secret"})
    assert "delegate-secret" in identities
    delegate = identities["delegate-secret"]
    assert delegate.actor_id == "asistan_1"
    assert delegate.authority_source == AUTHORITY_DELEGATE
    assert delegate.has_scope(SCOPE_APPROVE_LOW)
    assert not delegate.has_scope(SCOPE_APPROVE_IRREVERSIBLE)


def test_load_identities_ignores_unknown_scopes_in_config():
    config = {"owner": None, "delegates": [{"actor_id": "d1", "token_env_var": "T1", "scopes": ["approve:low", "sudo"]}]}
    identities = load_identities(config, env={"T1": "tok"})
    assert identities["tok"].scopes == frozenset({SCOPE_APPROVE_LOW})


def test_load_identities_both_owner_and_delegate_when_both_env_vars_set():
    identities = load_identities(_CONFIG, env={"OPS_SUITE_OWNER_TOKEN": "o", "OPS_SUITE_DELEGATE_TOKEN_1": "d"})
    assert len(identities) == 2


# --- IdentityStore.authenticate --------------------------------------------


def test_authenticate_valid_token_returns_identity():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    identity = store.authenticate("tok")
    assert identity.actor_id == "a"


def test_authenticate_none_token_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(AuthenticationError) as excinfo:
        store.authenticate(None)
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_MISSING


def test_authenticate_empty_token_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(AuthenticationError) as excinfo:
        store.authenticate("")
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_MISSING


def test_authenticate_unknown_token_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(AuthenticationError) as excinfo:
        store.authenticate("wrong-token")
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_INVALID


def test_is_owner_configured_true_when_owner_present():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    assert store.is_owner_configured is True


def test_is_owner_configured_false_when_only_delegates():
    store = IdentityStore(
        {"tok": Identity(actor_id="d", display_name="D", authority_source=AUTHORITY_DELEGATE, scopes=frozenset())}
    )
    assert store.is_owner_configured is False


def test_from_config_path_missing_file_yields_no_identities(tmp_path):
    store = IdentityStore.from_config_path(tmp_path / "does-not-exist.json", env={"OPS_SUITE_OWNER_TOKEN": "x"})
    with pytest.raises(AuthenticationError):
        store.authenticate("x")


def test_from_config_path_reads_real_file_and_env(tmp_path):
    import json

    config_path = tmp_path / "identities.json"
    config_path.write_text(json.dumps(_CONFIG), encoding="utf-8")
    store = IdentityStore.from_config_path(config_path, env={"OPS_SUITE_OWNER_TOKEN": "real-owner-tok"})
    identity = store.authenticate("real-owner-tok")
    assert identity.actor_id == "serkan_eryilmaz"


# --- authorize_decision (owner-root-guard + scopes) ------------------------


def _owner() -> Identity:
    return Identity(actor_id="serkan_eryilmaz", display_name="Serkan Eryılmaz", authority_source=AUTHORITY_OWNER)


def _delegate(scopes: frozenset[str]) -> Identity:
    return Identity(actor_id="delegate_1", display_name="Delegate 1", authority_source=AUTHORITY_DELEGATE, scopes=scopes)


def test_owner_can_approve_any_risk_level():
    for risk_level in ("low", "medium", "high", "irreversible", None, "unknown-level"):
        scope = authorize_decision(_owner(), decision="approved", risk_level=risk_level)
        assert scope == "owner_root"


def test_owner_can_reject():
    assert authorize_decision(_owner(), decision="rejected", risk_level="irreversible") == "owner_root"


def test_delegate_with_matching_scope_can_approve():
    delegate = _delegate(frozenset({SCOPE_APPROVE_LOW}))
    assert authorize_decision(delegate, decision="approved", risk_level="low") == SCOPE_APPROVE_LOW


def test_delegate_without_matching_scope_raises():
    delegate = _delegate(frozenset({SCOPE_APPROVE_LOW}))
    with pytest.raises(AuthorizationError) as excinfo:
        authorize_decision(delegate, decision="approved", risk_level="high")
    assert excinfo.value.reason_code == REASON_CODE_AUTHZ_INSUFFICIENT_SCOPE


def test_delegate_cannot_approve_irreversible_even_without_config_restriction():
    """Root-guard: delegate'in config'inde `approve:irreversible` OLSA
    BILE, `risk_level="irreversible"` onayi YALNIZCA owner'a acik --
    bu, kod seviyesinde, config'ten BAGIMSIZ uygulanir."""
    delegate = _delegate(frozenset({SCOPE_APPROVE_IRREVERSIBLE}))
    with pytest.raises(AuthorizationError, match="SAHIBI-ONLY") as excinfo:
        authorize_decision(delegate, decision="approved", risk_level="irreversible")
    assert excinfo.value.reason_code == REASON_CODE_AUTHZ_OWNER_ONLY


def test_delegate_cannot_approve_unknown_risk_level_even_with_irreversible_scope():
    """Bilinmeyen/None risk_level de en kisitlayici (`approve:irreversible`)
    kapsama duser -- delegate'in config'inde bu scope olsa bile owner-only kalir."""
    delegate = _delegate(frozenset({SCOPE_APPROVE_IRREVERSIBLE, SCOPE_APPROVE_LOW, SCOPE_APPROVE_MEDIUM, SCOPE_APPROVE_HIGH}))
    with pytest.raises(AuthorizationError):
        authorize_decision(delegate, decision="approved", risk_level=None)


def test_delegate_with_reject_scope_can_reject():
    delegate = _delegate(frozenset({SCOPE_REJECT}))
    assert authorize_decision(delegate, decision="rejected", risk_level="irreversible") == SCOPE_REJECT


def test_delegate_without_reject_scope_raises():
    delegate = _delegate(frozenset({SCOPE_APPROVE_LOW}))
    with pytest.raises(AuthorizationError):
        authorize_decision(delegate, decision="rejected", risk_level="low")


def test_authorize_decision_invalid_decision_raises_value_error():
    with pytest.raises(ValueError, match="gecersiz decision"):
        authorize_decision(_owner(), decision="maybe", risk_level="low")


# --- B051 (BACKLOG.md B051, PLAN.md T50): hash_token + TokenRevocationStore --


def test_hash_token_is_deterministic_and_not_reversible_lookalike():
    assert hash_token("secret-123") == hash_token("secret-123")
    assert hash_token("secret-123") != "secret-123"
    assert hash_token("secret-123") != hash_token("secret-124")


def test_revocation_store_is_revoked_false_for_unknown_token(tmp_path):
    store = TokenRevocationStore(tmp_path / "revoked.jsonl")
    assert store.is_revoked("never-seen") is False


def test_revocation_store_revoke_then_is_revoked_true(tmp_path):
    store = TokenRevocationStore(tmp_path / "revoked.jsonl")
    store.revoke("tok-abc", actor_id="delegate_1", revoked_by="serkan_eryilmaz", reason="revoked")
    assert store.is_revoked("tok-abc") is True
    assert store.is_revoked("tok-xyz") is False


def test_revocation_store_only_persists_hash_never_raw_token(tmp_path):
    """B051 kabul kriteri -- ham token DEGERI hicbir diske yazilan
    dosyada GORUNMEMELI."""
    log_path = tmp_path / "revoked.jsonl"
    store = TokenRevocationStore(log_path)
    store.revoke("super-secret-raw-token", actor_id="delegate_1", revoked_by="owner", reason="revoked")
    raw_contents = log_path.read_text(encoding="utf-8")
    assert "super-secret-raw-token" not in raw_contents
    assert hash_token("super-secret-raw-token") in raw_contents


def test_revocation_store_persists_across_new_instances(tmp_path):
    """Kalicilik kanit -- YENI bir TokenRevocationStore ornegi (ayni
    yolu isaret eden) da AYNI iptali gormeli (bellek-ici DEGIL, disk-tabanli)."""
    log_path = tmp_path / "revoked.jsonl"
    TokenRevocationStore(log_path).revoke("tok-abc", actor_id="d1", revoked_by="owner", reason="revoked")
    fresh_store = TokenRevocationStore(log_path)
    assert fresh_store.is_revoked("tok-abc") is True


def test_revocation_store_tolerates_malformed_lines(tmp_path):
    log_path = tmp_path / "revoked.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("not valid json\n\n", encoding="utf-8")
    store = TokenRevocationStore(log_path)
    assert store.is_revoked("anything") is False  # coke DEGIL, sessizce bos sayar


def test_revocation_store_missing_file_yields_not_revoked(tmp_path):
    store = TokenRevocationStore(tmp_path / "does-not-exist.jsonl")
    assert store.is_revoked("anything") is False


# --- B051: IdentityStore.rotate_token / revoke_actor ------------------------


def test_identity_store_without_revocation_store_still_authenticates_normally():
    """Geriye uyumluluk -- `revocation_store` verilmezse (mevcut testlerin
    coğunluğu, `IdentityStore({token: Identity})`) davranis DEGISMEZ."""
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    assert store.authenticate("tok").actor_id == "a"


def test_rotate_token_happy_path_old_token_rejected_new_token_works(tmp_path):
    revocation_store = TokenRevocationStore(tmp_path / "revoked.jsonl")
    store = IdentityStore(
        {"old-tok": Identity(actor_id="delegate_1", display_name="D1", authority_source=AUTHORITY_DELEGATE, scopes=frozenset({SCOPE_APPROVE_LOW}))},
        revocation_store=revocation_store,
    )
    # Rotasyon ONCESI: eski token calisir.
    assert store.authenticate("old-tok").actor_id == "delegate_1"

    new_token = store.rotate_token("delegate_1", revoked_by="serkan_eryilmaz")
    assert new_token != "old-tok"
    assert len(new_token) > 20  # secrets.token_urlsafe(32) -- kisa/tahmin edilebilir DEGIL

    # Rotasyon SONRASI: eski token GERCEKTEN reddedilir (B051 kabul kriteri).
    with pytest.raises(AuthenticationError) as excinfo:
        store.authenticate("old-tok")
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_REVOKED

    # Yeni token CALISIR ve AYNI kimlige (scope/authority_source DAHIL) esler.
    identity = store.authenticate(new_token)
    assert identity.actor_id == "delegate_1"
    assert identity.authority_source == AUTHORITY_DELEGATE
    assert identity.has_scope(SCOPE_APPROVE_LOW)


def test_rotate_token_persists_revocation_across_a_simulated_restart(tmp_path):
    """B051 kabul kriteri -- 'sunucu yeniden baslasa BILE eski token
    GECERSIZ kalmali'. Burada GERCEK bir restart, IKINCI bir
    IdentityStore ornegi (AYNI eski token'i HALA env'den yukleyen) ile
    simule edilir."""
    revocation_path = tmp_path / "revoked.jsonl"
    store1 = IdentityStore(
        {"old-tok": Identity(actor_id="delegate_1", display_name="D1", authority_source=AUTHORITY_DELEGATE)},
        revocation_store=TokenRevocationStore(revocation_path),
    )
    store1.rotate_token("delegate_1", revoked_by="owner")

    # "Restart" -- operator henuz env degiskenini GUNCELLEMEDI, bu yuzden
    # YENI surec HALA eski token'i yukluyor (gercekci senaryo).
    store2 = IdentityStore(
        {"old-tok": Identity(actor_id="delegate_1", display_name="D1", authority_source=AUTHORITY_DELEGATE)},
        revocation_store=TokenRevocationStore(revocation_path),
    )
    with pytest.raises(AuthenticationError) as excinfo:
        store2.authenticate("old-tok")
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_REVOKED


def test_rotate_token_unknown_actor_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(UnknownActorError):
        store.rotate_token("does-not-exist", revoked_by="owner")


def test_revoke_actor_happy_path_no_new_token_issued(tmp_path):
    revocation_store = TokenRevocationStore(tmp_path / "revoked.jsonl")
    store = IdentityStore(
        {"tok": Identity(actor_id="delegate_1", display_name="D1", authority_source=AUTHORITY_DELEGATE)},
        revocation_store=revocation_store,
    )
    store.revoke_actor("delegate_1", revoked_by="serkan_eryilmaz")
    with pytest.raises(AuthenticationError) as excinfo:
        store.authenticate("tok")
    assert excinfo.value.reason_code == REASON_CODE_AUTH_TOKEN_REVOKED


def test_revoke_actor_unknown_actor_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(UnknownActorError):
        store.revoke_actor("does-not-exist", revoked_by="owner")


def test_rotate_token_without_revocation_store_still_rotates_in_memory():
    """`revocation_store=None` (varsayilan) durumunda bile rotate_token
    bellek-ici eslemeyi gunceller -- yalnizca KALICI iptal kaydi
    ATLANIR (disk erisimi yok)."""
    store = IdentityStore({"old-tok": Identity(actor_id="d1", display_name="D1", authority_source=AUTHORITY_DELEGATE)})
    new_token = store.rotate_token("d1", revoked_by="owner")
    assert store.authenticate(new_token).actor_id == "d1"
    # revocation_store olmadigi icin eski token'in KALICI iptali YOK, ama
    # bellek-ici eslemeden zaten SILINDI, bu yuzden yine de eslemez.
    with pytest.raises(AuthenticationError):
        store.authenticate("old-tok")
