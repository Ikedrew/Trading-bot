"""Research Engine AWS credential/profile resolution tests (no real AWS).

Proves the sanctioned precedence in research_engine.data_access.s3_source:

Priority 1: RESEARCH_AWS_PROFILE=<profile>  → boto3 Session built with that
            NAMED profile (SSO/session credentials), resolved eagerly.
Priority 2: unset/empty                      → standard boto3 chain, no forced
            profile (AWS_PROFILE / env / shared config / EC2 instance role).
Priority 3: any S3/credential failure        → actionable ResearchDataSourceError;
            never a local fallback, never a silent account switch.

All boto3 construction is mocked via the `s3_source._build_session` dependency
hook; nothing here touches the network or real AWS. No secrets appear anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from research_engine.data_access import s3_source
from research_engine.data_access.s3_source import ResearchDataSourceError

# ─── helpers ───────────────────────────────────────────────────────────────────


class _StubClient:
    """Minimal S3 client stub: empty listings, hard miss on get_object."""

    def list_objects_v2(self, **kwargs):
        return {"Contents": [], "IsTruncated": False}

    def get_object(self, **kwargs):
        raise KeyError(kwargs["Key"])


class _DeniedClient(_StubClient):
    def __init__(self, error):
        self._error = error

    def list_objects_v2(self, **kwargs):
        raise self._error


def _err(cls_name: str, message: str):
    """Create an exception whose type NAME matches a botocore class name.

    Keeps tests independent of exact botocore exception constructor signatures
    while exercising the class-name-based diagnostics exactly as production
    would behave.
    """
    return type(cls_name, (Exception,), {})(message)


def _client_error(code: str, message: str):
    exc = type("ClientError", (Exception,), {})(message)
    exc.response = {"Error": {"Code": code, "Message": message}}
    return exc


# ─── explicit RESEARCH_AWS_PROFILE ─────────────────────────────────────────────


def test_explicit_research_profile_builds_session_with_that_profile(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")
    recorded = {}

    class _Session:
        def get_credentials(self):
            return object()

        def client(self, service, **kw):
            recorded["client"] = (service, kw)
            return _StubClient()

    def _build(profile_, region_):
        recorded["profile"] = profile_
        recorded["region"] = region_
        return _Session()

    monkeypatch.setattr(s3_source, "_build_session", _build)

    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    assert src.research_profile == "test-profile"
    assert src.read_dataset("trade_truth") == []
    assert recorded["profile"] == "test-profile"
    assert recorded["region"] == "eu-west-2"
    assert recorded["client"] == ("s3", {"region_name": "eu-west-2"})


def test_explicit_profile_with_env_region_uses_aws_region(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    recorded = {}

    class _Session:
        def get_credentials(self):
            return object()

        def client(self, service, **kw):
            return _StubClient()

    def _build(profile_, region_):
        recorded["region"] = region_
        return _Session()

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    src.read_dataset("trade_truth")
    assert recorded["region"] == "eu-west-1"  # existing AWS_REGION semantics kept


# ─── standard chain fallback (EC2 instance-role compatibility) ────────────────


def test_standard_chain_when_research_profile_unset(monkeypatch):
    """RESEARCH_AWS_PROFILE unset → plain Session; ec2/IMDS default chain intact."""
    monkeypatch.delenv("RESEARCH_AWS_PROFILE", raising=False)
    recorded = {}

    class _Session:
        def client(self, service, **kw):
            return _StubClient()

    def _build(profile_, region_):
        recorded["profile"] = profile_
        recorded["region"] = region_
        return _Session()

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    assert src.research_profile is None
    assert src.read_dataset("trade_truth") == []
    assert recorded["profile"] is None  # nothing forced → default chain preserved


def test_standard_chain_does_not_eagerly_fetch_credentials(monkeypatch):
    """EC2 laziness: the default-chain path must never force a credential fetch."""
    monkeypatch.delenv("RESEARCH_AWS_PROFILE", raising=False)
    seen = {}

    class _Session:
        def get_credentials(self):
            seen["creds"] = True
            return object()

        def client(self, service, **kw):
            return _StubClient()

    monkeypatch.setattr(s3_source, "_build_session", lambda p, r: _Session())
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    src.read_dataset("trade_truth")
    assert "creds" not in seen  # stays lazy: IMDS/instance-role chain untouched
    assert src.read_dataset("decision_trace") == []


def test_existing_aws_profile_environment_not_overridden(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "ext-profile")
    monkeypatch.delenv("RESEARCH_AWS_PROFILE", raising=False)
    recorded = {}

    class _Session:
        def client(self, service, **kw):
            return _StubClient()

    def _build(profile_, region_):
        recorded["profile"] = profile_
        return _Session()

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    src.read_dataset("trade_truth")
    # We pass NO profile override → boto3 honours the ambient AWS_PROFILE env.
    assert recorded["profile"] is None


def test_default_chain_constructs_plain_session_no_profile():
    """Real _build_session(None, region): a bare Session, no forced profile."""
    try:
        import boto3
    except ImportError:
        pytest.skip("boto3 not installed")
    try:
        session = s3_source._build_session(None, "eu-west-2")
    except Exception as exc:  # pragma: no cover - weird local AWS config
        pytest.skip(f"bare Session construction not possible here: {exc!r}")
    assert isinstance(session, boto3.Session)
    # The plain default chain is used with NO forced profile_name override: the
    # profile is exactly what the ambient environment selects (AWS_PROFILE env,
    # or boto3's 'default' section when AWS_PROFILE is unset). On EC2/VM with no
    # env/keys this same bare chain falls through to the instance-role via IMDS.
    assert session.profile_name == os.environ.get("AWS_PROFILE", "default")
    assert session.region_name == "eu-west-2"


# ─── failure diagnostics ──────────────────────────────────────────────────────


def test_invalid_profile_surfaces_actionable_error_not_sso(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "does-not-exist")

    def _build(profile_, region_):
        raise _err("ProfileNotFound", f"The config profile ({profile_}) could not be found")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    assert "does-not-exist" in msg
    assert "does not exist" in msg
    assert "aws sso login" not in msg  # a missing profile is not an SSO-expiry


def test_access_denied_raises_error_no_local_fallback(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")
    denied = _DeniedClient(_client_error("AccessDenied", "Access Denied"))

    class _Session:
        def get_credentials(self):
            return object()

        def client(self, service, **kw):
            return denied

    monkeypatch.setattr(s3_source, "_build_session", lambda p, r: _Session())
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    assert "DENIED" in msg
    assert "AccessDenied" in msg
    assert "test-bucket" in msg
    assert "aws sso login" not in msg  # permissions problem, not SSO expiry


def test_access_denied_at_standard_chain_also_raises(monkeypatch):
    monkeypatch.delenv("RESEARCH_AWS_PROFILE", raising=False)

    def _build(profile_, region_):
        raise _client_error("AccessDenied", "Access Denied (default chain)")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    assert "DENIED" in str(ei.value)
    assert "test-bucket" in str(ei.value)


def test_dedicated_denial_exception_class_is_detected(monkeypatch):
    """Newer botocore raises dedicated classes whose NAME is the denial code."""
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")

    def _build(profile_, region_):
        raise _err("AccessDenied", "An error occurred (AccessDenied) when calling the ListObjectsV2 operation: Access Denied")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    assert "DENIED" in msg
    assert "test-bucket" in msg
    assert "NOT an SSO-expiry" in msg
    assert "aws sso login" not in msg


def test_expired_sso_diagnostic_is_actionable(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "trading-bot-new")
    secret = "AKIAFAKE-SECRET-VALUE-NEVER-LOG"

    def _build(profile_, region_):
        raise _err("SSOTokenLoadError", f"could not load SSO token for {profile_}: {secret}")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    assert "aws sso login --profile trading-bot-new" in msg
    assert secret not in msg  # underlying secret must never reach the diagnostic


def test_sso_credential_retrieval_expiry_hint(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "trading-bot-new")

    def _build(profile_, region_):
        raise _err(
            "CredentialRetrievalError",
            "Error when retrieving credentials from sso: Token has expired and refresh failed",
        )

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    assert "aws sso login --profile trading-bot-new" in str(ei.value)


def test_network_error_not_mislabelled_sso(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "trading-bot-new")

    def _build(profile_, region_):
        raise _err("ConnectionError", "timed out reaching s3.eu-west-2.amazonaws.com")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    assert "aws sso login" not in msg
    assert "timed out" in msg


def test_profile_with_no_credentials_fails_explicitly(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")
    client_ct = {}

    class _Session:
        def get_credentials(self):
            return None

        def client(self, service, **kw):
            client_ct["called"] = True
            return _StubClient()

    monkeypatch.setattr(s3_source, "_build_session", lambda p, r: _Session())
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    assert "no credentials" in str(ei.value)
    assert "client" not in client_ct  # never even attempted an S3 call


# ─── security: no secrets / no hard-coded profile ─────────────────────────────


def test_secret_values_never_leak_into_diagnostics(monkeypatch):
    monkeypatch.setenv("RESEARCH_AWS_PROFILE", "test-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-SECRET-KEY-VALUE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret-secret-value")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "session-token-value")

    def _build(profile_, region_):
        raise _err("NoCredentialsError", "Unable to locate credentials")

    monkeypatch.setattr(s3_source, "_build_session", _build)
    src = s3_source.S3ResearchDataSource(bucket="test-bucket")
    with pytest.raises(ResearchDataSourceError) as ei:
        src.read_dataset("trade_truth")
    msg = str(ei.value)
    for secret in ("AKIA-SECRET-KEY-VALUE", "super-secret-secret-value", "session-token-value"):
        assert secret not in msg


def test_no_hardcoded_user_profile_or_keys_in_s3_source():
    path = (
        Path(__file__).resolve().parent.parent
        / "research_engine" / "data_access" / "s3_source.py"
    )
    src = path.read_text(encoding="utf-8")
    assert "trading-bot-new" not in src
    assert "AKIA" not in src
    assert "RESEARCH_AWS_PROFILE" in src  # the documented env key is present


def test_config_exposes_research_profile_key():
    import core.config as cfg

    assert hasattr(cfg, "RESEARCH_AWS_PROFILE")
    assert cfg.RESEARCH_AWS_PROFILE == os.environ.get("RESEARCH_AWS_PROFILE", "")