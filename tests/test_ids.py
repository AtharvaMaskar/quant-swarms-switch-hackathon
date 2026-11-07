import pytest

from qs import db, ids


def test_ids(tmp_path):
    assert ids.family("h12.1v3") == "h12"
    assert ids.family("h12") == "h12"
    assert ids.kind("h12") == "root" and ids.kind("h12v2") == "patch"
    assert ids.kind("h12.1") == "fork" and ids.kind("h12.1v2") == "patch"
    assert ids.parent("h12") is None
    assert ids.parent("h12v2") == "h12" and ids.parent("h12v3") == "h12v2"
    assert ids.parent("h12.1") == "h12" and ids.parent("h12.1.2") == "h12.1"
    assert ids.parent("h12.1v2") == "h12.1"
    assert ids.depth("h12.1.2") == 2 and ids.depth("h12v4") == 0
    assert ids.room_name("h12.1", "expiry mom volgate") == "h12-1-expiry-mom-volgate"
    assert ids.room_name("h12", "Expiry Mom Vol Gate") == "h12-expiry-mom-vol"

    con = db.connect(tmp_path / "r.db")
    assert ids.next_root(con) == "h1"
    db.add_hypothesis(con, "h12", "expiry mom", {})
    assert ids.next_root(con) == "h13"
    assert ids.next_patch(con, "h12") == "h12v2"
    db.add_hypothesis(con, "h12v2", "expiry mom", {})
    assert ids.next_patch(con, "h12v2") == "h12v3"
    assert ids.next_fork(con, "h12") == "h12.1"
    db.add_hypothesis(con, "h12.1", "expiry mom volgate", {})
    db.add_hypothesis(con, "h12.1.1", "x", {})
    assert ids.next_fork(con, "h12") == "h12.2"
    assert ids.next_fork(con, "h12v2") == "h12.2"
    assert ids.next_patch(con, "h12.1") == "h12.1v2"
    assert ids.next_fork(con, "h12.1") == "h12.1.2"
    with pytest.raises(ValueError):
        ids.next_fork(con, "h1.1.1.1")
    with pytest.raises(ValueError):
        ids.family("x12")
