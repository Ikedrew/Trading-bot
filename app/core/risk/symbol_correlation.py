"""Governed symbol-correlation model authority (Block 2C).

This module is the ONE explicit correlation authority for prop-risk telemetry.
It exists because the repository previously had NO trustworthy correlation
model for this purpose:

  * ``core/correlation.py`` is a DECISION-CYCLE correlation ID generator
    (``COR-<date>-<cycle>-<symbol>-<hash>``). Despite the name it has nothing to
    do with market or symbol correlation.
  * ``core.config.CORRELATION_GROUPS`` is an unversioned hardcoded list of
    symbols consumed by a runtime entry guard in LOTS.
  * ``core.portfolio_ranking.context`` keeps its own duplicated
    ``_DEFAULT_GROUPS`` and applies an ad-hoc rank penalty.

None of those are account-scoped, versioned, provenance-carrying or validated.
They are therefore NOT reused as a correlation authority here.

WHAT THIS MODEL IS
------------------
A **STATIC, GOVERNED, EXPLICIT** relationship map. It is NOT empirical
correlation, NOT a rolling market-data estimate, and NOT live. This repository
does not compute a reproducible, durable, account-independent empirical
correlation matrix today, so claiming one would be false precision. The model
declares ``is_empirical = False`` and persists that fact.

WHAT A CLUSTER MEANS
--------------------
Membership identifies CONCENTRATION, not hedging credit. Two symbols in one
cluster are one directional theme. Nothing in this module reduces, nets or
discounts risk.

CANONICAL SYMBOLS ONLY
----------------------
Correlation operates on canonical (broker-agnostic) symbols. Broker aliases
(``NAS100`` / ``USTEC`` / ``USTECH100M``) MUST be resolved upstream by the
canonical symbol resolver before they reach this module.

FAIL CLOSED
-----------
A model with duplicate cluster ids, a symbol assigned inconsistently where
exclusivity is required, an empty/malformed cluster, a non-canonical symbol, or
a contradictory relation is INVALID and raises :class:`CorrelationModelInvalid`.
The caller never silently picks one interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence


# -- MODEL IDENTITY ------------------------------------------------------------

DEFAULT_MODEL_ID = "SYMBOL_CORRELATION_CLUSTERS"
DEFAULT_MODEL_VERSION = "v1"

#: Deterministic cluster id used for an open canonical symbol the model does not
#: describe. Policy: a symbol absent from the governed map gets its OWN
#: singleton cluster with NO assumed relationships. It is never dropped and is
#: never merged into a nearby cluster.
UNCLASSIFIED_POLICY = "SINGLETON_UNCLASSIFIED"
UNCLASSIFIED_PREFIX = "UNCLASSIFIED::"

#: A canonical symbol is an uppercase alphanumeric broker-agnostic name.
CANONICAL_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,32}$")
#: A cluster id is an uppercase token. Whitespace and separators are rejected so
#: a malformed id can never masquerade as a different cluster.
CLUSTER_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class CorrelationModelError(RuntimeError):
    """Base error for the governed symbol-correlation model."""


class CorrelationModelInvalid(CorrelationModelError):
    """The correlation model is structurally invalid. Consumers fail closed."""


def _canonical_symbol(value: Any) -> str:
    if not isinstance(value, str):
        raise CorrelationModelInvalid(f"CORRELATION_SYMBOL_NOT_STRING:{value!r}")
    symbol = value.strip()
    if not symbol:
        raise CorrelationModelInvalid("CORRELATION_SYMBOL_EMPTY")
    if not CANONICAL_SYMBOL_PATTERN.match(symbol):
        raise CorrelationModelInvalid(f"NON_CANONICAL_CORRELATION_SYMBOL:{symbol}")
    return symbol


@dataclass(frozen=True)
class CorrelationCluster:
    """One governed correlation cluster over canonical symbols.

    ``member_symbols`` is stored sorted and de-duplicated so cluster membership
    is a pure function of the declared relationship set. Membership is
    EXCLUSIVE: a canonical symbol belongs to at most one cluster.
    """

    cluster_id: str
    member_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.cluster_id, str):
            raise CorrelationModelInvalid("CORRELATION_CLUSTER_ID_NOT_STRING")
        cluster_id = self.cluster_id.strip()
        if not cluster_id:
            raise CorrelationModelInvalid("CORRELATION_CLUSTER_ID_EMPTY")
        if not CLUSTER_ID_PATTERN.match(cluster_id):
            raise CorrelationModelInvalid(
                f"MALFORMED_CORRELATION_CLUSTER_ID:{cluster_id}")
        if not isinstance(self.member_symbols, (tuple, list)):
            raise CorrelationModelInvalid(
                f"CORRELATION_CLUSTER_MEMBERS_NOT_SEQUENCE:{cluster_id}")
        members = tuple(_canonical_symbol(s) for s in self.member_symbols)
        if not members:
            raise CorrelationModelInvalid(f"EMPTY_CORRELATION_CLUSTER:{cluster_id}")
        if len(set(members)) != len(members):
            duplicates = sorted({s for s in members if members.count(s) > 1})
            raise CorrelationModelInvalid(
                f"DUPLICATE_SYMBOL_IN_CLUSTER:{cluster_id}:{','.join(duplicates)}")
        object.__setattr__(self, "cluster_id", cluster_id)
        object.__setattr__(self, "member_symbols", tuple(sorted(members)))

    def contains(self, canonical_symbol: str) -> bool:
        return canonical_symbol in self.member_symbols

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "member_symbols": list(self.member_symbols),
            "member_count": len(self.member_symbols),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CorrelationCluster":
        return cls(
            cluster_id=str(payload["cluster_id"]),
            member_symbols=tuple(payload.get("member_symbols") or ()),
        )


def unclassified_cluster_id(canonical_symbol: str) -> str:
    """Deterministic singleton cluster id for a symbol outside the model."""
    return f"{UNCLASSIFIED_PREFIX}{_canonical_symbol(canonical_symbol)}"
@dataclass(frozen=True)
class SymbolCorrelationModel:
    """The governed, versioned, canonical-symbol correlation authority.

    A cluster is a CONCENTRATION bucket. ``is_empirical`` is permanently False:
    the relationships are static and declared, not estimated from market data.
    """

    model_id: str
    model_version: str
    effective_from_utc: str
    clusters: tuple[CorrelationCluster, ...]
    provenance: str
    semantics: str = "CONCENTRATION_NOT_HEDGING_CREDIT"
    is_empirical: bool = False
    unclassified_policy: str = UNCLASSIFIED_POLICY

    def __post_init__(self) -> None:
        for name in ("model_id", "model_version", "effective_from_utc", "provenance"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CorrelationModelInvalid(f"CORRELATION_MODEL_FIELD_REQUIRED:{name}")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.clusters, (tuple, list)):
            raise CorrelationModelInvalid("CORRELATION_CLUSTERS_NOT_SEQUENCE")
        clusters = tuple(
            c if isinstance(c, CorrelationCluster) else CorrelationCluster.from_dict(c)
            for c in self.clusters
        )
        object.__setattr__(self, "clusters", clusters)
        if self.is_empirical is not False:
            raise CorrelationModelInvalid(
                "EMPIRICAL_CORRELATION_NOT_SUPPORTED: static model by contract")
        if self.unclassified_policy != UNCLASSIFIED_POLICY:
            raise CorrelationModelInvalid(
                f"UNSUPPORTED_UNCLASSIFIED_POLICY:{self.unclassified_policy}")

        # -- STRUCTURAL VALIDATION (fail closed, never pick one reading) ----
        seen_ids: set[str] = set()
        owner: dict[str, str] = {}
        for cluster in clusters:
            if cluster.cluster_id in seen_ids:
                raise CorrelationModelInvalid(
                    f"DUPLICATE_CORRELATION_CLUSTER_ID:{cluster.cluster_id}")
            seen_ids.add(cluster.cluster_id)
            for symbol in cluster.member_symbols:
                prior = owner.get(symbol)
                if prior is not None and prior != cluster.cluster_id:
                    raise CorrelationModelInvalid(
                        "INCONSISTENT_SYMBOL_ASSIGNMENT:"
                        f"{symbol}:{prior}|{cluster.cluster_id}")
                owner[symbol] = cluster.cluster_id
        object.__setattr__(self, "_index", dict(owner))
# -- identity ----------------------------------------------------------
    @property
    def model_key(self) -> str:
        """Exact ``model_id@model_version`` string persisted on every record."""
        return f"{self.model_id}@{self.model_version}"

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)

    @property
    def symbol_universe(self) -> tuple[str, ...]:
        return tuple(sorted(self._index))

    def cluster(self, cluster_id: str) -> CorrelationCluster:
        for candidate in self.clusters:
            if candidate.cluster_id == cluster_id:
                return candidate
        raise CorrelationModelInvalid(f"UNKNOWN_CORRELATION_CLUSTER:{cluster_id}")

    def cluster_for_symbol(self, canonical_symbol: str) -> str:
        """Governed cluster id, or a deterministic UNCLASSIFIED singleton id."""
        symbol = _canonical_symbol(canonical_symbol)
        return self._index.get(symbol) or unclassified_cluster_id(symbol)

    def is_classified(self, canonical_symbol: str) -> bool:
        return _canonical_symbol(canonical_symbol) in self._index

    def classify(self, canonical_symbols: Iterable[str]) -> dict[str, str]:
        """Deterministic symbol -> cluster id mapping for one observation."""
        return {s: self.cluster_for_symbol(s) for s in canonical_symbols}

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_key": self.model_key,
            "effective_from_utc": self.effective_from_utc,
            "provenance": self.provenance,
            "semantics": self.semantics,
            "is_empirical": self.is_empirical,
            "unclassified_policy": self.unclassified_policy,
            "cluster_count": self.cluster_count,
            "symbol_universe": list(self.symbol_universe),
            "clusters": [c.to_dict() for c in self.clusters],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SymbolCorrelationModel":
        return cls(
            model_id=str(payload["model_id"]),
            model_version=str(payload["model_version"]),
            effective_from_utc=str(payload["effective_from_utc"]),
            clusters=tuple(
                CorrelationCluster.from_dict(c) for c in (payload.get("clusters") or ())
            ),
            provenance=str(payload.get("provenance") or ""),
semantics=str(payload.get("semantics")
                          or "CONCENTRATION_NOT_HEDGING_CREDIT"),
            is_empirical=bool(payload.get("is_empirical", False)),
            unclassified_policy=str(payload.get("unclassified_policy")
                                    or UNCLASSIFIED_POLICY),
        )


def build_symbol_correlation_model(
    clusters: Sequence[Mapping[str, Any] | CorrelationCluster],
    *,
    model_id: str = DEFAULT_MODEL_ID,
    model_version: str = DEFAULT_MODEL_VERSION,
    effective_from_utc: str,
    provenance: str,
) -> SymbolCorrelationModel:
    """Construct and fully validate a governed correlation model."""
    return SymbolCorrelationModel(
        model_id=model_id,
        model_version=model_version,
        effective_from_utc=effective_from_utc,
        clusters=tuple(clusters),
        provenance=provenance,
    )


def model_from_groups(
    groups: Sequence[Sequence[str]],
    *,
    cluster_ids: Sequence[str] | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    model_version: str = DEFAULT_MODEL_VERSION,
    effective_from_utc: str,
    provenance: str,
) -> SymbolCorrelationModel:
    """Promote a legacy group list into an explicit, versioned governed model.

    This is the ONLY sanctioned bridge from the old ad-hoc
    ``config.CORRELATION_GROUPS`` shape. It does not change how the runtime
    entry guard reads that config; it lifts the relationships into a validated,
    provenance-carrying model.
    """
    materialised: list[CorrelationCluster] = []
    for index, group in enumerate(groups):
        members = tuple(group)
        if not members:
            raise CorrelationModelInvalid(f"EMPTY_CORRELATION_GROUP_INDEX:{index}")
        if cluster_ids is not None:
            if len(cluster_ids) != len(groups):
                raise CorrelationModelInvalid(
                    "CORRELATION_CLUSTER_ID_COUNT_MISMATCH:"
                    f"{len(cluster_ids)}!={len(groups)}")
            cluster_id = str(cluster_ids[index])
        else:
            cluster_id = f"GROUP_{index + 1:02d}"
        materialised.append(
            CorrelationCluster(cluster_id=cluster_id, member_symbols=members))
    return SymbolCorrelationModel(
        model_id=model_id, model_version=model_version,
        effective_from_utc=effective_from_utc,
        clusters=tuple(materialised), provenance=provenance,
    )


def default_symbol_correlation_model() -> SymbolCorrelationModel:
    """The project's current governed correlation model.

    Built from ``core.config.CORRELATION_GROUPS`` -- the repository's ACTUAL
    static relationship source of truth -- but lifted into an explicit,
    versioned, provenance-carrying, validated model. STATIC, not empirical.
    """
    try:
        from core import config as _config
        groups = [list(group) for group in getattr(_config, "CORRELATION_GROUPS", ())]
    except Exception:  # config import must never break telemetry import
        groups = []
    return model_from_groups(
        groups,
        model_id=DEFAULT_MODEL_ID,
        model_version=DEFAULT_MODEL_VERSION,
        effective_from_utc="BLOCK_2C_STATIC_BASELINE",
        provenance="core.config.CORRELATION_GROUPS (static, governed lift)",
    )


__all__ = [
    "CANONICAL_SYMBOL_PATTERN",
    "CLUSTER_ID_PATTERN",
    "CorrelationCluster",
    "CorrelationModelError",
    "CorrelationModelInvalid",
    "DEFAULT_MODEL_ID",
    "DEFAULT_MODEL_VERSION",
    "SymbolCorrelationModel",
    "UNCLASSIFIED_POLICY",
    "UNCLASSIFIED_PREFIX",
    "build_symbol_correlation_model",
    "default_symbol_correlation_model",
    "model_from_groups",
    "unclassified_cluster_id",
]