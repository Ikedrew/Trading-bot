"""
Lambda Anomaly Analysis — Configuration.

All settings are loaded from environment variables with sensible defaults.
"""

import os

# S3 Configuration
S3_BUCKET = os.environ.get("S3_BUCKET", "v10-engine")
DATASET_PATH = os.environ.get("DATASET_PATH", "research_ready_trade_dataset/research_ready_trades.jsonl")
REPORT_PATH = os.environ.get("REPORT_PATH", "reports/research/")

# Analysis thresholds (match core/research_anomaly.py)
EXTREME_R_HIGH = float(os.environ.get("EXTREME_R_HIGH", "5.0"))
EXTREME_R_LOW = float(os.environ.get("EXTREME_R_LOW", "-3.0"))
EXTREME_PNL_PERCENTILE = float(os.environ.get("EXTREME_PNL_PERCENTILE", "2.5"))
