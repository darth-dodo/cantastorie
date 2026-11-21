"""Copy Linear's Cantastorie issues to GitHub Issues (spec: software factory, Part 2).

Dry run by default. Idempotent: a mapping file records AI-NNN -> issue number as each
issue is created, and a rerun skips anything already mapped. Security-sensitive
issues stay in Linear.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

REPO = "darth-dodo/cantastorie"
PRIORITY = {1: "priority:urgent", 2: "priority:high", 3: "priority:medium", 4: "priority:low"}
PRIORITY_NAME = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
SECURITY = re.compile(
    r"token (exposure|leak)|leaked .*token|account takeover|auth(entication|orization)? bypass|"
    r"unauthenticated|secret|credential|csrf|xss|injection|exploit|vulnerab",
    re.I,
)


class GhRunner(Protocol):
    def __call__(self, args: list[str]) -> str: ...


@dataclass
class MigrationReport:
    to_create: list[str] = field(default_factory=list)
    already_mapped: list[str] = field(default_factory=list)
    kept_in_linear: list[str] = field(default_factory=list)


def _number(identifier: str) -> int:
    return int(identifier.split("-")[1])


def is_security_sensitive(issue: dict[str, Any]) -> bool:
    text = f"{issue.get('title', '')}\n{issue.get('description') or ''}"
    return bool(SECURITY.search(text))


def render_body(issue: dict[str, Any]) -> str:
    lines = [f"Migrated from Linear {issue['identifier']} ({issue['url']})", ""]
    lines.append(issue.get("description") or "(no description)")
    lines += ["", "---", f"Linear status: {issue['status']}"]
    lines.append(f"Linear priority: {PRIORITY_NAME.get(issue.get('priority') or 0, 'None')}")
    if issue.get("assignee"):
        lines.append(f"Assignee: {issue['assignee']}")
    if issue.get("parent"):
        lines.append(f"Epic: {issue['parent']}")
    lines.append(f"Created: {issue['createdAt']}")
    if issue.get("completedAt"):
        lines.append(f"Completed: {issue['completedAt']}")
    if issue.get("duplicateOf"):
        lines.append(f"Duplicate of: {issue['duplicateOf']}")
    return "\n".join(lines)


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


def _comments(issue: dict[str, Any]) -> str:
    return "\n\n".join(
        f"**{c['author']}** ({c['createdAt']}):\n\n{c['body']}" for c in issue.get("comments", [])
    )


def migrate(
    export: list[dict[str, Any]],
    mapping_path: Path,
    gh: GhRunner,
    *,
    dry_run: bool,
    pause_s: float = 0.0,
) -> MigrationReport:
    mapping: dict[str, int] = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    epics = {i["parent"] for i in export if i.get("parent")}
    report = MigrationReport()
    for issue in sorted(export, key=lambda i: _number(i["identifier"])):
        ident = issue["identifier"]
        if is_security_sensitive(issue):
            report.kept_in_linear.append(ident)
            continue
        if ident in mapping:
            report.already_mapped.append(ident)
            continue
        report.to_create.append(ident)
        if dry_run:
            continue
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
        url = gh(args).strip()
        number = int(url.rsplit("/", 1)[1])
        mapping[ident] = number
        mapping_path.write_text(
            json.dumps(mapping, indent=2, sort_keys=True)
        )  # after every issue: resumable
        if issue.get("comments"):
            gh(["issue", "comment", str(number), "--repo", REPO, "--body", _comments(issue)])
        if issue["status"] == "Done":
            gh(["issue", "close", str(number), "--repo", REPO, "--reason", "completed"])
        elif issue["status"] in ("Canceled", "Duplicate"):
            gh(["issue", "close", str(number), "--repo", REPO, "--reason", "not planned"])
        time.sleep(pause_s)  # stay well under GitHub's content-creation rate limit
    return report


def link_epics(export: list[dict[str, Any]], mapping: dict[str, int], gh: GhRunner) -> None:
    """Second pass, once every issue exists: children name their epic as #N, epics list children."""
    by_id = {i["identifier"]: i for i in export}
    children: dict[str, list[str]] = {}
    for issue in export:
        if issue.get("parent") in mapping and issue["identifier"] in mapping:
            children.setdefault(issue["parent"], []).append(issue["identifier"])
    for epic, kids in children.items():
        listing = "\n".join(f"- #{mapping[k]}" for k in sorted(kids, key=_number))
        body = f"{render_body(by_id[epic])}\n\n## Child issues\n{listing}"
        gh(["issue", "edit", str(mapping[epic]), "--repo", REPO, "--body", body])
        for kid in kids:
            kid_body = render_body(by_id[kid]).replace(f"Epic: {epic}", f"Epic: #{mapping[epic]}")
            gh(["issue", "edit", str(mapping[kid]), "--repo", REPO, "--body", kid_body])


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="create issues (default: dry run)")
    parser.add_argument("--link-epics", action="store_true", help="second pass: fill epic links")
    args = parser.parse_args()
    export = json.loads(args.export.read_text())
    if args.link_epics:
        link_epics(export, json.loads(args.mapping.read_text()), _gh)
        return
    report = migrate(export, args.mapping, _gh, dry_run=not args.apply, pause_s=3.0)
    print(json.dumps(report.__dict__, indent=2))


if __name__ == "__main__":
    main()
