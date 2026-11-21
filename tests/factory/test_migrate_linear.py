"""Linear to GitHub Issues: idempotent, ordered, security issues kept in Linear."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from scripts.factory.migrate_linear import (
    MAX_CHARS,
    TRUNCATION_NOTE,
    GhError,
    _gh,
    is_security_sensitive,
    link_epics,
    main,
    migrate,
    preflight_labels,
    render_body,
    resolve,
    security_term,
)

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


def _comment(body: str) -> dict[str, str]:
    return {"author": "Abhishek", "createdAt": "2026-09-02T10:00:00Z", "body": body}


class FakeGh:
    """Records gh calls; issue numbers count up from 100."""

    def __init__(self, *, create_output: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.next_number = 100
        self.failures: dict[str, list[str]] = {}  # subcommand -> queued error messages
        self.create_output = create_output
        self.existing: list[dict[str, object]] = []  # what `gh issue list` finds

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        queue = self.failures.get(args[1], []) if len(args) > 1 else []
        if queue:
            raise GhError(queue.pop(0))
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.existing)
        if args[:2] == ["issue", "create"]:
            if self.create_output is not None:
                return self.create_output
            self.next_number += 1
            return f"https://github.com/darth-dodo/cantastorie/issues/{self.next_number}"
        return ""

    def of(self, sub: str) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["issue", sub]]


def _numbers(mapping: Path) -> dict[str, int]:
    data = json.loads(mapping.read_text())
    return {k: v["number"] for k, v in data.items() if k != "needs_manual_check"}


def _no_sleep(_s: float) -> None:
    return None


def test_body_starts_with_the_linear_link_and_keeps_the_description() -> None:
    body = render_body(_issue(5))
    assert body.splitlines()[0] == "Migrated from Linear AI-5 (https://linear.app/x/issue/AI-5)"
    assert "Body text." in body
    assert "Linear priority: Medium" in body


def test_security_issues_are_kept_in_linear() -> None:
    leak = _issue(7, title="M6: a leaked family token is permanent account takeover")
    assert is_security_sensitive(leak)
    assert not is_security_sensitive(_issue(8, title="Add a favicon"))


def test_a_vulnerability_only_in_a_comment_is_caught() -> None:
    issue = _issue(9, comments=[_comment("Found an XSS in the title field")])
    assert is_security_sensitive(issue)
    assert security_term(issue) is not None


def test_a_security_label_alone_is_caught() -> None:
    assert is_security_sensitive(_issue(10, labels=["Security"]))


def test_new_patterns_catch_idor_and_friends() -> None:
    assert security_term(_issue(11, title="IDOR on /parent/stories/{id}")) is not None
    for text in (
        "unauthorised read",
        "privilege escalation",
        "session fixation",
        "brute force login",
        "PII in logs",
    ):
        assert is_security_sensitive(_issue(12, title=text)), text
    assert not is_security_sensitive(_issue(13, title="Narration speed slider"))


def test_issues_are_created_in_ai_number_order_and_mapped(tmp_path: Path) -> None:
    gh, mapping = FakeGh(), tmp_path / "map.json"
    migrate([_issue(3), _issue(1), _issue(2)], mapping, gh, dry_run=False)
    titles = [c[c.index("--title") + 1] for c in gh.of("create")]
    assert titles == ["Issue 1", "Issue 2", "Issue 3"]
    assert _numbers(mapping) == {"AI-1": 101, "AI-2": 102, "AI-3": 103}


def test_a_rerun_creates_nothing_new(tmp_path: Path) -> None:
    mapping = tmp_path / "map.json"
    migrate([_issue(1), _issue(2)], mapping, FakeGh(), dry_run=False)
    second = FakeGh()
    migrate([_issue(1), _issue(2)], mapping, second, dry_run=False)
    assert not second.of("create")


def test_an_interrupted_run_resumes_where_it_stopped(tmp_path: Path) -> None:
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"AI-1": 101}))  # older files hold a bare int
    gh = FakeGh()
    migrate([_issue(1), _issue(2)], mapping, gh, dry_run=False)
    created = gh.of("create")
    assert len(created) == 1 and "Issue 2" in created[0]
    assert _numbers(mapping) == {"AI-1": 101, "AI-2": 101}


def test_done_closes_as_completed_and_canceled_as_not_planned(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate(
        [_issue(1, status="Done"), _issue(2, status="Canceled")],
        tmp_path / "m.json",
        gh,
        dry_run=False,
    )
    closes = gh.of("close")
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


def test_duplicate_closes_as_not_planned(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate(
        [_issue(1, status="Duplicate", duplicateOf="AI-2"), _issue(2)],
        tmp_path / "m.json",
        gh,
        dry_run=False,
    )
    assert [
        "issue",
        "close",
        "101",
        "--repo",
        "darth-dodo/cantastorie",
        "--reason",
        "not planned",
    ] in gh.of("close")
    create = gh.of("create")[0]
    assert "Duplicate of: AI-2" in create[create.index("--body") + 1]


def _labels_of(create: list[str]) -> list[str]:
    return [create[i + 1] for i, a in enumerate(create) if a == "--label"]


def test_priority_and_status_become_labels(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate([_issue(1, priority=1, status="In Review")], tmp_path / "m.json", gh, dry_run=False)
    labels = _labels_of(gh.of("create")[0])
    assert "priority:urgent" in labels and "status:in-progress" in labels


def test_bug_and_feature_labels_carry_over_and_others_do_not(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate([_issue(1, labels=["Bug", "Feature", "Voice"])], tmp_path / "m.json", gh, dry_run=False)
    labels = _labels_of(gh.of("create")[0])
    assert "bug" in labels and "feature" in labels and "voice" not in labels


def test_an_issue_with_children_is_labelled_epic(tmp_path: Path) -> None:
    gh = FakeGh()
    migrate([_issue(1), _issue(2, parent="AI-1")], tmp_path / "m.json", gh, dry_run=False)
    assert "epic" in _labels_of(gh.of("create")[0])


def test_link_epics_rewrites_both_sides_with_github_numbers() -> None:
    gh = FakeGh()
    export = [_issue(1), _issue(2, parent="AI-1")]
    link_epics(export, {"AI-1": 101, "AI-2": 102}, gh)
    edits = {c[2]: c[c.index("--body") + 1] for c in gh.of("edit")}
    assert "## Child issues\n- #102" in edits["101"]
    assert "Epic: #101" in edits["102"] and "Epic: AI-1" not in edits["102"]


def test_link_epics_leaves_a_matching_line_in_the_description_alone() -> None:
    gh = FakeGh()
    child = _issue(2, parent="AI-1", description="Notes\nEpic: AI-1 was the old plan")
    link_epics([_issue(1), child], {"AI-1": 101, "AI-2": 102}, gh)
    body = next(c for c in gh.of("edit") if c[2] == "102")
    text = body[body.index("--body") + 1]
    assert "Epic: AI-1 was the old plan" in text and "Epic: #101" in text


def test_dry_run_calls_gh_for_nothing_and_lists_both_sides(tmp_path: Path) -> None:
    gh = FakeGh()
    report = migrate(
        [_issue(1), _issue(2, title="Auth bypass on /parent")],
        tmp_path / "m.json",
        gh,
        dry_run=True,
    )
    assert gh.calls == []
    assert report.to_create == [{"identifier": "AI-1", "title": "Issue 1"}]
    assert len(report.kept_in_linear) == 1
    kept = report.kept_in_linear[0]
    assert (
        kept["identifier"] == "AI-2" and kept["title"] == "Auth bypass on /parent" and kept["term"]
    )
    json.dumps(report.__dict__)


def test_a_failed_comment_is_finished_on_rerun(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    issue = _issue(1, comments=[_comment("hello")])
    gh = FakeGh()
    gh.failures["comment"] = ["boom"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    assert _numbers(mapping) == {"AI-1": 101}
    second = FakeGh()
    migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and len(second.of("comment")) == 1
    third = FakeGh()
    migrate([issue], mapping, third, dry_run=False, sleep=_no_sleep)
    assert not third.calls


def test_a_failed_close_is_finished_on_rerun(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    issue = _issue(1, status="Done", comments=[_comment("hello")])
    gh = FakeGh()
    gh.failures["close"] = ["boom"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and not second.of("comment") and len(second.of("close")) == 1


def test_rate_limits_are_retried_with_backoff(tmp_path: Path) -> None:
    gh, sleeps = FakeGh(), []
    gh.failures["create"] = ["HTTP 403: API rate limit exceeded", "HTTP 429: too many requests"]
    migrate([_issue(1)], tmp_path / "m.json", gh, dry_run=False, sleep=sleeps.append)
    assert len(gh.of("create")) == 3
    assert sleeps == [60, 120]


def test_other_errors_are_not_retried(tmp_path: Path) -> None:
    gh, sleeps = FakeGh(), []
    gh.failures["create"] = ["label not found"]
    with pytest.raises(GhError):
        migrate([_issue(1)], tmp_path / "m.json", gh, dry_run=False, sleep=sleeps.append)
    assert len(gh.of("create")) == 1 and sleeps == []


def test_persistent_rate_limit_gives_up_after_three_retries(tmp_path: Path) -> None:
    gh, sleeps = FakeGh(), []
    gh.failures["create"] = ["rate limit"] * 10
    with pytest.raises(GhError):
        migrate([_issue(1)], tmp_path / "m.json", gh, dry_run=False, sleep=sleeps.append)
    assert len(gh.of("create")) == 4 and sleeps == [60, 120, 240]


def test_a_long_body_is_truncated_with_a_note() -> None:
    body = render_body(_issue(1, description="x" * 100_000))
    assert len(body) <= MAX_CHARS
    assert TRUNCATION_NOTE in body
    assert "Linear status: Todo" in body


def test_long_comments_are_split_in_order_under_the_limit(tmp_path: Path) -> None:
    gh = FakeGh()
    comments = [_comment("a" * 40_000), _comment("b" * 40_000), _comment("c" * 70_000)]
    migrate([_issue(1, comments=comments)], tmp_path / "m.json", gh, dry_run=False, sleep=_no_sleep)
    posted = [c[c.index("--body") + 1] for c in gh.of("comment")]
    assert len(posted) >= 3 and all(len(p) <= MAX_CHARS for p in posted)
    joined = "".join(posted)
    assert joined.index("a" * 100) < joined.index("b" * 100) < joined.index("c" * 100)
    assert joined.count("c") == 70_000


def test_an_unparseable_create_output_is_recorded_and_never_recreated(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    with pytest.raises(RuntimeError):
        migrate([_issue(1)], mapping, FakeGh(create_output="something odd"), dry_run=False)
    data = json.loads(mapping.read_text())
    assert data["needs_manual_check"] == [{"identifier": "AI-1", "output": "something odd"}]
    again = FakeGh()
    report = migrate([_issue(1)], mapping, again, dry_run=False)
    assert not again.of("create") and report.needs_manual_check == ["AI-1"]


def test_the_mapping_file_is_written_atomically(tmp_path: Path) -> None:
    migrate([_issue(1)], tmp_path / "m.json", FakeGh(), dry_run=False)
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_preflight_reports_missing_labels() -> None:
    export = [_issue(1, priority=1), _issue(2, parent="AI-1")]

    def gh(args: list[str]) -> str:
        assert args[:2] == ["label", "list"]
        return json.dumps([{"name": "priority:urgent"}, {"name": "priority:medium"}])

    with pytest.raises(SystemExit) as exc:
        preflight_labels(export, gh)
    assert "epic" in str(exc.value)


def test_preflight_passes_when_all_labels_exist() -> None:
    def gh(args: list[str]) -> str:
        return json.dumps([{"name": "priority:medium"}])

    preflight_labels([_issue(1)], gh)


def test_a_create_that_failed_after_github_made_the_issue_is_found_not_duplicated(
    tmp_path: Path,
) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1)
    gh = FakeGh()
    gh.failures["create"] = ["502 Bad Gateway"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    second.existing = [{"number": 150, "body": render_body(issue)}]
    migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and _numbers(mapping) == {"AI-1": 150}
    search = second.of("list")[0]
    assert "--search" not in search
    assert (
        search[search.index("--limit") + 1] == "100" and "number,body" in search and "all" in search
    )


def test_a_moved_linear_url_still_matches_on_the_prefix(tmp_path: Path) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1)
    gh = FakeGh()
    gh.failures["create"] = ["timeout"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    second.existing = [
        {
            "number": 150,
            "body": "Migrated from Linear AI-1 (https://linear.app/new/issue/AI-1)\n\nx",
        }
    ]
    migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert _numbers(mapping) == {"AI-1": 150}


def test_an_attempted_create_that_is_not_found_goes_to_manual_check_not_recreated(
    tmp_path: Path,
) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1)
    gh = FakeGh()
    gh.failures["create"] = ["502 Bad Gateway"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    report = migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and report.needs_manual_check == ["AI-1"]
    assert json.loads(mapping.read_text())["needs_manual_check"][0]["identifier"] == "AI-1"


def test_a_body_that_only_looks_similar_is_not_a_match(tmp_path: Path) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1)
    gh = FakeGh()
    gh.failures["create"] = ["timeout"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    second.existing = [
        {"number": 150, "body": "Migrated from Linear AI-10 (https://linear.app/x/issue/AI-10)"}
    ]
    report = migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and report.needs_manual_check == ["AI-1"]


def test_a_failed_search_goes_to_manual_check(tmp_path: Path) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1)
    gh = FakeGh()
    gh.failures["create"] = ["timeout"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    second = FakeGh()
    second.failures["list"] = ["search is down"]
    report = migrate([issue], mapping, second, dry_run=False, sleep=_no_sleep)
    assert not second.of("create") and report.needs_manual_check == ["AI-1"]


def test_gh_timeouts_and_failures_become_gh_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def timing_out(cmd: list[str], **kwargs: object) -> object:
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(subprocess, "run", timing_out)
    with pytest.raises(GhError, match="timed out"):
        _gh(["issue", "create"])
    assert seen["timeout"] == 120

    def failing(cmd: list[str], **kwargs: object) -> object:
        raise subprocess.CalledProcessError(1, cmd, stderr="HTTP 502\n")

    monkeypatch.setattr(subprocess, "run", failing)
    with pytest.raises(GhError, match="HTTP 502"):
        _gh(["issue", "create"])


def test_resolve_records_the_number_and_clears_both_lists(tmp_path: Path) -> None:
    mapping, issue = tmp_path / "m.json", _issue(1, status="Done")
    gh = FakeGh()
    gh.failures["create"] = ["502"]
    with pytest.raises(GhError):
        migrate([issue], mapping, gh, dry_run=False, sleep=_no_sleep)
    migrate(
        [issue], mapping, FakeGh(), dry_run=False, sleep=_no_sleep
    )  # lands in needs_manual_check
    resolve(mapping, "AI-1", 42)
    data = json.loads(mapping.read_text())
    assert data["AI-1"] == {"number": 42, "commented": False, "closed": False, "comment_chunks": 0}
    assert "needs_manual_check" not in data and "create_attempted" not in data
    after = FakeGh()
    migrate([issue], mapping, after, dry_run=False, sleep=_no_sleep)
    assert not after.of("create") and len(after.of("close")) == 1


def test_resolve_cli_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = tmp_path / "m.json"
    monkeypatch.setattr(
        sys, "argv", ["migrate_linear", "--mapping", str(mapping), "--resolve", "AI-7=77"]
    )
    main()
    assert _numbers(mapping) == {"AI-7": 77}


@pytest.mark.parametrize("order", [[1, 2, 3], [3, 2, 1]])
def test_a_three_level_epic_chain_edits_each_issue_once(order: list[int]) -> None:
    gh = FakeGh()
    items = {1: _issue(1), 2: _issue(2, parent="AI-1"), 3: _issue(3, parent="AI-2")}
    link_epics([items[n] for n in order], {"AI-1": 101, "AI-2": 102, "AI-3": 103}, gh)
    edits = gh.of("edit")
    assert sorted(c[2] for c in edits) == ["101", "102", "103"]
    bodies = {c[2]: c[c.index("--body") + 1] for c in edits}
    assert "## Child issues\n- #102" in bodies["101"]
    assert "Epic: #101" in bodies["102"] and "## Child issues\n- #103" in bodies["102"]
    assert "Epic: #102" in bodies["103"]


def test_legacy_bare_int_entries_are_not_reposted(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps({"AI-1": 101}))
    gh = FakeGh()
    migrate([_issue(1, status="Done", comments=[_comment("hi")])], mapping, gh, dry_run=False)
    assert not gh.calls


def test_a_null_comment_body_is_treated_as_empty(tmp_path: Path) -> None:
    issue = _issue(1, comments=[_comment("fine"), {"author": "A", "createdAt": "t", "body": None}])
    assert not is_security_sensitive(issue)
    gh = FakeGh()
    migrate([issue], tmp_path / "m.json", gh, dry_run=False)
    assert len(gh.of("comment")) == 1


def test_an_already_public_issue_that_matches_security_still_finishes(tmp_path: Path) -> None:
    mapping = tmp_path / "m.json"
    issue = _issue(1, title="Auth bypass", status="Done")
    mapping.write_text(json.dumps({"AI-1": {"number": 101, "commented": True, "closed": False}}))
    gh = FakeGh()
    report = migrate([issue, _issue(2, title="XSS in title")], mapping, gh, dry_run=False)
    assert len(gh.of("close")) == 1 and not gh.of("create")
    assert [r["identifier"] for r in report.already_public_but_matches_security] == ["AI-1"]
    assert [r["identifier"] for r in report.kept_in_linear] == ["AI-2"]


def test_a_403_that_is_not_a_rate_limit_is_not_retried(tmp_path: Path) -> None:
    gh, sleeps = FakeGh(), []
    gh.failures["create"] = ["HTTP 403: Resource not accessible by integration"]
    with pytest.raises(GhError):
        migrate([_issue(1)], tmp_path / "m.json", gh, dry_run=False, sleep=sleeps.append)
    assert sleeps == []


def test_abuse_and_secondary_rate_limits_are_retried(tmp_path: Path) -> None:
    gh, sleeps = FakeGh(), []
    gh.failures["create"] = [
        "You have exceeded a secondary rate limit",
        "abuse detection triggered",
    ]
    migrate([_issue(1)], tmp_path / "m.json", gh, dry_run=False, sleep=sleeps.append)
    assert sleeps == [60, 120]
