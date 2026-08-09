"""
Research Control Plane.

An index and orchestration layer for the new-engine research system.
Does NOT contain research findings — it references independent question products.

Responsibilities:
    - Manage question lifecycle (DISCOVERED → CANDIDATE → ACTIVE → FINDING)
    - Index universe/population health
    - Track research run manifests
    - Enforce question development growth limits
    - Generate navigable status documents
"""
