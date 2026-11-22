"""Copy Linear's Cantastorie issues to GitHub Issues (spec: software factory, Part 2).

Dry run by default. Idempotent and resumable: a mapping file records, per issue, the
GitHub number and whether the comment and close steps finished. It is rewritten after
every step, so a rerun finishes whatever was left. Security-sensitive issues stay in Linear.

Clearing the manual lists: an issue lands in `needs_manual_check` or `create_attempted`
(both top-level keys of the mapping file) when a create may or may not have reached
GitHub. Look the issue up on GitHub by hand, then either run
`--resolve AI-N=<github number>` or edit the mapping file yourself: remove the identifier
from those lists and add `"AI-N": {"number": <github number>, "commented": false,
"closed": false}` (or just delete the entry if nothing was created). The next `--apply`
then finishes the comment and close steps.

Clearing security false positives: the security filter is deliberately broad. Review the
dry run's `kept_in_linear` list, then put the identifiers you cleared in a text file, one
per line (blank lines and `#` comments are ignored), and pass it with `--allow FILE`.
Those issues skip the filter and migrate normally. The ones that had matched are listed
under `allowed_by_owner` in the report so the decision stays visible. An identifier that is
not in the export is an error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = "darth-dodo/cantastorie"
MAX_CHARS = 65536  # GitHub's limit for issue bodies and comments
TRUNCATION_NOTE = "(truncated; see the Linear link above)"
GH_TIMEOUT_S = 120
BACKOFF_S = (60.0, 120.0, 240.0)
RATE_LIMIT = re.compile(r"rate limit|secondary rate|HTTP 429|abuse", re.I)
PRIORITY = {1: "priority:urgent", 2: "priority:high", 3: "priority:medium", 4: "priority:low"}
PRIORITY_NAME = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
CLOSE_REASON = {"Done": "completed", "Canceled": "not planned", "Duplicate": "not planned"}
SECURITY = re.compile(
    r"token (exposure|leak)|leaked .*token|account takeover|auth(entication|orization)? bypass|"
    r"unauthenticated|unauthori[sz]ed|\bidor\b|privilege|bypass|expos(ed|ure)|leak|"
    r"session fixation|brute.?force|\bpii\b|secret|credential|csrf|xss|injection|exploit|vulnerab",
    re.I,
)
SECURITY_LABEL = re.compile(r"security", re.I)
ISSUE_URL = re.compile(r"/issues/(\d+)\s*$", re.M)


class GhError(RuntimeError):
    """A gh invocation failed; the message is gh's stderr."""


class GhRunner(Protocol):
    def __call__(self, args: list[str]) -> str: ...


@dataclass
class MigrationReport:
    to_create: list[dict[str, str]] = field(default_factory=list)
    already_mapped: list[str] = field(default_factory=list)
    kept_in_linear: list[dict[str, str]] = field(default_factory=list)
    allowed_by_owner: list[dict[str, str]] = field(default_factory=list)
    needs_manual_check: list[str] = field(default_factory=list)
    already_public_but_matches_security: list[dict[str, str]] = field(default_factory=list)


def _number(identifier: str) -> int:
    return int(identifier.split("-")[1])


def security_term(issue: dict[str, Any]) -> str | None:
    """The first security term found in the title, description, comments or labels."""
    texts = [issue.get("title", ""), issue.get("description") or ""]
    texts += [c.get("body") or "" for c in issue.get("comments", [])]
    for text in texts:
        match = SECURITY.search(text)
        if match:
            return match.group(0)
    for label in issue.get("labels", []):
        match = SECURITY.search(label) or SECURITY_LABEL.search(label)
        if match:
            return f"label: {label}"
    return None


def load_allow(path: Path) -> frozenset[str]:
    """Identifiers from the owner's allow file: one per line, `#` starts a comment."""
    idents = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return frozenset(i for i in idents if i)


def is_security_sensitive(issue: dict[str, Any]) -> bool:
    return security_term(issue) is not None


def render_body(issue: dict[str, Any], epic_ref: str | None = None) -> str:
    head = [f"Migrated from Linear {issue['identifier']} ({issue['url']})", ""]
    foot = ["", "---", f"Linear status: {issue['status']}"]
    foot.append(f"Linear priority: {PRIORITY_NAME.get(issue.get('priority') or 0, 'None')}")
    if issue.get("assignee"):
        foot.append(f"Assignee: {issue['assignee']}")
    if issue.get("parent"):
        foot.append(f"Epic: {epic_ref or issue['parent']}")
    foot.append(f"Created: {issue['createdAt']}")
    if issue.get("completedAt"):
        foot.append(f"Completed: {issue['completedAt']}")
    if issue.get("duplicateOf"):
        foot.append(f"Duplicate of: {issue['duplicateOf']}")
    description = issue.get("description") or "(no description)"
    fixed = len("\n".join([*head, "", *foot]))
    room = MAX_CHARS - fixed - 2000  # headroom for the child-issue list added by link_epics
    if len(description) > room:
        description = description[: room - len(TRUNCATION_NOTE) - 2] + "\n\n" + TRUNCATION_NOTE
    return "\n".join([*head, description, *foot])


def _labels(issue: dict[str, Any], epics: set[str]) -> list[str]:
    labels = [PRIORITY[issue["priority"]]] if issue.get("priority") in PRIORITY else []
    if issue["identifier"] in epics:
        labels.append("epic")
    if issue["status"] in ("In Progress", "In Review"):
        labels.append("status:in-progress")
    for name in issue.get("labels", []):
        if name.lower() in ("bug", "feature", "epic") and name.lower() not in labels:
            labels.append(name.lower())
    return labels


def _comment_chunks(issue: dict[str, Any], limit: int = MAX_CHARS) -> list[str]:
    """Comments in order, packed into GitHub comments that each fit under the limit."""
    pieces: list[str] = []
    for c in issue.get("comments", []):
        text = f"**{c['author']}** ({c['createdAt']}):\n\n{c.get('body') or ''}"
        pieces += [text[i : i + limit] for i in range(0, len(text), limit)]
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 2 + len(piece) <= limit:
            chunks[-1] += "\n\n" + piece
        else:
            chunks.append(piece)
    return chunks


@dataclass
class _State:
    """The mapping file in memory: per-issue progress, manual-check notes, create attempts."""

    path: Path
    entries: dict[str, dict[str, Any]]
    manual: list[dict[str, str]]
    attempted: list[str]

    def save(self) -> None:
        data: dict[str, Any] = dict(self.entries)
        if self.manual:
            data["needs_manual_check"] = self.manual
        if self.attempted:
            data["create_attempted"] = self.attempted
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".mapping-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
            Path(tmp).replace(self.path)  # atomic, same as os.replace
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _new_entry(number: int) -> dict[str, Any]:
    return {"number": number, "commented": False, "closed": False, "comment_chunks": 0}


def _load(mapping_path: Path, export: list[dict[str, Any]] | None = None) -> _State:
    raw: dict[str, Any] = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    manual: list[dict[str, str]] = raw.pop("needs_manual_check", [])
    attempted: list[str] = raw.pop("create_attempted", [])
    by_id = {i["identifier"]: i for i in export or []}
    entries: dict[str, dict[str, Any]] = {}
    for ident, value in raw.items():
        if isinstance(value, dict):
            entries[ident] = {**_new_entry(value["number"]), **value}
        else:  # legacy bare int: its steps ran before progress was tracked, so repost nothing
            chunks = len(_comment_chunks(by_id[ident])) if ident in by_id else 0
            entries[ident] = {
                "number": value,
                "commented": True,
                "closed": True,
                "comment_chunks": chunks,
            }
    return _State(mapping_path, entries, manual, attempted)


def load_numbers(mapping_path: Path) -> dict[str, int]:
    return {ident: int(e["number"]) for ident, e in _load(mapping_path).entries.items()}


def _call(
    gh: GhRunner, args: list[str], sleep: Callable[[float], None], backoff: tuple[float, ...]
) -> str:
    """Run gh, retrying with backoff only when the failure looks like a rate limit."""
    for delay in backoff:
        try:
            return gh(args)
        except GhError as exc:
            if not RATE_LIMIT.search(str(exc)):
                raise
            sleep(delay)
    return gh(args)


def _find_existing(issue: dict[str, Any], run: Callable[[list[str]], str]) -> int | None:
    """Look in the 100 newest issues for one whose body starts with this issue's migration line.

    A plain listing (no --search) reads straight from the database, so it has no index lag.
    The prefix stops at the open parenthesis: it excludes AI-10 when looking for AI-1 and
    survives a changed Linear URL.
    """
    prefix = f"Migrated from Linear {issue['identifier']} ("
    out = run(
        [
            "issue",
            "list",
            "--repo",
            REPO,
            "--state",
            "all",
            "--limit",
            "100",
            "--json",
            "number,body",
        ]
    )
    for item in json.loads(out):
        lines = (item.get("body") or "").splitlines()
        if lines and lines[0].startswith(prefix):
            return int(item["number"])
    return None


def _create(
    issue: dict[str, Any],
    epics: set[str],
    state: _State,
    run: Callable[[list[str]], str],
) -> bool:
    """Make sure the issue has a GitHub number. False means it went to needs_manual_check."""
    ident = issue["identifier"]
    if ident in state.attempted:  # an earlier create may have succeeded without our seeing it
        try:
            existing = _find_existing(issue, run)
        except (GhError, ValueError) as exc:
            state.manual.append({"identifier": ident, "output": f"search failed: {exc}"})
            state.save()
            return False
        if existing is None:  # rare, and a human should look rather than risk a duplicate
            state.manual.append({"identifier": ident, "output": "attempted create not found"})
            state.save()
            return False
        state.entries[ident] = _new_entry(existing)
        state.attempted.remove(ident)
        state.save()
        return True
    else:
        state.attempted.append(ident)
        state.save()
    args = [
        "issue",
        "create",
        "--repo",
        REPO,
        "--title",
        issue["title"],
        "--body",
        render_body(issue),
    ]
    for label in _labels(issue, epics):
        args += ["--label", label]
    out = run(args)
    found = ISSUE_URL.search(out)
    if not found:
        state.manual.append({"identifier": ident, "output": out})
        state.save()
        raise RuntimeError(f"could not read the issue number for {ident} from gh output: {out!r}")
    state.entries[ident] = _new_entry(int(found.group(1)))
    state.attempted.remove(ident)
    state.save()
    return True


def _finish(
    issue: dict[str, Any],
    entry: dict[str, Any],
    run: Callable[[list[str]], str],
    save: Callable[[], None],
) -> None:
    """Comment and close steps, each recorded as it lands so a rerun resumes mid-issue."""
    number = str(entry["number"])
    chunks = _comment_chunks(issue)
    while entry["comment_chunks"] < len(chunks):
        run(["issue", "comment", number, "--repo", REPO, "--body", chunks[entry["comment_chunks"]]])
        entry["comment_chunks"] += 1
        save()
    if not entry["commented"]:
        entry["commented"] = True
        save()
    reason = CLOSE_REASON.get(issue["status"])
    if reason and not entry["closed"]:
        run(["issue", "close", number, "--repo", REPO, "--reason", reason])
        entry["closed"] = True
        save()


def migrate(
    export: list[dict[str, Any]],
    mapping_path: Path,
    gh: GhRunner,
    *,
    dry_run: bool,
    pause_s: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    backoff: tuple[float, ...] = BACKOFF_S,
    allow: frozenset[str] = frozenset(),
) -> MigrationReport:
    unknown = sorted(allow - {i["identifier"] for i in export}, key=_number)
    if unknown:
        raise SystemExit(f"--allow lists identifiers not in the export: {', '.join(unknown)}")
    state = _load(mapping_path, export)
    stuck = {m["identifier"] for m in state.manual}
    epics = {i["parent"] for i in export if i.get("parent")}
    report = MigrationReport()

    def run(args: list[str]) -> str:
        return _call(gh, args, sleep, backoff)

    for issue in sorted(export, key=lambda i: _number(i["identifier"])):
        ident = issue["identifier"]
        found_term = security_term(issue)
        row = {"identifier": ident, "title": issue["title"], "term": found_term or ""}
        term = None if ident in allow else found_term
        if found_term and term is None:
            report.allowed_by_owner.append(row)
        mapped = ident in state.entries
        if term and not mapped:
            report.kept_in_linear.append(row)
            continue
        if term:  # already public: still finish its steps, but tell the owner
            report.already_public_but_matches_security.append(row)
        if ident in stuck and not mapped:
            report.needs_manual_check.append(ident)
            continue
        if mapped:
            report.already_mapped.append(ident)
        else:
            report.to_create.append({"identifier": ident, "title": issue["title"]})
        if dry_run:
            continue
        created_now = not mapped
        if created_now and not _create(issue, epics, state, run):
            report.to_create.pop()
            report.needs_manual_check.append(ident)
            continue
        _finish(issue, state.entries[ident], run, state.save)
        if created_now and pause_s:
            sleep(pause_s)  # stay well under GitHub's content-creation rate limit
    return report


def link_epics(export: list[dict[str, Any]], mapping: dict[str, int], gh: GhRunner) -> None:
    """Second pass, once every issue exists: children name their epic as #N, epics list children."""
    by_id = {i["identifier"]: i for i in export}
    children: dict[str, list[str]] = {}
    for issue in export:
        if issue.get("parent") in mapping and issue["identifier"] in mapping:
            children.setdefault(issue["parent"], []).append(issue["identifier"])

    def ref(ident: str) -> str | None:
        parent = by_id[ident].get("parent")
        return f"#{mapping[parent]}" if parent in mapping else None

    for ident in sorted(
        set(children) | {k for kids in children.values() for k in kids}, key=_number
    ):
        body = render_body(by_id[ident], ref(ident))
        kids = children.get(ident, [])
        if kids:
            listing = "\n".join(f"- #{mapping[k]}" for k in sorted(kids, key=_number))
            body = f"{body}\n\n## Child issues\n{listing}"
        gh(["issue", "edit", str(mapping[ident]), "--repo", REPO, "--body", body])


def preflight_labels(
    export: list[dict[str, Any]], gh: GhRunner, allow: frozenset[str] = frozenset()
) -> None:
    """Exit clearly if the repo lacks any label the migration is about to use."""
    epics = {i["parent"] for i in export if i.get("parent")}
    needed = {
        label
        for i in export
        if i["identifier"] in allow or not is_security_sensitive(i)
        for label in _labels(i, epics)
    }
    existing = {
        item["name"]
        for item in json.loads(
            gh(["label", "list", "--repo", REPO, "--json", "name", "--limit", "500"])
        )
    }
    missing = sorted(needed - existing)
    if missing:
        raise SystemExit(
            f"Missing labels in {REPO}: {', '.join(missing)}. Create them first (Task 6)."
        )


def _gh(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True, timeout=GH_TIMEOUT_S
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise GhError((exc.stderr or "").strip() or f"gh exited with {exc.returncode}") from None
    except subprocess.TimeoutExpired:
        raise GhError(f"gh timed out after {GH_TIMEOUT_S}s") from None


def resolve(mapping_path: Path, ident: str, number: int) -> None:
    """Record a hand-checked GitHub number and clear the identifier from both manual lists."""
    state = _load(mapping_path)
    state.entries[ident] = _new_entry(number)
    state.manual = [m for m in state.manual if m["identifier"] != ident]
    state.attempted = [a for a in state.attempted if a != ident]
    state.save()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=__doc__.split("\n\n", 2)[2],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--export", type=Path)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="create issues (default: dry run)")
    parser.add_argument("--link-epics", action="store_true", help="second pass: fill epic links")
    parser.add_argument(
        "--resolve",
        action="append",
        default=[],
        metavar="AI-N=NUMBER",
        help="record a hand-checked GitHub number and clear the manual lists for AI-N",
    )
    parser.add_argument(
        "--allow",
        type=Path,
        metavar="FILE",
        help="text file of Linear identifiers (one per line, # comments) that skip the security "
        "filter",
    )
    args = parser.parse_args()
    if args.resolve:
        for item in args.resolve:
            ident, _, number = item.partition("=")
            if not number.isdigit():
                parser.error(f"--resolve expects AI-N=<number>, got {item!r}")
            resolve(args.mapping, ident, int(number))
        return
    if args.export is None:
        parser.error("--export is required unless --resolve is used")
    export = json.loads(args.export.read_text())
    if args.link_epics:
        link_epics(export, load_numbers(args.mapping), _gh)
        return
    allow = load_allow(args.allow) if args.allow else frozenset()
    if args.apply:
        preflight_labels(export, _gh, allow)
    report = migrate(export, args.mapping, _gh, dry_run=not args.apply, pause_s=3.0, allow=allow)
    print(json.dumps(report.__dict__, indent=2))


if __name__ == "__main__":
    main()
