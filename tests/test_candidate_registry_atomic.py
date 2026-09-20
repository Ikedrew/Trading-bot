"""HARDENING 1.4 - CandidateRegistry atomic persistence tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.v10.candidates import CandidateRecord, CandidateRegistry


def _make(cid: str, **kw) -> CandidateRecord:
    kw.setdefault("baseline_id", "BASE_001")
    kw.setdefault("component", "risk.stop_model")
    return CandidateRecord(candidate_id=cid, **kw)


def _read_canonical(tmp_path: Path) -> str:
    return (tmp_path / "candidates.jsonl").read_text(encoding="utf-8")


class TestAtomicRoundTrip:
    def test_single_round_trip(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("C1", description="hello"))
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        loaded = reg2.get("C1")
        assert loaded is not None
        assert loaded.description == "hello"

    def test_multiple_ordering_content(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        for cid in ("A", "B", "C"):
            reg.create(_make(cid, description=f"desc-{cid}"))
        raw = _read_canonical(tmp_path)
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        assert len(lines) == 3
        ids = [json.loads(ln)["candidate_id"] for ln in lines]
        assert ids == ["A", "B", "C"]
        expected = "\n".join(
            json.dumps(c.to_dict(), default=str) for c in reg.list_all()
        ) + "\n"
        assert raw == expected
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert [c.candidate_id for c in reg2.list_all()] == ["A", "B", "C"]
        assert [c.to_dict() for c in reg2.list_all()] == [
            c.to_dict() for c in reg.list_all()
        ]

    def test_reload_identical(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("X1"))
        reg.update_status("X1", "VALIDATING")
        reg.add_validation_result("X1", validation_id="VAL_1", decision="IMPROVED")
        before = [c.to_dict() for c in reg.list_all()]
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert [c.to_dict() for c in reg2.list_all()] == before

    def test_no_tmp_after_success(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("T1"))
        reg.create(_make("T2"))
        assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

class TestNoDirectTruncation:
    def test_replace_only_after_complete_payload(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("KEEP"))
        before = _read_canonical(tmp_path)
        real_replace = os.replace
        seen = {}

        def spy_replace(src, dst):
            seen["tmp_bytes"] = Path(src).read_bytes()
            assert Path(src).parent == tmp_path
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)
        reg.create(_make("NEW1"))
        after = _read_canonical(tmp_path)
        assert len(after.strip().splitlines()) == 2
        assert seen["tmp_bytes"] == after.encode("utf-8")
        assert before != after

    def test_flush_fsync_before_replace(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("F0"))
        calls: list = []
        real_replace = os.replace
        real_fsync = os.fsync

        def spy_fsync(fd):
            calls.append("fsync")
            return real_fsync(fd)

        def spy_replace(src, dst):
            calls.append("replace")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "fsync", spy_fsync)
        monkeypatch.setattr(os, "replace", spy_replace)
        reg.create(_make("F1"))
        assert calls.index("fsync") < calls.index("replace")

    def test_write_failure_keeps_canonical(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("ORIG"))
        before = _read_canonical(tmp_path)

        def boom_write(fd, data):
            raise OSError("simulated write failure")

        monkeypatch.setattr(os, "write", boom_write)
        with pytest.raises(OSError):
            reg.create(_make("SHOULD_NOT_LAND"))
        assert _read_canonical(tmp_path) == before
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert [c.candidate_id for c in reg2.list_all()] == ["ORIG"]
        assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


class TestFailureBehaviour:
    def test_fsync_failure_leaves_canonical_intact(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("S1"))
        before = _read_canonical(tmp_path)

        def boom_fsync(fd):
            raise OSError("simulated fsync failure")

        monkeypatch.setattr(os, "fsync", boom_fsync)
        with pytest.raises(OSError):
            reg.create(_make("S2"))
        assert _read_canonical(tmp_path) == before
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert [c.candidate_id for c in reg2.list_all()] == ["S1"]
        assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_replace_failure_leaves_canonical_intact(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("R1"))
        before = _read_canonical(tmp_path)

        def boom_replace(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", boom_replace)
        with pytest.raises(OSError):
            reg.create(_make("R2"))
        assert _read_canonical(tmp_path) == before
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        assert [c.candidate_id for c in reg2.list_all()] == ["R1"]
        assert (tmp_path / "candidates.jsonl").exists()
        assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []

    def test_exceptions_surface(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("E1"))
        monkeypatch.setattr(
            os, "replace",
            lambda s, d: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            reg.create(_make("E2"))

    def test_temp_cleanup_never_deletes_canonical(self, tmp_path, monkeypatch):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("K1"))
        before = _read_canonical(tmp_path)
        unlinked: list = []
        real_unlink = os.unlink

        def spy_unlink(p):
            unlinked.append(str(p))
            return real_unlink(p)

        def boom_replace(src, dst):
            raise OSError("replace down")

        monkeypatch.setattr(os, "unlink", spy_unlink)
        monkeypatch.setattr(os, "replace", boom_replace)
        with pytest.raises(OSError):
            reg.create(_make("K2"))
        assert _read_canonical(tmp_path) == before
        for u in unlinked:
            assert Path(u).name != "candidates.jsonl"


class TestSemanticsUnchanged:
    def test_identity_dedup_lifecycle(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make("DUP"))
        with pytest.raises(ValueError):
            reg.create(_make("DUP"))
        reg.update_status("DUP", "VALIDATING")
        assert reg.get("DUP").status == "VALIDATING"
        with pytest.raises(ValueError):
            reg.update_status("DUP", "ACCEPTED")
        reg.add_validation_result("DUP", validation_id="V", decision="IMPROVED")
        assert len(reg.get("DUP").validation_history) == 1
        reg2 = CandidateRegistry(storage_dir=str(tmp_path))
        c = reg2.get("DUP")
        assert c.status == "VALIDATING"
        assert len(c.validation_history) == 1
        assert c.baseline_id == "BASE_001"
