"""The read guard: a text tool must refuse non-text input at the boundary.

These tests exist because of a measured failure, not a hypothetical one. On a
real lab run the agent called read_file on two matplotlib PNGs; each returned
roughly 33,000 tokens of mojibake that then rode along in the context window for
every remaining turn. Those two reads were 25% of the run's entire input.

The lesson generalises past this project: any tool that returns text into a
context window needs a size ceiling and a type check, because the caller
deciding what to read is a language model and "don't do that" is a request
rather than a guarantee.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from labsagent.agent.confined import (
    BINARY_SUFFIXES,
    MAX_READ_BYTES,
    ConfinedBackend,
    looks_binary,
)


def make_png(path, width=8, height=8):
    """A real, valid PNG -- not just bytes with a .png name."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


@pytest.fixture
def backend(tmp_path):
    from deepagents.backends import FilesystemBackend

    return ConfinedBackend(
        FilesystemBackend(root_dir=str(tmp_path), virtual_mode=False), tmp_path
    )


class TestLooksBinary:
    def test_png_by_suffix(self, tmp_path):
        assert looks_binary(make_png(tmp_path / "plot.png"))

    def test_python_source_is_text(self, tmp_path):
        path = tmp_path / "task1.py"
        path.write_text("print('hello')\n", encoding="utf-8")
        assert not looks_binary(path)

    def test_nul_sniff_catches_an_unlabelled_binary(self, tmp_path):
        """Suffix lists are never complete, so content gets a vote too."""
        path = tmp_path / "model.weights"
        path.write_bytes(b"\x01\x02\x00\x03" * 100)
        assert looks_binary(path)

    def test_unicode_text_is_not_binary(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("héllo — ünicode ✓\n" * 50, encoding="utf-8")
        assert not looks_binary(path)

    def test_every_listed_suffix_is_lowercase_and_dotted(self):
        assert all(s.startswith(".") and s.islower() for s in BINARY_SUFFIXES)

    def test_suffix_check_is_case_insensitive(self, tmp_path):
        assert looks_binary(make_png(tmp_path / "PLOT.PNG"))


class TestReadGuard:
    def test_refuses_a_png_and_says_why(self, backend, tmp_path):
        make_png(tmp_path / "figure.png")
        result = backend.read("/figure.png")
        assert result.error is not None
        assert "binary" in result.error.lower()
        assert result.file_data is None

    def test_refusal_is_short(self, backend, tmp_path):
        """The whole point is not spending tokens -- a verbose refusal defeats it."""
        make_png(tmp_path / "figure.png", 64, 64)
        assert len(backend.read("/figure.png").error) < 400

    def test_refusal_reassures_so_the_agent_does_not_retry(self, backend, tmp_path):
        make_png(tmp_path / "figure.png")
        error = backend.read("/figure.png").error
        assert "written successfully" in error

    def test_text_still_reads_normally(self, backend, tmp_path):
        (tmp_path / "task1.py").write_text("print('ok')\n", encoding="utf-8")
        result = backend.read("/task1.py")
        assert result.error is None
        assert result.file_data is not None

    def test_oversized_text_is_truncated_not_refused(self, backend, tmp_path):
        """A huge log still has useful information at the top; a PNG does not."""
        (tmp_path / "big.txt").write_text("x" * (MAX_READ_BYTES + 5_000), encoding="utf-8")
        result = backend.read("/big.txt")
        assert result.error is not None
        assert "too large" in result.error.lower()
        assert "xxx" in result.error  # the head is included

    def test_a_file_exactly_at_the_limit_is_allowed(self, backend, tmp_path):
        (tmp_path / "edge.txt").write_text("y" * MAX_READ_BYTES, encoding="utf-8")
        assert backend.read("/edge.txt").error is None

    def test_the_security_boundary_still_wins(self, backend):
        """Escape is checked before type: an outside path is denied, not sniffed."""
        result = backend.read("C:/Windows/System32/drivers/etc/hosts")
        assert result.error is not None
        assert "denied" in result.error.lower()

    def test_missing_file_is_not_swallowed_by_the_guard(self, backend):
        result = backend.read("/nope.py")
        assert result.error is not None
        assert "binary" not in result.error.lower()
