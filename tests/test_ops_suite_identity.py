"""PLAN.md T28, BACKLOG.md B044 (SECURITY P0) -- ops_suite.identity testleri:
kimlik yukleme (`load_identities`), token dogrulama (`IdentityStore.authenticate`),
kapsam/owner-root-guard yetkilendirmesi (`authorize_decision`). Tamami ag/dosya
sistemi olmadan calisan saf birim testleridir (env sozlugu elle insa edilir)."""

from __future__ import annotations

import pytest

from ops_suite.identity import (
    AUTHORITY_DELEGATE,
    AUTHORITY_OWNER,
    SCOPE_APPROVE_HIGH,
    SCOPE_APPROVE_IRREVERSIBLE,
    SCOPE_APPROVE_LOW,
    SCOPE_APPROVE_MEDIUM,
    SCOPE_REJECT,
    AuthenticationError,
    AuthorizationError,
    Identity,
    IdentityStore,
    authorize_decision,
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
    with pytest.raises(AuthenticationError):
        store.authenticate(None)


def test_authenticate_empty_token_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(AuthenticationError):
        store.authenticate("")


def test_authenticate_unknown_token_raises():
    store = IdentityStore({"tok": Identity(actor_id="a", display_name="A", authority_source=AUTHORITY_OWNER)})
    with pytest.raises(AuthenticationError):
        store.authenticate("wrong-token")


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
    with pytest.raises(AuthorizationError):
        authorize_decision(delegate, decision="approved", risk_level="high")


def test_delegate_cannot_approve_irreversible_even_without_config_restriction():
    """Root-guard: delegate'in config'inde `approve:irreversible` OLSA
    BILE, `risk_level="irreversible"` onayi YALNIZCA owner'a acik --
    bu, kod seviyesinde, config'ten BAGIMSIZ uygulanir."""
    delegate = _delegate(frozenset({SCOPE_APPROVE_IRREVERSIBLE}))
    with pytest.raises(AuthorizationError, match="SAHIBI-ONLY"):
        authorize_decision(delegate, decision="approved", risk_level="irreversible")


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
