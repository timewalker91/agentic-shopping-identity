"""Agent authentication: only an agent holding its registered key gets a passport (RFC 7523 assertion)."""
from agentpass.agent_identity import ASSERTION_AUDIENCE, make_assertion
from agentpass.clock import now_epoch
from agentpass.signer import LocalSigner
from agentpass.tokens import encode_jwt


def reason(resp) -> str:
    return resp.json().get("reason", "")


def test_signed_assertion_gets_a_passport_naming_both_identities(harness):
    sid = harness.new_session()
    resp = harness.exchange(sid)
    assert resp.status_code == 200, resp.text
    claims = resp.json()["claims"]
    assert claims["act"] == {"sub": "agent_shopper_v1", "sub_profile": "ai_agent"}
    assert claims["sub"] != "agent_shopper_v1"  # sub is the human


def test_bare_agent_name_is_not_enough(harness):
    sid = harness.new_session()
    resp = harness.exchange(sid, actor_token="agent_shopper_v1",
                            actor_token_type="urn:agentpass:params:oauth:token-type:agent-id")
    assert resp.status_code == 400 and reason(resp) == "actor_token_required"


def test_assertion_signed_by_another_key_is_rejected(harness):
    sid = harness.new_session()
    impostor = make_assertion(LocalSigner(), "agent_shopper_v1")
    resp = harness.exchange(sid, actor_token=impostor)
    assert resp.status_code == 401 and reason(resp) == "invalid_actor_token"


def test_replayed_assertion_is_rejected(harness):
    sid = harness.new_session()
    assertion = harness.assertion()
    assert harness.exchange(sid, actor_token=assertion).status_code == 200
    resp = harness.exchange(sid, actor_token=assertion)
    assert resp.status_code == 401 and reason(resp) == "actor_token_replayed"


def test_unknown_agent_is_rejected(harness):
    sid = harness.new_session()
    resp = harness.exchange(sid, actor_token=make_assertion(harness.agent_signer, "agent_evil"))
    assert resp.status_code == 401 and reason(resp) == "unknown_agent"


def test_long_lived_assertion_is_rejected(harness):
    sid = harness.new_session()
    now = now_epoch()
    long_lived = encode_jwt(harness.agent_signer, {
        "iss": "agent_shopper_v1", "sub": "agent_shopper_v1", "aud": ASSERTION_AUDIENCE,
        "iat": now, "exp": now + 3600, "jti": "long-lived-1",
    })
    resp = harness.exchange(sid, actor_token=long_lived)
    assert resp.status_code == 401 and reason(resp) == "invalid_actor_token"


def test_failed_agent_authentication_is_audited(harness):
    sid = harness.new_session()
    harness.exchange(sid, actor_token=make_assertion(LocalSigner(), "agent_shopper_v1"))
    events = harness.audit(sid)
    assert any("agent authentication failed" in e["reason"] for e in events)
