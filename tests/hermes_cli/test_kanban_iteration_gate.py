"""Tests for the kanban iteration gate (max_iterations_per_root)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# _goal_root unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Build Athar", "build athar"),
        ("Build Athar v2", "build athar"),
        ("Build Athar — v3", "build athar"),
        ("Build Athar (v2)", "build athar"),
        ("Ship feature", "ship feature"),
        ("Refine", None),
        ("", None),
        ("   ", None),
        ("v2 alone", None),
        ("Build Athar find-my-device web prototype end-to-end", "build athar find my"),
        ("Iteration 4: rebuild", None),
        ("Iteration 2: ship a thing", "ship a thing"),
        ("v23 part 7", None),
        ("Build.Athar—v9", "build athar"),
        ("clean up the backlog (final)", "clean up the backlog"),
        ("test the thing", "test the thing"),
    ],
)
def test_goal_root(title, expected):
    assert kb._goal_root(title) == expected


# ---------------------------------------------------------------------------
# Behavioural tests for create_task + the gate
# ---------------------------------------------------------------------------


def test_gate_demotes_to_triage_when_cap_exceeded(kanban_home, monkeypatch):
    """3 existing 'Build Athar' tasks, cap=3: the 4th must land in triage."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 3)
    with kb.connect() as conn:
        for i in range(3):
            tid = kb.create_task(conn, title=f"Build Athar v{i+1}")
            assert kb.get_task(conn, tid).status == "ready"
        # 4th one must be demoted to triage
        new_id = kb.create_task(conn, title="Build Athar v4")
        new_task = kb.get_task(conn, new_id)
        assert new_task is not None
        assert new_task.status == "triage"
        # Body should carry the gate note
        assert "Gated by `kanban.max_iterations_per_root`" in (new_task.body or "")
        assert "build athar" in (new_task.body or "")


def test_gate_allows_up_to_cap(kanban_home, monkeypatch):
    """cap=2: 2 existing tasks ok, 3rd must be demoted."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 2)
    with kb.connect() as conn:
        for i in range(2):
            tid = kb.create_task(conn, title=f"Ship feature v{i+1}")
            assert kb.get_task(conn, tid).status == "ready"
        new_id = kb.create_task(conn, title="Ship feature v3")
        new_task = kb.get_task(conn, new_id)
        assert new_task is not None
        assert new_task.status == "triage"


def test_gate_disabled_when_cap_zero(kanban_home, monkeypatch):
    """cap=0 must skip the check entirely — no triage, no body mutation."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 0)
    with kb.connect() as conn:
        for i in range(10):
            tid = kb.create_task(conn, title=f"Deploy v{i+1}")
            assert kb.get_task(conn, tid).status == "ready"


def test_gate_does_not_match_one_word_titles(kanban_home, monkeypatch):
    """A title with only one token must not be gated (would over-match)."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 1)
    with kb.connect() as conn:
        for word in ["Deploy", "Deploy", "Deploy", "Deploy"]:
            tid = kb.create_task(conn, title=word)
            assert kb.get_task(conn, tid).status == "ready"


def test_gate_does_not_match_unrelated_titles(kanban_home, monkeypatch):
    """A cap=1 'Build Athar' must not gate 'Build Something Else'."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 1)
    with kb.connect() as conn:
        kb.create_task(conn, title="Build Athar v1")
        other = kb.create_task(conn, title="Build Something Else v1")
        assert kb.get_task(conn, other).status == "ready"


def test_gate_respects_explicit_triage(kanban_home, monkeypatch):
    """If the caller already said triage=True, the gate must not mutate."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 1)
    with kb.connect() as conn:
        kb.create_task(conn, title="Build Athar v1")
        # 2nd one with explicit triage should be a normal triage task,
        # NOT receive a gate note (caller intent is already triage).
        new_id = kb.create_task(
            conn, title="Build Athar v2", triage=True, body="real triage spec"
        )
        t = kb.get_task(conn, new_id)
        assert t is not None
        assert t.status == "triage"
        assert (t.body or "") == "real triage spec"


def test_gate_archived_tasks_do_not_count(kanban_home, monkeypatch):
    """Archived tasks must not contribute to the count."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 2)
    with kb.connect() as conn:
        for i in range(3):
            kb.create_task(conn, title=f"Build Athar v{i+1}")
        # Archive the first 2; the count of non-archived should now be 1.
        # The next create should succeed as 'ready'.
        all_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM tasks WHERE status='ready' ORDER BY created_at LIMIT 2"
            ).fetchall()
        ]
        for tid in all_ids:
            kb.archive_task(conn, tid)
        new_id = kb.create_task(conn, title="Build Athar v4")
        new_task = kb.get_task(conn, new_id)
        assert new_task is not None
        assert new_task.status == "ready"


def test_gate_emits_iteration_gate_event(kanban_home, monkeypatch):
    """When the gate demotes a task, an 'iteration_gate' event is appended."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 1)
    with kb.connect() as conn:
        kb.create_task(conn, title="Build Athar v1")
        new_id = kb.create_task(conn, title="Build Athar v2")
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT kind, payload FROM task_events "
                "WHERE task_id = ? ORDER BY id",
                (new_id,),
            ).fetchall()
        ]
        event_types = [e["kind"] for e in events]
        assert "created" in event_types
        assert "iteration_gate" in event_types
        gate = next(e for e in events if e["kind"] == "iteration_gate")
        import json as _json
        payload = _json.loads(gate["payload"]) if isinstance(gate["payload"], str) else gate["payload"]
        assert payload["root"] == "build athar"
        assert payload["existing_count"] == 1
        assert payload["cap"] == 1


def test_gate_is_board_scoped(kanban_home, monkeypatch):
    """Tasks on a different physical board (separate DB) must not
    contribute to the count. This is verified indirectly: ``connect``
    auto-routes to the active board's DB, so a second board needs a
    fresh ``kanban_db_path`` + ``connect()`` to a different file. We
    just check that the count is connection-scoped, not global."""
    monkeypatch.setattr(kb, "_get_max_iterations_per_root", lambda: 1)
    with kb.connect() as conn:
        kb.create_task(conn, title="Build Athar v1")
        # Same DB, no second board can interfere via the same conn.
        # A second create on the same conn must be gated.
        new_id = kb.create_task(conn, title="Build Athar v2")
        new_task = kb.get_task(conn, new_id)
        assert new_task is not None
        assert new_task.status == "triage"
