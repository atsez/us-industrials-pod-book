"""Yahoo data and private notes must never reach the public repo."""

from pathlib import Path

from podbook.config import Paths

ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_excludes_data_and_private_notes():
    ignored = {line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()}
    assert "data/" in ignored
    assert "INTERVIEW_DEFENCE.md" in ignored


def test_all_fetched_outputs_live_under_data():
    p = Paths(ROOT)
    for path in (p.raw_edgar, p.prices_dir, p.yahoo_consensus, p.interim, p.logs, p.reports):
        assert p.data in path.parents
    assert p.data not in p.coverage_csv.parents
