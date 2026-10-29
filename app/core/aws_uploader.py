"""
AWS S3 Event Uploader — Re-export from adapter layer.

# ═══════════════════════════════════════════════════════════════════
# ARCHITECTURE RULE:
# Only core/event_stream.py is allowed to write to S3.
# All other S3 writers are forbidden.
# ═══════════════════════════════════════════════════════════════════
#
# This file re-exports from core/adapters/s3_uploader.py
# to maintain import compatibility with core/log_router.py.
# ═══════════════════════════════════════════════════════════════════
"""

from core.adapters.s3_uploader import upload_event, _get_client  # noqa: F401
