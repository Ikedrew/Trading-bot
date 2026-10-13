"""Small archive integrity tests; no evaluator, scientific fixture or network."""
import hashlib
import pytest
from research_engine.v10.continuous.research_lab_archive import safe_path, verified_file, atomic


@pytest.mark.parametrize("relative", ["../outside", "C:\\outside", "folder/../outside",
                                     "folder/file. ", "CON", "nested/NUL.json",
                                     "file:stream", "/absolute"])
def test_archive_rejects_unsafe_paths(tmp_path, relative):
    with pytest.raises(ValueError):
        safe_path(tmp_path, relative)


def test_verified_file_rejects_corruption_and_missing_artifact(tmp_path):
    path = tmp_path / "detail.json"
    content = b'{"status":"BLOCKED"}'
    path.write_bytes(content)
    catalog = {"files": {"detail.json": {
        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}}}
    assert verified_file(tmp_path, catalog, "detail.json") == path
    path.write_bytes(content.replace(b"BLOCKED", b"CHANGED"))
    with pytest.raises(ValueError, match="ARCHIVE_ARTIFACT_CHANGED"):
        verified_file(tmp_path, catalog, "detail.json")
    path.unlink()
    with pytest.raises(OSError):
        verified_file(tmp_path, catalog, "detail.json")


def test_failed_atomic_replace_preserves_display_pointer(tmp_path, monkeypatch):
    import research_engine.v10.continuous.research_lab_archive as archive
    pointer = tmp_path / "index.json"
    pointer.write_bytes(b'{"latest":"previous"}')
    def fail_replace(*args):
        raise OSError("simulated interrupted promotion")
    monkeypatch.setattr(archive.os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic(pointer, {"latest": "new"})
    assert pointer.read_bytes() == b'{"latest":"previous"}'
    assert list(tmp_path.iterdir()) == [pointer]
