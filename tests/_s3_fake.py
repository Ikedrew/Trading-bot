"""
Shared fake-S3 test helper for Research Engine tests.

Mirrors the sanctioned S3-injection pattern used by
tests/test_research_engine_s3_source.py and tests/test_research_loaders.py:
an in-memory boto3-compatible client (list_objects_v2 + get_object) injected via
set_default_source, so tests never touch the network or local logs/ for source
data.

Key layout (matches core.production_data_contract):
    <base>/schema_version=<schema>/symbol=<SYMBOL>/date=<DATE>/part-000.jsonl

Date-only datasets (portfolio_rankings/portfolio_shadow) omit the symbol=
segment. Derived research artifacts live under research_artifacts/<name>/.
"""

from __future__ import annotations

import json

from core.production_data_contract import s3_base_prefix, current_schema
from research_engine.data_access.s3_source import (
    S3ResearchDataSource,
    set_default_source,
    reset_default_source,
)

_DATE_ONLY = {"portfolio_rankings", "portfolio_shadow"}


class FakeS3:
    """In-memory S3 with dataset-aware seeding helpers."""

    def __init__(self):
        self.objects: dict[str, str] = {}

    # ── seeding ────────────────────────────────────────────────────────────
    def add(
        self,
        dataset: str,
        records: list[dict],
        *,
        symbol: str | None = None,
        date: str = "2026-07-23",
    ) -> None:
        base = s3_base_prefix(dataset)
        schema = current_schema(dataset)
        if dataset in _DATE_ONLY or symbol is None:
            key = f"{base}/schema_version={schema}/date={date}/part-000.jsonl"
        else:
            key = f"{base}/schema_version={schema}/symbol={symbol}/date={date}/part-000.jsonl"
        self.objects[key] = self.objects.get(key, "") + "".join(
            json.dumps(r) + "\n" for r in records
        )

    def add_artifact(self, name: str, records: list[dict], *, part: str = "part-000.jsonl") -> None:
        """Seed a derived research artifact (e.g. 'research_universe')."""
        key = f"research_artifacts/{name}/{part}"
        self.objects[key] = self.objects.get(key, "") + "".join(
            json.dumps(r) + "\n" for r in records
        )

    def add_artifact_raw(self, name: str, body: str, *, part: str = "part-000.jsonl") -> None:
        key = f"research_artifacts/{name}/{part}"
        self.objects[key] = body

    # ── boto3-compatible surface ─────────────────────────────────────────────
    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, **kw):
        key = kw["Key"]

        class _Body:
            def __init__(self, t):
                self._t = t

            def read(self):
                return self._t.encode("utf-8")

        return {"Body": _Body(self.objects[key])}


def install_fake_s3(fake: FakeS3 | None = None) -> FakeS3:
    """Install a FakeS3 as the default research data source. Returns the fake."""
    fake = fake or FakeS3()
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=fake))
    return fake


def reset_fake_s3() -> None:
    reset_default_source()
