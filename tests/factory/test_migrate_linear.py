"""Linear to GitHub Issues: idempotent, ordered, security issues kept in Linear."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.factory.migrate_linear import is_security_sensitive, link_epics, migrate, render_body

if TYPE_CHECKING:
    from pathlib import Path


def _issue(n: int, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "identifier": f"AI-{n}",
        "title": f"Issue {n}",
        "description": "Body text.",
        "assignee": None,
        "status": "Todo",
        "priority": 3,
        "labels": [],
        "parent": None,
        "createdAt": "2026-09-01T10:00:00Z",
        "completedAt": None,
        "url": f"https://linear.app/x/issue/AI-{n}",
        "comments": [],
        "duplicateOf": None,
    }
    base.update(extra)
    return base


class FakeGh:
    """Records gh calls; issue numbers count up from 100."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.next_number = 100

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "create"]:
            self.next_number += 1
            return f"https://github.com/darth-dodo/cantastorie/issues/{self.next_number}"
        return ""


def test_body_starts_with_the_linear_link_and_keeps_the_description() -> None:
    body = render_body(_issue(5))
    assert body.splitlines()[0] == "Migrated from Linear AI-5 (https://linear.app/x/issue/AI-5)"
    assert "Body text." in body
    assert "Linear priority: Medium" in body


def test_security_issues_are_kept_in_linear() -> None:
    leak = _issue(7, title="M6: a leaked family token is permanent account takeover")
    assert is_security_sensitive(leak)
    assert not is_security_sensitive(_issue(8, title="Add a favicon"))


def test_issues_are_created_in_ai_number_order_and_mapped(tmp_path: Path) -> None:
    gh, mapping = FakeGh(), tmp_path / "map.json"
    migrate([_issue(3), _issue(1), _issue(2)], mapping, gh, dry_run=False)
    titles = [c[c.index("--title") + 1] for c in gh.calls if c[:2] == ["issue", "create"]]
    assert titles == ["Issue 1", "Issue 2", "Issue 3"]
    assert json.loads(mapping.read_text()) == {"AI-1": 101, "AI-2": 102, "AI-3": 103}


def test_a_rerun_creates_nothing_new(tmp_path: Path) -> None:
    mapping = tmp_path / "map.json"
    migrate([_issue(1), _issue(2)], mapping, FakeGh(), dry_run=False)
    second = FakeGh()
    migrate([_issue(1), _issue(2)], mapping, second, dry_run=False)
    assert not [c for c in second.calls if c[:2] == ["issue", "create"]]


def test_an_interrupted_run_resumes_where_it_stopped(tmp_path: Path) -> None:
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"AI-1": 101}))
    gh = FakeGh()
    migrate([_issue(1), _issue(2)], mapping, gh, dry_run=False)
    created = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert len(created) == 1 and "Issue 2" in created[0]


def test_done_closes_as_completed_and_canceled_as_not_planned(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate(
        [_issue(1, status="Done"), _issue(2, status="Canceled")],
        tmp_path / "m.json",
        gh,
        dry_run=False,
    )
    closes = [c for c in gh.calls if c[:2] == ["issue", "close"]]
    assert [
        "issue",
        "close",
        "101",
        "--repo",
        "darth-dodo/cantastorie",
        "--reason",
        "completed",
    ] in closes
    assert [
        "issue",
        "close",
        "102",
        "--repo",
        "darth-dodo/cantastorie",
        "--reason",
        "not planned",
    ] in closes


def test_priority_and_status_become_labels(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate([_issue(1, priority=1, status="In Review")], tmp_path / "m.json", gh, dry_run=False)
    create = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    labels = [create[i + 1] for i, a in enumerate(create) if a == "--label"]
    assert "priority:urgent" in labels and "status:in-progress" in labels


def test_an_issue_with_children_is_labelled_epic(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate([_issue(1), _issue(2, parent="AI-1")], tmp_path / "m.json", gh, dry_run=False)
    first = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    assert "epic" in [first[i + 1] for i, a in enumerate(first) if a == "--label"]


def test_link_epics_rewrites_both_sides_with_github_numbers() -> None:
    gh = FakeGh()
    export = [_issue(1), _issue(2, parent="AI-1")]
    link_epics(export, {"AI-1": 101, "AI-2": 102}, gh)
    edits = {c[2]: c[c.index("--body") + 1] for c in gh.calls if c[:2] == ["issue", "edit"]}
    assert "## Child issues\n- #102" in edits["101"]
    assert "Epic: #101" in edits["102"] and "Epic: AI-1" not in edits["102"]


def test_dry_run_calls_gh_for_nothing(tmp_path: Path) -> None:
    gh = FakeGh()
    report = migrate(
        [_issue(1), _issue(2, title="Auth bypass on /parent")],
        tmp_path / "m.json",
        gh,
        dry_run=True,
    )
    assert gh.calls == []
    assert report.to_create == ["AI-1"] and report.kept_in_linear == ["AI-2"]
