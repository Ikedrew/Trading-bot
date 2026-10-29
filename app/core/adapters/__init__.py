"""
Adapter Layer — Compatibility bridges for non-canonical infrastructure.

Adapters are NOT part of the canonical data pipeline.
They exist solely to maintain backward compatibility with legacy
callers that still import disabled or side-effect-only modules.

ARCHITECTURE RULE:
    - Adapters MUST NOT perform network writes (no boto3, no requests)
    - Adapters MUST NOT write to S3 (only event_stream.py may)
    - Adapters MAY emit Discord messages (side-effect, non-blocking)
    - Adapters MAY return no-op values for interface compliance
    - Adapters MUST declare ADAPTER_MODE = True guard

CANONICAL PIPELINE:
    core/event_stream.py → local JSONL + S3 mirror (single truth)

ADAPTERS:
    core/adapters/s3_uploader.py    → No-op S3 sink (import compat for log_router)
    core/adapters/output_router.py  → Discord routing + no-op S3 (import compat for live_scanner)
"""
