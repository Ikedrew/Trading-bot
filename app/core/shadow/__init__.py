"""
NEW Shadow Runtime — per-opportunity horizon-shadow simulation.

First-class DATA-layer child lineage of canonical_opportunity_id.
See core/shadow/runtime.py and the approved Shadow Runtime contract.
There is NO shadow decision stage; live V10 facts are inherited observations.
"""

from core.shadow.models import (
    CONSTRUCTION_MODEL_VERSION,
    SCHEMA_VERSION,
    SIMULATION_MODEL_VERSION,
)

__all__ = [
    "SCHEMA_VERSION",
    "CONSTRUCTION_MODEL_VERSION",
    "SIMULATION_MODEL_VERSION",
]
