"""Recognising read files (FASTQ, FASTA, read/count tables), plain or compressed with gzip/bz2."""
from __future__ import annotations

import bz2
import gzip
import io
import re
from pathlib import Path

SUFFIXES = (".fastq", ".fq", ".fasta", ".fa", ".fna", ".txt", ".tsv", ".csv")
COMPRESSED = (".gz", ".bz2")
SAMPLE_RECORDS = 50
SEQ_RE = re.compile(r"^[ACGTUN.]+$", re.IGNORECASE)
COUNT_RE = re.compile(r"^[ACGTUN]+[\t,;]\s*\d+(\.\d+)?$", re.IGNORECASE)


class NotReads(ValueError):
    """The file is not a read file; the message is meant for the user."""


def allowed_name(name: str) -> bool:
    low = name.lower()
    for c in COMPRESSED:
        if low.endswith(c):
            low = low[: -len(c)]
    return low.endswith(SUFFIXES)


def compression(path: Path) -> str:
    with open(path, "rb") as fh:
        magic = fh.read(3)
    if magic[:2] == b"\x1f\x8b":
        return "gz"
    if magic == b"BZh":
        return "bz2"
    return ""


def open_text(path: Path):
    comp = compression(path)
    if comp == "gz":
        raw = gzip.open(path, "rb")
    elif comp == "bz2":
        raw = bz2.open(path, "rb")
    else:
        raw = open(path, "rb")
    return io.TextIOWrapper(raw, encoding="utf-8", errors="strict")


def _head(path: Path, n: int) -> list[str]:
    lines = []
    try:
        with open_text(path) as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if line.strip():
                    lines.append(line)
                if len(lines) >= n:
                    break
    except UnicodeDecodeError:
        raise NotReads("This is not a text file (or a gzip/bz2 compressed text file).")
    except (OSError, EOFError) as exc:
        raise NotReads(f"The file cannot be read: {exc}")
    return lines


def sniff(path: Path) -> str:
    """'fastq', 'fasta' or 'counts', with '.gz'/'.bz2' appended when compressed; raises NotReads."""
    lines = _head(path, 4 * SAMPLE_RECORDS)
    if not lines:
        raise NotReads("The file is empty.")
    first = lines[0]
    if first.lstrip().lower().startswith(("<!doctype", "<html")):
        raise NotReads("This is a web page, not a sequence file. If it came from a link, check that the file is "
                       "shared publicly and that the link points to the file itself.")
    comp = compression(path)
    suffix = f".{comp}" if comp else ""
    if first.startswith("@"):
        full = lines[: len(lines) - len(lines) % 4] or lines
        for i in range(0, len(full) - 3, 4):
            head, seq, plus, qual = full[i:i + 4]
            if not head.startswith("@") or not plus.startswith("+") or not SEQ_RE.match(seq) or len(qual) != len(seq):
                raise NotReads(f"This looks like FASTQ, but record {i // 4 + 1} is malformed.")
        return "fastq" + suffix
    if first.startswith(">"):
        for line in lines:
            if not line.startswith(">") and not SEQ_RE.match(line.strip()):
                raise NotReads("This looks like FASTA, but it contains a line that is not a nucleotide sequence.")
        return "fasta" + suffix
    if all(COUNT_RE.match(line.strip()) for line in lines):
        return "counts" + suffix
    raise NotReads("The file is not FASTQ, FASTA or a read/count table (sequence and count per line).")
