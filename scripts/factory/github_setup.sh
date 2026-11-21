#!/usr/bin/env bash
set -euo pipefail
# Factory GitHub setup for darth-dodo/cantastorie. Safe to rerun: every call is idempotent.
REPO="darth-dodo/cantastorie"
OWNER="darth-dodo"
BOARD="Cantastorie"

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }
label "bug" "d73a4a" "Something is broken"
label "feature" "a2eeef" "New behaviour"
label "paused" "fbca04" "A builder stopped; see the handoff comment"
label "needs-info" "d4c5f9" "Waiting on Abhishek"
label "ready-for-human" "0e8a16" "Ready for Abhishek to act"
label "epic" "5319e7" "Groups other Issues"
label "status:in-progress" "1d76db" "Being worked on"
label "priority:urgent" "b60205" "Urgent"
label "priority:high" "d93f0b" "High"
label "priority:medium" "fbca04" "Medium"
label "priority:low" "c2e0c6" "Low"

# The board: create it once, if no project titled "Cantastorie" exists.
board_number() {
  gh project list --owner "$OWNER" --limit 100 --format json \
    --jq ".projects[]|select(.title==\"$BOARD\")|.number"
}
number="$(board_number)"
if [[ -z "$number" ]]; then
  number="$(gh project create --owner "$OWNER" --title "$BOARD" --format json --jq .number)"
fi
[[ -n "$number" ]] || { echo "Cantastorie board not found" >&2; exit 1; }

# Board fields: skip any that exist. GitHub ships a built-in "Status" field, so it is
# usually already there and is left alone.
fields="$(gh project field-list "$number" --owner "$OWNER" --format json --jq '.fields[].name')"
has_field() { grep -Fxq "$1" <<<"$fields"; }

if ! has_field "Status"; then
  gh project field-create "$number" --owner "$OWNER" --name "Status" \
    --data-type SINGLE_SELECT --single-select-options "Todo,In progress,Done"
fi
if ! has_field "Estimate"; then
  gh project field-create "$number" --owner "$OWNER" --name "Estimate" --data-type NUMBER
fi

read -r -p "Protect main (require 'CI Success', no force pushes, admins included)? [y/N] " answer || answer=""
if [[ "$answer" =~ ^[yY]$ ]]; then
  gh api -X PUT "repos/$REPO/branches/main/protection" --input - <<'JSON'
{"required_status_checks":{"strict":false,"contexts":["CI Success"]},
 "enforce_admins":true,"required_pull_request_reviews":null,"restrictions":null,
 "allow_force_pushes":false,"allow_deletions":false}
JSON
fi
