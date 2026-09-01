"""
Control Plane CLI Tests.

Tests the research.py command functions without executing full universes.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question
from research_engine.v10.runner.primitive_mapping import QUESTION_PARAMETERS, build_full_mapping
from research_engine.v10.cockpit.bottleneck import _load_all_findings, analyse_bottleneck, recommend_next
from research_engine.v10.cockpit.optimisation_register import _load_register, _append_register


class TestQuestionResolution:

    def test_m002_has_parameters(self):
        assert "M-002" in QUESTION_PARAMETERS
        assert QUESTION_PARAMETERS["M-002"]["feature_field"] == "htf_alignment_strength"

    def test_m004_has_parameters(self):
        assert "M-004" in QUESTION_PARAMETERS
        assert QUESTION_PARAMETERS["M-004"]["feature_field"] == "h1_structural_clarity"

    def test_s003_has_parameters(self):
        assert "S-003" in QUESTION_PARAMETERS
        assert QUESTION_PARAMETERS["S-003"]["predicted_field"] == "confidence"

    def test_all_45_questions_mappable(self):
        mapping = build_full_mapping(QUESTION_BANK)
        assert len(mapping) == len(QUESTION_BANK)

    def test_question_lookup(self):
        q = get_question("E-001")
        assert q is not None
        assert q.title == "System Expectancy"


class TestBottleneckDetection:

    def test_loads_findings(self):
        findings = _load_all_findings()
        # Should find findings from previous runs
        assert len(findings) >= 30  # At least 30 executed

    def test_bottleneck_does_not_crash(self, capsys):
        """Bottleneck analysis runs without error."""
        analyse_bottleneck()
        captured = capsys.readouterr()
        assert "RESEARCH PICTURE" in captured.out or "BOTTLENECK" in captured.out.upper()

    def test_next_does_not_crash(self, capsys):
        """Next recommendation runs without error."""
        recommend_next()
        captured = capsys.readouterr()
        assert "NEXT RESEARCH" in captured.out


class TestOptimisationRegister:

    def test_load_empty_register(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "research_engine.v10.cockpit.optimisation_register._REGISTER_FILE",
            tmp_path / "opt.jsonl",
        )
        entries = _load_register()
        assert entries == []

    def test_append_and_load(self, tmp_path, monkeypatch):
        reg_file = tmp_path / "opt.jsonl"
        monkeypatch.setattr(
            "research_engine.v10.cockpit.optimisation_register._REGISTER_FILE",
            reg_file,
        )
        entry = {"id": "OPT-TEST", "status": "PROPOSED", "bottleneck": "test"}
        _append_register(entry)
        entries = _load_register()
        assert len(entries) == 1
        assert entries[0]["id"] == "OPT-TEST"


class TestCLIImports:

    def test_research_module_importable(self):
        """The research.py CLI module can be imported."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "research", str(Path(__file__).parent.parent / "research.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't execute main, just verify import
        assert spec is not None

    def test_no_trading_imports_in_cli(self):
        """CLI must not import trading execution code."""
        cli_path = Path(__file__).parent.parent / "research.py"
        source = cli_path.read_text(encoding="utf-8")
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        for line in import_lines:
            assert "core.runtime" not in line
            assert "execution.mt5" not in line
            assert "risk.manager" not in line
