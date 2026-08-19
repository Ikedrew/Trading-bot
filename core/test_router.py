"""Manual Discord router test. Run directly to verify event emission."""

from core.log_router import StructuredLogger

logger = StructuredLogger()
logger.event("SYSTEM_STARTUP", {"status": "test"})
