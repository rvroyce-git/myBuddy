#!/usr/bin/env bash
# Safety net: an Oracle Cloud budget of 1 (your account currency) on the whole tenancy, with
# an alert email as soon as ACTUAL spend reaches 1% of it. An Always Free account that was
# never upgraded cannot be billed; this only warns you if something ever starts costing
# money (for example after an accidental upgrade to Pay As You Go). See docs/costs.md.
# Run it on YOUR machine with a configured `oci` CLI. Idempotent: an existing budget with
# the same name is reused, and an existing alert rule is not duplicated.
#
#   bash deploy/budget-alert.sh --email you@example.com --dry-run
#   bash deploy/budget-alert.sh --email you@example.com [--tenancy ocid1.tenancy...]
#
# Options: --email ADDRESS (required; never stored in the repo), --tenancy OCID (default:
# `tenancy=` from the [DEFAULT] profile of ~/.oci/config), --name NAME (default
# mybuddy-safety-net), --dry-run (print the oci commands, create nothing).
set -euo pipefail

EMAIL=""
TENANCY=${OCI_TENANCY_ID:-}
NAME=mybuddy-safety-net
DRY_RUN=0
AMOUNT=1
THRESHOLD_PERCENT=1

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $1" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --email) EMAIL=${2:?}; shift ;;
    --tenancy) TENANCY=${2:?}; shift ;;
    --name) NAME=${2:?}; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

is_placeholder() { [[ "$1" =~ ^\<[^\<\>]+\>$ ]]; }
if [ -z "$TENANCY" ]; then
  cfg=${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}
  if [ -r "$cfg" ]; then
    TENANCY=$(awk -F= '/^\[/{p=($0=="[DEFAULT]")} p&&$1~/^[[:space:]]*tenancy[[:space:]]*$/{gsub(/[[:space:]]/,"",$2);print $2;exit}' "$cfg")
  fi
fi
missing=()
[ -n "$EMAIL" ] || missing+=("--email ADDRESS (where the alert goes)")
[ -n "$TENANCY" ] || missing+=("--tenancy OCID [OCI_TENANCY_ID] (Profile > Tenancy in the console, or tenancy= in ~/.oci/config)")
if [ "${#missing[@]}" -gt 0 ]; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "NOTE: dry run with placeholders for: ${missing[*]%% *}" >&2
    EMAIL=${EMAIL:-<email>}
    TENANCY=${TENANCY:-<tenancy-ocid>}
  else
    { echo "ERROR: missing required arguments:"; printf '  %s\n' "${missing[@]}"; } >&2
    exit 1
  fi
fi
for v in "$EMAIL" "$TENANCY"; do
  if is_placeholder "$v" && [ "$DRY_RUN" = 0 ]; then die "placeholder $v is only allowed with --dry-run"; fi
done
is_placeholder "$EMAIL" || [[ "$EMAIL" =~ ^[^[:space:]@,\;]+@[^[:space:]@,\;]+\.[^[:space:]@,\;]+$ ]] || die "--email does not look like one email address"
[[ "$NAME" =~ ^[A-Za-z0-9_-]{1,100}$ ]] || die "--name: letters, digits, _ and - only"
is_placeholder "$TENANCY" || [[ "$TENANCY" == ocid1.tenancy.* ]] || die "--tenancy must be a tenancy OCID (Profile > Tenancy in the console)"
if [ "$DRY_RUN" = 0 ]; then
  command -v oci >/dev/null || die "oci CLI not found"
fi

show() {
  local out="[dry-run] oci" a q="'"
  for a in "$@"; do
    if [[ "$a" =~ ^[A-Za-z0-9._:/=@,+-]+$ ]]; then out+=" $a"; else out+=" '${a//$q/$q\\$q$q}'"; fi
  done
  printf '%s\n' "$out"
}

# Budgets live in the root compartment (the tenancy) and here also target all of it.
FIND_ARGS=(budgets budget list --compartment-id "$TENANCY" --target-type COMPARTMENT --all
  --query "data[?\"display-name\"=='$NAME'].id | [0]" --raw-output)
CREATE_ARGS=(budgets budget create --compartment-id "$TENANCY"
  --display-name "$NAME" --amount "$AMOUNT" --reset-period MONTHLY
  --target-type COMPARTMENT --targets "[\"$TENANCY\"]"
  --description "MyBuddy safety net: Always Free should never cost anything"
  --query 'data.id' --raw-output)

echo "==> Budget '$NAME': $AMOUNT (account currency) per month on the whole tenancy"
if [ "$DRY_RUN" = 1 ]; then
  show "${FIND_ARGS[@]}"
  show "${CREATE_ARGS[@]}"
  BUDGET_ID="<budget-ocid>"
else
  BUDGET_ID=$(oci "${FIND_ARGS[@]}")
  if [ -n "$BUDGET_ID" ] && [ "$BUDGET_ID" != null ]; then
    echo "    exists: $BUDGET_ID"
  else
    BUDGET_ID=$(oci "${CREATE_ARGS[@]}")
    [[ "$BUDGET_ID" == ocid1.budget.* ]] || die "budget was not created"
    echo "    created: $BUDGET_ID"
  fi
fi

RULES_ARGS=(budgets alert-rule list --budget-id "$BUDGET_ID" --all
  --query "length(data[?type=='ACTUAL'])" --raw-output)
RULE_ARGS=(budgets alert-rule create --budget-id "$BUDGET_ID"
  --display-name "${NAME//-/_}_actual_1pct" --type ACTUAL
  --threshold "$THRESHOLD_PERCENT" --threshold-type PERCENTAGE
  --recipients "$EMAIL"
  --message "Oracle Cloud is charging money on the MyBuddy tenancy. Check Billing > Cost Analysis now."
  --query 'data.id' --raw-output)

echo "==> Alert: email when ACTUAL spend reaches $THRESHOLD_PERCENT% of the budget"
if [ "$DRY_RUN" = 1 ]; then
  show "${RULES_ARGS[@]}"
  show "${RULE_ARGS[@]}"
  echo "==> Dry run: nothing was created."
  exit 0
fi
count=$(oci "${RULES_ARGS[@]}")
if [ "${count:-0}" != 0 ]; then
  echo "    an ACTUAL-spend alert rule already exists; not adding another"
else
  rule=$(oci "${RULE_ARGS[@]}")
  echo "    created: $rule"
fi
echo "==> Done. Check Billing & Cost Management > Budgets in the console."
