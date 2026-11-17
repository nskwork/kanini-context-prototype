"""Two-reviewer approval (kanini_context/curation/approve.py): one name records the prototype exception; two different
people (together, or one after the other on an unchanged file) need none; a repeated name is refused; a change to the
file after approval voids it, in the approval script and in the loader."""
from __future__ import annotations

import copy
import json

import pytest

from conftest import ROOT
from kanini_context.curation import approve as A
from kanini_context.curation.load import LoadError, review_of

RULE = json.loads(next((ROOT / "kanini_context/rules").glob("*.json")).read_text(encoding="utf-8"))


def fresh() -> dict:
    d = copy.deepcopy(RULE)
    d["review"] = {k: v for k, v in d["review"].items() if k not in ("reviewed_by", "reviewed_at", "review_exception",
                                                                     "reviewed_sha", "approvals")}
    return d


def test_one_name_records_the_exception():
    d = fresh()
    assert "one approver" in A.record(d, ["NANDHU S K"])
    assert d["review"]["reviewed_by"] == ["NANDHU S K"] and d["review"]["review_exception"] == A.SCOPE_EXCEPTION
    assert review_of(d, False)[1] == A.SCOPE_EXCEPTION


def test_two_people_together_need_no_exception():
    d = fresh()
    assert "two people" in A.record(d, ["Reviewer One", "Reviewer Two"])
    assert d["review"]["reviewed_by"] == ["Reviewer One", "Reviewer Two"] and "review_exception" not in d["review"]
    assert review_of(d, False) == (["Reviewer One", "Reviewer Two"], None, d["review"]["reviewed_at"])


def test_second_approval_later_is_added_and_clears_the_exception():
    d = fresh()
    A.record(d, ["Reviewer One"])
    A.record(d, ["Reviewer Two"])
    assert d["review"]["reviewed_by"] == ["Reviewer One", "Reviewer Two"] and "review_exception" not in d["review"]
    assert [a["by"] for a in d["review"]["approvals"]] == ["Reviewer One", "Reviewer Two"]


@pytest.mark.parametrize("first,second", [(["Same Person", "same person"], None), (["Reviewer One"], ["Reviewer One"])])
def test_a_repeated_name_is_refused(first, second):
    d = fresh()
    if second is None:
        with pytest.raises(A.ReviewError):
            A.record(d, first)
        return
    A.record(d, first)
    with pytest.raises(A.ReviewError):
        A.record(d, second)
    assert d["review"]["reviewed_by"] == first


def test_a_change_after_approval_voids_it():
    d = fresh()
    A.record(d, ["Reviewer One"])
    d["schema_version"] = str(d.get("schema_version", "")) + "x"   # any edit to the file outside its review block
    with pytest.raises(LoadError):
        review_of(d, False)
    note = A.record(d, ["Reviewer Two"])
    assert "no longer counts" in note and d["review"]["reviewed_by"] == ["Reviewer Two"]
    assert d["review"]["review_exception"] == A.SCOPE_EXCEPTION


def test_cli_refuses_a_repeated_name(tmp_path, capsys):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(fresh()), encoding="utf-8")
    A.main(["--by", "Reviewer One", "--by", "Reviewer One", "--yes", str(p)])
    assert "two different people" in capsys.readouterr().out
    assert "reviewed_by" not in json.loads(p.read_text(encoding="utf-8"))["review"]
