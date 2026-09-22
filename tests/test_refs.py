"""Splitting an answer into the references it names.

The case that matters is the one that failed in production: the data question
is offered with its value joined by ", ", and the answer

    Online Retail.xlsx, https://archive.ics.uci.edu/dataset/352/online+retail

used to come back as three references -- 'Online', 'Retail.xlsx' and the URL.
"""

from __future__ import annotations

import pytest

from labsagent.data.refs import candidate_tokens, split_refs


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The production failure, verbatim.
        (
            "Online Retail.xlsx, https://archive.ics.uci.edu/dataset/352/online+retail",
            ["Online Retail.xlsx", "https://archive.ics.uci.edu/dataset/352/online+retail"],
        ),
        # No comma means the spaces ARE the separators.
        ("a.csv b.csv", ["a.csv", "b.csv"]),
        # Newlines always separate, whatever else is going on.
        ("a.csv\nb.csv", ["a.csv", "b.csv"]),
        ("Online Retail.xlsx\nuciml/iris", ["Online Retail.xlsx", "uciml/iris"]),
        # Quotes protect a name and are removed.
        ('"my data.csv", uciml/iris', ["my data.csv", "uciml/iris"]),
        # A trailing separator is not an empty reference.
        ("a.csv,", ["a.csv"]),
        ("a.csv, , b.csv", ["a.csv", "b.csv"]),
        # Nothing in, nothing out.
        ("", []),
        ("   ", []),
        # The same file named twice is one file.
        ("a.csv, a.csv", ["a.csv"]),
    ],
)
def test_split_refs(raw, expected):
    assert split_refs(raw) == expected


def test_a_windows_path_keeps_its_backslashes():
    """shlex would eat these, which is why the scanner is hand-rolled."""
    assert split_refs(r"C:\Users\HP\Online Retail.xlsx, uciml/iris") == [
        r"C:\Users\HP\Online Retail.xlsx",
        "uciml/iris",
    ]


def test_a_relative_path_keeps_its_leading_dot():
    assert split_refs("./data/iris.csv") == ["./data/iris.csv"]


def test_trailing_sentence_punctuation_is_stripped():
    assert split_refs("https://example.com/a.csv.") == ["https://example.com/a.csv"]


def test_candidate_tokens_offers_the_whole_segment_before_its_words():
    """Prose is split wider, but a spaced filename must still be offered whole."""
    found = candidate_tokens("Online Retail.xlsx, use pandas")
    assert found.index("Online Retail.xlsx") < found.index("Online")
    assert "use pandas" in found


def test_candidate_tokens_on_ordinary_prose_offers_only_rejectable_things():
    """`classify` is the filter; these are what it must be given the chance to reject."""
    assert "numpy" in candidate_tokens("use numpy, not plain Python")
