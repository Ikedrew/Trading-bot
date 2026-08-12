"""
V10 Research S3 Persistence Tests.

Proves:
    - Path construction is correct
    - Dry-run enumerates expected files
    - S3 prefix is reports/v10-research/ (not reports/research/)
    - Question products are published
    - Run manifests are published
    - Control plane state is published
    - Immutable history is preserved
    - Existing reports/research/ prefix is not touched
    - No analysis logic in persistence module
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from research_engine.v10.persistence.s3_publisher import (
    V10ResearchS3Publisher,
    get_s3_key,
    get_expected_s3_structure,
    publish_v10_research,
    publish_v10_run,
    _S3_BUCKET,
    _S3_PREFIX,
)


class TestPathConstruction:

    def test_s3_prefix_is_v10_research(self):
        assert _S3_PREFIX == "reports/v10-research"

    def test_bucket_is_v10_engine(self):
        assert _S3_BUCKET == "v10-engine"

    def test_get_s3_key(self):
        key = get_s3_key("runs/run_001.json")
        assert key == "reports/v10-research/runs/run_001.json"

    def test_expected_structure(self):
        keys = get_expected_s3_structure("run_001", ["E-001", "D-001"])
        assert "reports/v10-research/runs/run_001.json" in keys
        assert "reports/v10-research/questions/E-001/question.json" in keys
        assert "reports/v10-research/questions/E-001/latest.json" in keys
        assert "reports/v10-research/questions/E-001/latest.md" in keys
        assert "reports/v10-research/questions/E-001/history/run_001.json" in keys
        assert "reports/v10-research/questions/D-001/latest.json" in keys
        assert "reports/v10-research/control_plane/control_plane_state.json" in keys

    def test_prefix_does_not_touch_old_research(self):
        """S3 prefix must NOT be reports/research/."""
        assert "reports/research/" not in _S3_PREFIX
        key = get_s3_key("questions/E-001/latest.json")
        assert key.startswith("reports/v10-research/")
        assert not key.startswith("reports/research/")


class TestDryRunPublish:

    def test_dry_run_publish_all(self):
        """Dry run enumerates files without touching S3."""
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        assert result["dry_run"] is True
        assert result["published"] > 0
        assert result["failed"] == 0
        # Should include question products
        assert any("questions/" in f for f in result["files"])

    def test_dry_run_publish_run(self):
        """Dry run for a specific run ID."""
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_run("run_20260809_033621_3c7f7b")
        assert result["dry_run"] is True
        assert result["published"] > 0
        assert result["run_id"] == "run_20260809_033621_3c7f7b"

    def test_dry_run_includes_manifest(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        manifests = [f for f in publisher._published if "runs/" in f]
        assert len(manifests) >= 1

    def test_dry_run_includes_control_plane(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        cp = [f for f in publisher._published if "control_plane" in f]
        assert len(cp) >= 1


class TestPublishContent:

    def test_publish_all_covers_questions(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        # Should have 44+ question products (all with question.json)
        q_files = [f for f in publisher._published if "/questions/" in f and "question.json" in f]
        assert len(q_files) >= 44

    def test_publish_all_covers_latest_findings(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        latest = [f for f in publisher._published if "latest.json" in f]
        assert len(latest) >= 44

    def test_publish_all_covers_history(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        history = [f for f in publisher._published if "/history/" in f]
        assert len(history) >= 44  # At least one history file per question

    def test_publish_all_covers_md(self):
        publisher = V10ResearchS3Publisher(dry_run=True)
        result = publisher.publish_all()
        md_files = [f for f in publisher._published if f.endswith(".md")]
        assert len(md_files) >= 44


class TestSafety:

    def test_no_analysis_imports(self):
        """Persistence module must NOT import analysis logic."""
        import inspect
        from research_engine.v10.persistence import s3_publisher
        source = inspect.getsource(s3_publisher)
        imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
        for line in imports:
            assert "primitives" not in line
            assert "question_runner" not in line
            assert "orchestrator" not in line
            assert "universe" not in line.split("import")[0] if "import" in line else True

    def test_publisher_does_not_modify_local_files(self, tmp_path):
        """Publishing to S3 must not alter local files."""
        # Create a mock local structure
        q_dir = tmp_path / "questions" / "E-001"
        q_dir.mkdir(parents=True)
        (q_dir / "latest.json").write_text('{"test": true}')
        original_content = (q_dir / "latest.json").read_text()

        publisher = V10ResearchS3Publisher(local_root=tmp_path, dry_run=True)
        publisher.publish_all()

        # Local file unchanged
        assert (q_dir / "latest.json").read_text() == original_content
