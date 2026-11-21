"""Copy Linear's Cantastorie issues to GitHub Issues (spec: software factory, Part 2).

Dry run by default. Idempotent and resumable: a mapping file records, per issue, the
GitHub number and whether the comment and close steps finished. It is rewritten after
every step, so a rerun finishes whatever was left. Security-sensitive issues stay in Linear.
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
BACKOFF_S = (60.0, 120.0, 240.0)
RATE_LIMIT = re.compile(r"rate limit|403|429", re.I)
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
    needs_manual_check: list[str] = field(default_factory=list)


def _number(identifier: str) -> int:
    return int(identifier.split("-")[1])


def security_term(issue: dict[str, Any]) -> str | None:
    """The first security term found in the title, description, comments or labels."""
    texts = [issue.get("title", ""), issue.get("description") or ""]
    texts += [c.get("body", "") for c in issue.get("comments", [])]
    for text in texts:
        match = SECURITY.search(text)
        if match:
            return match.group(0)
    for label in issue.get("labels", []):
        match = SECURITY.search(label) or SECURITY_LABEL.search(label)
        if match:
            return f"label: {label}"
    return None


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
        text = f"**{c['author']}** ({c['createdAt']}):\n\n{c['body']}"
        pieces += [text[i : i + limit] for i in range(0, len(text), limit)]
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 2 + len(piece) <= limit:
            chunks[-1] += "\n\n" + piece
        else:
            chunks.append(piece)
    return chunks


def _load(mapping_path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    raw: dict[str, Any] = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    manual: list[dict[str, str]] = raw.pop("needs_manual_check", [])
    entries: dict[str, dict[str, Any]] = {}
    for ident, value in raw.items():
        base = value if isinstance(value, dict) else {"number": value}
        entries[ident] = {"commented": False, "closed": False, "comment_chunks": 0, **base}
    return entries, manual


def load_numbers(mapping_path: Path) -> dict[str, int]:
    return {ident: int(e["number"]) for ident, e in _load(mapping_path)[0].items()}


def _save(
    mapping_path: Path, entries: dict[str, dict[str, Any]], manual: list[dict[str, str]]
) -> None:
    data: dict[str, Any] = dict(entries)
    if manual:
        data["needs_manual_check"] = manual
    fd, tmp = tempfile.mkstemp(dir=mapping_path.parent, prefix=".mapping-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        Path(tmp).replace(mapping_path)  # atomic, same as os.replace
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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


def _create(
    issue: dict[str, Any],
    epics: set[str],
    entries: dict[str, dict[str, Any]],
    manual: list[dict[str, str]],
    run: Callable[[list[str]], str],
    save: Callable[[], None],
) -> None:
    ident = issue["identifier"]
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
        manual.append({"identifier": ident, "output": out})
        save()
        raise RuntimeError(f"could not read the issue number for {ident} from gh output: {out!r}")
    entries[ident] = {
        "number": int(found.group(1)),
        "commented": False,
        "closed": False,
        "comment_chunks": 0,
    }
    save()


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
) -> MigrationReport:
    entries, manual = _load(mapping_path)
    stuck = {m["identifier"] for m in manual}
    epics = {i["parent"] for i in export if i.get("parent")}
    report = MigrationReport()

    def run(args: list[str]) -> str:
        return _call(gh, args, sleep, backoff)

    def save() -> None:
        _save(mapping_path, entries, manual)

    for issue in sorted(export, key=lambda i: _number(i["identifier"])):
        ident = issue["identifier"]
        term = security_term(issue)
        if term:
            report.kept_in_linear.append(
                {"identifier": ident, "title": issue["title"], "term": term}
            )
            continue
        if ident in stuck:
            report.needs_manual_check.append(ident)
            continue
        created_now = ident not in entries
        if created_now:
            report.to_create.append({"identifier": ident, "title": issue["title"]})
        else:
            report.already_mapped.append(ident)
        if dry_run:
            continue
        if created_now:
            _create(issue, epics, entries, manual, run, save)
        _finish(issue, entries[ident], run, save)
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

    for epic, kids in children.items():
        listing = "\n".join(f"- #{mapping[k]}" for k in sorted(kids, key=_number))
        body = f"{render_body(by_id[epic], ref(epic))}\n\n## Child issues\n{listing}"
        gh(["issue", "edit", str(mapping[epic]), "--repo", REPO, "--body", body])
        for kid in kids:
            gh(
                [
                    "issue",
                    "edit",
                    str(mapping[kid]),
                    "--repo",
                    REPO,
                    "--body",
                    render_body(by_id[kid], ref(kid)),
                ]
            )


def preflight_labels(export: list[dict[str, Any]], gh: GhRunner) -> None:
    """Exit clearly if the repo lacks any label the migration is about to use."""
    epics = {i["parent"] for i in export if i.get("parent")}
    needed = {label for i in export if not is_security_sensitive(i) for label in _labels(i, epics)}
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
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise GhError((exc.stderr or "").strip() or f"gh exited with {exc.returncode}") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="create issues (default: dry run)")
    parser.add_argument("--link-epics", action="store_true", help="second pass: fill epic links")
    args = parser.parse_args()
    export = json.loads(args.export.read_text())
    if args.link_epics:
        link_epics(export, load_numbers(args.mapping), _gh)
        return
    if args.apply:
        preflight_labels(export, _gh)
    report = migrate(export, args.mapping, _gh, dry_run=not args.apply, pause_s=3.0)
    print(json.dumps(report.__dict__, indent=2))


if __name__ == "__main__":
    main()
