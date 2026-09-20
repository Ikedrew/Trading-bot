"""
POST-SIGN-OFF HARDENING 1.3 — BASELINE CONFIG HASH MATERIAL COVERAGE.

Focused proof tests for compute_config_hash() / _get_material_params() in
core/research_events.py.

Proves:
    1. Declared coverage is exact: payload keys == _MATERIAL_CONFIG_PARAMS.
    2. Every core/config.py value is classified exactly once (material XOR
       non-material). A newly added config value fails this suite loudly —
       the fail-closed fix for silent hash omission.
    3. Changing ANY material parameter changes config_hash (parameterised).
    4. Changing representative NON-MATERIAL config does NOT move the hash.
    5. No secrets/credentials enter the hash payload (keys or values).
    6. Canonical ordering/serialization is deterministic (incl. the
       unordered-set RR3_PATTERNS across PYTHONHASHSEED values).
    7. Identical material config produces an identical hash.
    8. Historical persisted baselines remain readable + unchanged; a
       pre-widening (legacy) hash fails closed as stale_config (never
       rewritten, never silently matched).

No trading settings are touched (monkeypatch restores everything).
No staging/commit/push; all persistence is tmp_path.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _s3_fake import install_fake_s3

import core.research_events as events_mod
from core.research_events import (
    _MATERIAL_CONFIG_PARAMS,
    _canonicalise_material_value,
    _get_material_params,
    compute_config_hash,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION INVENTORY — the deliberate non-material side of the contract.
# Every entry: "NAME — reason". Promote to material by moving the name into
# _MATERIAL_CONFIG_PARAMS in core/research_events.py ( flips the partition
# test below immediately).
# ═══════════════════════════════════════════════════════════════════════════════
_NON_MATERIAL_CONFIG_REGISTRY: tuple[str, ...] = (
    # ── Presentation / logging only ──
    "PRINT_MODE",                # startup label only (main.py log line)
    "DRY_RUN_EXECUTION_LOGS",    # log verbosity (execution/mt5_execution.py:637)
    "ESSENTIAL_LOGS",            # log verbosity switch (inert today)
    "FULL_DEBUG_REPLAY",         # replay debug verbosity
    "FULL_DEBUG_LIVE",           # log verbosity (inert)
    "TICK_READY_LOGS",           # log verbosity (inert)
    "Q_MODULE_DEBUG_LOGS",       # log verbosity (inert)
    "METRICS_ENABLED",           # metrics switch (inert)
    "LEGACY_DECISION_LOGS",      # logger formatting only
    # ── Audit / observability persistence + switches ──
    "DECISION_AUDIT_ENABLED",    # whether the audit trail is written
    "DECISION_AUDIT_INCLUDE_REJECTIONS",  # audit content detail only
    "DECISION_AUDIT_FLUSH_EVERY_WRITE",   # audit IO cadence
    "RESEARCH_ASSESSMENT_LOGGING",        # dual-EV comparison logging only
    "MARKET_CONTEXT_SCORING_ENABLED",     # Phase-3 SCORING flag: inert today
    "MARKET_CONTEXT_PERSISTENCE_ENABLED", # market-context persistence only
    "PORTFOLIO_RANKING_AUTHORITY",        # reporting-only reader flag
    "PORTFOLIO_RANKING_SHADOW_LOG",       # shadow ranking log verbosity
    "DASHBOARD_INCLUDE_PNL_METRICS",      # dashboard payload content only
    "DASHBOARD_EMIT_DAILY_SUMMARY",       # dashboard emission cadence
    "EQUITY_CURVE_ENABLED",      # equity snapshot writer switch
    "SHARPE_DECAY_THRESHOLD",    # alerting threshold (informational)
    "SLIPPAGE_MONITORING_ENABLED",  # monitoring switch
    "SLIPPAGE_ALERT_THRESHOLD_PIPS",  # alerting threshold only
    "SLIPPAGE_MAX_HISTORY",      # monitor buffer size
    "NO_TRADE_ALERT_THRESHOLD",  # health alerting cadence
    "NO_TRADE_ALERT_REPEAT_INTERVAL",  # health alerting cadence
    "CHECKPOINT_INTERVAL_CYCLES",  # durable-checkpoint cadence
    "OBSERVABILITY_VALIDATION_ENABLED",  # validation switch (inert)
    # ── Persistence locations (where state is stored, never what it means) ──
    "DECISION_AUDIT_DIR",
    "EVENT_STREAM_DIR",
    "SHADOW_RUNTIME_DIR",
    "ENGINE_STATE_PERSIST_DIR",
    "POSITION_EXCURSION_DIR",
    "REPLAY_CACHE_DIR",
    "DAILY_LOSS_STATE_FILE",
    "DAILY_TRADE_LIMIT_STATE_FILE",
    "TRADE_COOLDOWN_STATE_FILE",
    "EQUITY_CURVE_FILE",
    "SLIPPAGE_JOURNAL_FILE",
    "HEARTBEAT_FILE",
    "CHALLENGE_PROGRESS_FILE",
    "CONSISTENCY_STATE_FILE",
    "WEEKEND_STATE_FILE",
    # ── State lifetime / recovery plumbing (persistence semantics, not policy) ──
    "ENGINE_STATE_WARM_START_ENABLED",
    "ENGINE_STATE_MAX_AGE_SECONDS",
    "POSITION_EXCURSION_MAX_AGE_SECONDS",
    "POSITION_EXCURSION_S3_MIRROR",
    # ── AWS / S3 / event plumbing ──
    "EVENT_STREAM_ENABLED",
    "EVENT_STREAM_S3_MIRROR",
    "NEW_RUNTIME_S3_BUCKET",
    "RESEARCH_AWS_PROFILE",
    "LIVE_MODE",
    "ALLOW_PRODUCTION_S3_WRITE",
    "ADAPTER_MODE",
    # ── Alerting / watchdogs / process supervision ──
    "ALERTING_ENABLED",
    "LEGACY_DISCORD_ENABLED",  # alerting-channel routing switch only
    "ENABLE_DISCORD_V2",       # alerting-channel routing switch only
    "HEARTBEAT_ENABLED",
    "WATCHDOG_POLL_INTERVAL_SECONDS",
    "HEARTBEAT_STALE_THRESHOLD_SECONDS",
    "MAX_RESTARTS_PER_HOUR",
    "BOT_START_COMMAND",
    "ENGINE_STATE_STRICT_VALIDATION",
    "STRICT_RISK_COVERAGE",
    # ── MT5 terminal plumbing (connection + reconnect policy) ──
    "MT5_CENTRALISED_INIT",
    "MT5_TERMINAL_PATH",
    "MT5_TERMINAL_PORTABLE",
    "MT5_TERMINAL_MANAGER_ENABLED",
    "MT5_RECONNECT_COOLDOWN_SECONDS",
    "MT5_RECONNECT_MAX_COOLDOWN_SECONDS",
    # ── Feed-health telemetry (fault behaviour, not strategy rules) ──
    "STALE_TICK_TIMEOUT_SECONDS",
    "STALE_CANDLE_TIMEOUT_SECONDS",
    "MARKET_HEARTBEAT_TIMEOUT_SECONDS",
    "STALE_ESCALATION_WARNING_SECONDS",
    "STALE_ESCALATION_CRITICAL_SECONDS",
    "LIVENESS_STALL_THRESHOLD_SECONDS",
    "FEED_STALE_THRESHOLD_SECONDS",
    # ── Poll cadence (evaluation timing, not decision rules) ──
    "POLL_SECONDS",
    # ── Shadow / replay / evaluation runners (offline/research routing) ──
    "SHADOW_RUNTIME_V2_ENABLED",   # shadow data capture route, not decisions
    "ENABLE_LEGACY_SHADOW_PIPELINE",  # offline evaluation/legacy shadow
    "MTF_SHADOW_MODE",             # evaluation runner mode, not live pipeline
    "ENABLE_CANDLE_REPLAY_CACHE",  # local replay cache only
    "REPLAY_START_TIME",           # replay-run window argument
    "REPLAY_END_TIME",             # replay-run window argument
    # ── Legacy aliases / code paths superseded by the canonical value ──
    "SYMBOL",             # legacy scalar; CANONICAL_SYMBOLS is hashed
    "SYMBOLS",            # legacy list; CANONICAL_SYMBOLS is hashed
    "USE_NEW_PIPELINE",   # docstring-only today (new engine is sole authority)
    "ALLOW_LEGACY_FALLBACK",  # inert
    "VALIDATION_MODE",    # inert
    # ── Declared but not wired into any production decision path (inert today;
    #    promoting any of these to live MUST also promote it to material) ──
    "USE_EMPIRICAL_PROBABILITY",  # doc/test refs only; live EV uses synthetic
    "PATTERN_CONFIRMATION_ENABLED",
    "MTF_H4_RANGING_SCORE_PENALTY",
    "MTF_H4_VOLATILE_MIN_SCORE_INCREASE",
    "MTF_H1_ALIGNED_BONUS",
    "MTF_H1_NEUTRAL_MIN_SCORE_INCREASE",
    "MTF_H1_CONTRADICTION_THRESHOLD",  # tools-only constant today
    "MTF_M15_MIN_STRUCTURE_QUALITY",   # tools-only constant today
    "MTF_M15_HIGH_QUALITY_THRESHOLD",
    "MTF_M15_HIGH_QUALITY_BONUS",
    "POSITION_EVICTION_ENABLED",       # reconciliation hygiene, not gating
    "POSITION_EVICTION_DELAY_SECONDS",
    "POSITION_EVICTION_CHECK_INTERVAL",  # inert
    # ── ACCOUNT / BROKER / DEPLOYMENT POLICY (class C): intentionally OUT of
    #    baseline config identity. Represented elsewhere (snapshot.environment;
    #    core/accounts/) and never merged into the config hash. ──
    "STRATEGY_NAME",          # strategy label (identity metadata, not a rule)
    "BOT_MAGIC",              # account/broker position identity (environment)
    "MAGIC_NUMBER_REGISTRY",  # account/broker position identity (environment)
    "SYMBOL_ALIASES",         # broker-specific symbol names (MT5 boundary)
    "CHALLENGE_START_DATE",   # challenge calendar (account contract)
    "CHALLENGE_END_DATE",     # challenge calendar (account contract)
    "CHALLENGE_START_EQUITY",  # account starting balance (account state)
    "MIN_TRADING_DAYS",       # challenge compliance day-count (contract)
)


# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS / SECRETS — enumerated separately so the test NEVER touches them.
# These names are intentionally ABSENT from both classification tuples above:
#   - mutating them in a test process would handle live secret values;
#   - they must never enter the hash payload (enforced by tests below against
#     the live module attributes, not by setting them).
# DISCORD_BOT_TOKEN, DISCORD_WEBHOOK_URL, DISCORD_WEBHOOKS,
# DISCORD_LIVE_CHANNELS, DISCORD_V2_CHANNELS
# ═══════════════════════════════════════════════════════════════════════════════
_UNTOUCHABLE_SECRET_NAMES: tuple[str, ...] = (
    "DISCORD_BOT_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "DISCORD_WEBHOOKS",
    "DISCORD_LIVE_CHANNELS",
    "DISCORD_V2_CHANNELS",
)


def _module_config_names() -> list[str]:
    """All module-level ASSIGNED names in core/config.py, via AST (no import).

    Imported symbols (e.g. ``DATA_CONTRACT_VERSION`` from
    core.production_data_contract) are ``ImportFrom`` bindings, deliberately
    EXCLUDED here: they are not configuration values and can never enter the
    hash payload.
    """
    src = Path("core/config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


def _changed_value(name, current):
    """A same-type-but-different probe value for a material parameter."""
    if isinstance(current, bool):
        return not current
    if isinstance(current, int):
        return current + 1
    if isinstance(current, float):
        return current + 1.0
    if isinstance(current, str):
        return current + "_H13_PROBE"
    if isinstance(current, frozenset):
        return frozenset(set(current) | {"H13_PROBE"})
    if isinstance(current, set):
        return set(current) | {"H13_PROBE"}
    if isinstance(current, list):
        return list(current) + ["H13_PROBE"]
    if isinstance(current, tuple):
        return tuple(current) + ("H13_PROBE",)
    if isinstance(current, dict):
        probe = dict(current)
        probe["H13_PROBE"] = "H13_PROBE_VALUE"
        return probe
    if current is None:
        # Absent-attribute sentinel: the only "changed" value is any real value.
        return "H13_PROBE_PRESENT"
    raise AssertionError(f"no probe strategy for {name} ({type(current).__name__})")


def _changed_nonmaterial_value(name, current):
    """A different value for a non-material parameter (must NOT move the hash)."""
    if isinstance(current, bool):
        return not current
    if isinstance(current, int):
        return current + 1
    if isinstance(current, float):
        return current + 1.0
    if isinstance(current, str):
        return (current or "") + "_H13_NONMATERIAL_PROBE"
    if isinstance(current, frozenset):
        return frozenset(set(current) | {"H13_PROBE_NM"})
    if isinstance(current, set):
        return set(current) | {"H13_PROBE_NM"}
    if isinstance(current, list):
        return list(current) + ["H13_PROBE_NM"]
    if isinstance(current, tuple):
        return tuple(current) + ("H13_PROBE_NM",)
    if isinstance(current, dict):
        probe = dict(current)
        probe["H13_PROBE_NM"] = "H13_PROBE_NM_VALUE"
        return probe
    if current is None:
        return "H13_PROBE_NM_PRESENT"
    raise AssertionError(f"no probe strategy for {name} ({type(current).__name__})")


# ═══════════════════════════════════════════════════════════════════════════════
# 1 + 2. DECLARED COVERAGE IS EXACT; EVERY CONFIG VALUE CLASSIFIED EXACTLY ONCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeclaredCoverageIsExact:
    def test_material_params_declaration_has_no_duplicates(self):
        assert len(_MATERIAL_CONFIG_PARAMS) == len(set(_MATERIAL_CONFIG_PARAMS))

    def test_payload_keys_match_declaration_exactly(self):
        payload = _get_material_params()
        assert set(payload.keys()) == set(_MATERIAL_CONFIG_PARAMS)
        assert len(payload) == len(_MATERIAL_CONFIG_PARAMS)

    def test_every_config_value_classified_exactly_once(self):
        """Fail-closed classification: a new config value with no side fails.

        Credentials (``_UNTOUCHABLE_SECRET_NAMES``) are intentionally outside
        BOTH tuples: they belong to neither baseline identity nor the test
        non-material inventory — they must simply never enter the payload
        (enforced by the secret tests below).
        """
        all_names = _module_config_names()
        material = set(_MATERIAL_CONFIG_PARAMS)
        non_material = set(_NON_MATERIAL_CONFIG_REGISTRY)
        secrets = set(_UNTOUCHABLE_SECRET_NAMES)
        overlap = material & non_material
        assert not overlap, f"classified BOTH material and non-material: {sorted(overlap)}"
        assert not (material & secrets), "a secret is classified MATERIAL"
        assert not (non_material & secrets), "a secret is classified non-material"
        unclassified = [
            n for n in all_names
            if n not in material and n not in non_material and n not in secrets
        ]
        assert not unclassified, (
            "UNCLASSIFIED config values (add to _MATERIAL_CONFIG_PARAMS if "
            f"behavioural, else to the test registry): {unclassified}"
        )
        stray = [n for n in material if n not in all_names]
        assert not stray, f"material entries missing from core/config.py: {stray}"
        stray_nm = [n for n in non_material if n not in all_names]
        assert not stray_nm, f"registry entries missing from core/config.py: {stray_nm}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EVERY MATERIAL PARAMETER MOVES THE HASH
# ═══════════════════════════════════════════════════════════════════════════════

class TestEveryMaterialParameterMovesHash:
    @pytest.mark.parametrize("name", list(_MATERIAL_CONFIG_PARAMS))
    def test_material_change_moves_hash(self, monkeypatch, name):
        import core.config as cfg

        baseline = compute_config_hash()
        assert baseline not in ("", "UNKNOWN")

        current = getattr(cfg, name, None)
        monkeypatch.setattr(cfg, name, _changed_value(name, current), raising=False)
        assert compute_config_hash() != baseline, (
            f"MATERIAL parameter '{name}' changed but config_hash did not move — "
            "two materially different baselines would share a config_hash"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4 + 5. NON-MATERIAL CONFIG DOES NOT MOVE THE HASH; NO SECRETS IN THE PAYLOAD
# ═══════════════════════════════════════════════════════════════════════════════

_REPRESENTATIVE_NON_MATERIAL = (
    # formerly secret-shaped / alerting plumbing
    "LEGACY_DISCORD_ENABLED",
    "ENABLE_DISCORD_V2",
    # persistence locations
    "DAILY_LOSS_STATE_FILE",
    "WEEKEND_STATE_FILE",
    "POSITION_EXCURSION_DIR",
    "EVENT_STREAM_DIR",
    # infra / plumbing
    "NEW_RUNTIME_S3_BUCKET",
    "RESEARCH_AWS_PROFILE",
    "MT5_TERMINAL_PATH",
    "EVENT_STREAM_S3_MIRROR",
    "ADAPTER_MODE",
    "STALE_TICK_TIMEOUT_SECONDS",
    "POLL_SECONDS",
    "HEARTBEAT_FILE",
    "MAX_RESTARTS_PER_HOUR",
    "DECISION_AUDIT_ENABLED",
    "PORTFOLIO_RANKING_SHADOW_LOG",
    "MTF_SHADOW_MODE",
    "SHADOW_RUNTIME_V2_ENABLED",
    # inert
    "VALIDATION_MODE",
    "PATTERN_CONFIRMATION_ENABLED",
    "USE_EMPIRICAL_PROBABILITY",
    "MTF_H4_RANGING_SCORE_PENALTY",
    "POSITION_EVICTION_CHECK_INTERVAL",
    # legacy aliases
    "SYMBOL",
    "SYMBOLS",
    # account/broker/deployment policy (class C)
    "BOT_MAGIC",
    "SYMBOL_ALIASES",
    "CHALLENGE_START_EQUITY",
    "STRATEGY_NAME",
)

_SECRET_BEARING_NAMES = _UNTOUCHABLE_SECRET_NAMES


class TestNonMaterialAndSecrets:
    def test_non_material_sample_is_registered_non_material(self):
        registered = set(_NON_MATERIAL_CONFIG_REGISTRY)
        for name in _REPRESENTATIVE_NON_MATERIAL:
            assert name in registered, f"sampled '{name}' must stay non-material"

    @pytest.mark.parametrize("name", list(_REPRESENTATIVE_NON_MATERIAL))
    def test_non_material_change_does_not_move_hash(self, monkeypatch, name):
        import core.config as cfg

        baseline = compute_config_hash()
        current = getattr(cfg, name, None)
        monkeypatch.setattr(
            cfg, name, _changed_nonmaterial_value(name, current), raising=False
        )
        assert compute_config_hash() == baseline, (
            f"NON-MATERIAL parameter '{name}' changed config_hash — "
            "it must stay out of baseline identity"
        )

    def test_no_secret_bearing_keys_in_payload(self):
        payload = _get_material_params()
        for name in _SECRET_BEARING_NAMES:
            assert name not in payload
        sensitive_markers = ("token", "webhook", "secret", "password", "private")
        lowered_keys = [k.lower() for k in payload.keys()]
        for marker in sensitive_markers:
            assert not any(marker in k for k in lowered_keys), (
                f"payload key looks secret-bearing (*{marker}*): "
                f"{[k for k in payload.keys() if marker in k.lower()]}"
            )

    def test_secret_values_do_not_leak_into_hash_payload(self):
        """Secret VALUES never enter the payload — asserted read-only.

        The test does NOT mutate the secret attributes (monkeypatching them
        would handle live credential values in this process). It reads the live
        values (skipped when empty) and asserts they are absent from the
        serialised material payload.
        """
        import core.config as cfg

        secrets = [getattr(cfg, name, "") for name in _SECRET_BEARING_NAMES]
        live = [s for s in secrets if isinstance(s, str) and s]
        if not live:
            pytest.skip("no live secret values present to check against")
        payload = _get_material_params()
        serialised = json.dumps(payload, sort_keys=True, default=str)
        for secret in live:
            assert secret not in serialised


# ═══════════════════════════════════════════════════════════════════════════════
# 6 + 7. CANONICAL SERIALIZATION; IDENTICAL CONFIG → IDENTICAL HASH
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeterministicSerialization:
    def test_identical_config_identical_hash(self):
        assert compute_config_hash() == compute_config_hash()

    def test_hash_matches_canonical_serialization_contract(self):
        """Hash == sha256(sorted-key canonical JSON of the payload)[:16]."""
        params = _get_material_params()
        content = json.dumps(params, sort_keys=True, default=str)
        assert compute_config_hash() == hashlib.sha256(content.encode()).hexdigest()[:16]

    def test_key_order_does_not_matter(self):
        """Reversed-dict payload serialises to the same digest (sort_keys)."""
        params = _get_material_params()
        reversed_params = dict(reversed(list(params.items())))
        assert list(reversed_params.keys()) != list(params.keys())
        h1 = hashlib.sha256(
            json.dumps(params, sort_keys=True, default=str).encode()
        ).hexdigest()
        h2 = hashlib.sha256(
            json.dumps(reversed_params, sort_keys=True, default=str).encode()
        ).hexdigest()
        assert h1 == h2

    def test_unordered_set_is_sorted_before_hashing(self):
        """RR3_PATTERNS (frozenset) canonicalises to a sorted list."""
        probe = frozenset({"scalpb", "engulfing", "hammers"})
        canon = _canonicalise_material_value(probe)
        assert isinstance(canon, list)
        assert canon == sorted(canon)
        assert set(canon) == set(probe)

    def test_hash_seed_independent(self):
        """Same material config under PYTHONHASHSEED=0/1/42 → same hash."""
        script = (
            "import sys; sys.path.insert(0,'.');"
            "from core.research_events import compute_config_hash;"
            "print(compute_config_hash())"
        )
        hashes = set()
        for seed in ("0", "1", "42"):
            env = {"PYTHONHASHSEED": seed, "PATH": os.environ.get("PATH", "")}
            if "SYSTEMROOT" in os.environ:
                env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=180,
                env=env, cwd=".",
            )
            assert proc.returncode == 0, proc.stderr[-2000:]
            hashes.add(proc.stdout.strip())
        assert len(hashes) == 1, f"hash differs across PYTHONHASHSEED: {hashes}"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. HISTORICAL BASELINES READABLE + UNCHANGED; LEGACY HASH FAILS CLOSED
# ═══════════════════════════════════════════════════════════════════════════════

class TestHistoricalBaselineCompatibility:
    @pytest.fixture(autouse=True)
    def _baseline_isolation(self, tmp_path, monkeypatch):
        install_fake_s3()
        monkeypatch.chdir(tmp_path)
        import research_engine.v10.baselines.baseline_authority as auth

        monkeypatch.setattr(auth, "_BASELINES_DIR", str(tmp_path / "baselines"))
        monkeypatch.setattr(
            auth, "_ACTIVE_POINTER_FILE",
            str(tmp_path / "baselines" / "active_baseline.json"),
        )

    def test_legacy_persisted_baseline_readable_and_byte_unchanged(self):
        """An old-scheme record loads verbatim and is never rewritten."""
        from research_engine.v10.baselines.baseline_authority import (
            ensure_active_baseline,
            load_baseline_snapshot,
            validate_candidate_baseline,
        )
        from research_engine.v10.baselines.models import BaselineSnapshot
        from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
        import research_engine.v10.baselines.baseline_authority as auth

        ensure_active_baseline()  # unrelated live state; proves isolation
        legacy = BaselineSnapshot(
            snapshot_id="V10_BASELINE_LEGACYH13",
            config_hash="0123456789abcdef",  # pre-widening scheme marker
            identity_hash="legacyidentity01",
            configuration={"engine_mode": "LEGACY"},
        )
        reg = SnapshotRegistry(baselines_dir=auth._BASELINES_DIR)
        reg.save(legacy)
        # Promote the legacy snapshot to active so the staleness comparison
        # below exercises the config-hash branch (not the baseline-id branch).
        auth.set_active(
            "V10_BASELINE_LEGACYH13", actor="test", reason="H1.3 legacy fixture"
        )
        record_path = Path(auth._BASELINES_DIR) / "V10_BASELINE_LEGACYH13.json"
        raw_before = record_path.read_bytes()

        loaded = load_baseline_snapshot("V10_BASELINE_LEGACYH13")
        assert loaded is not None
        assert loaded.config_hash == "0123456789abcdef"
        assert loaded.identity_hash == "legacyidentity01"
        assert loaded.snapshot_id == "V10_BASELINE_LEGACYH13"

        # A staleness comparison must neither match silently nor rewrite history.
        ok, reason = validate_candidate_baseline(
            "V10_BASELINE_LEGACYH13", "0123456789abcdef"
        )
        assert ok is False
        assert reason and reason.startswith("stale_config")
        assert record_path.read_bytes() == raw_before

    def test_current_config_hash_does_not_claim_legacy_identity(self):
        """Newly computed hashes use corrected coverage; no silent equivalence."""
        assert compute_config_hash() not in ("", "UNKNOWN", "0123456789abcdef")


