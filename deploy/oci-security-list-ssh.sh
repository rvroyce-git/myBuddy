#!/usr/bin/env bash
# Set which source addresses may reach SSH (TCP 22) in an Oracle Cloud Security List.
# Every other ingress rule is kept exactly as it is. Run on YOUR machine with a configured
# `oci` CLI. See docs/ssh-access.md before using it: a wrong CIDR locks SSH out (the
# console can always undo it: Networking > VCN > Security Lists).
#
#   bash deploy/oci-security-list-ssh.sh --security-list ocid1.securitylist... \
#     --cidr 203.0.113.7/32 --cidr 198.51.100.0/24 --dry-run
#   ... --cidr 10.0.0.0/24        # only from inside the VCN (Bastion service, see docs)
#   ... --close                   # no SSH rule at all (e.g. Tailscale only)
#   ... --cidr 0.0.0.0/0 --allow-anywhere   # back to "open to the internet"
#
# Options: --security-list OCID [env OCI_SECURITY_LIST_ID], --cidr CIDR (repeatable or
# comma-separated), --close, --allow-anywhere, --backup-dir DIR (default: current dir),
# --dry-run (read the current rules, print the new ones and the command, change nothing).
# Before any change the current rules are saved to a JSON file and the exact rollback
# command is printed.
set -euo pipefail

SECLIST=${OCI_SECURITY_LIST_ID:-}
CIDRS=()
CLOSE=0
ANYWHERE=0
DRY_RUN=0
BACKUP_DIR=$PWD

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $1" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --security-list) SECLIST=${2:?}; shift ;;
    --cidr) IFS=',' read -ra more <<<"${2:?}"; CIDRS+=("${more[@]}"); shift ;;
    --close) CLOSE=1 ;;
    --allow-anywhere) ANYWHERE=1 ;;
    --backup-dir) BACKUP_DIR=${2:?}; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ "$SECLIST" == ocid1.securitylist.* ]] || die "--security-list must be a security list OCID"
if [ "$CLOSE" = 1 ]; then
  [ ${#CIDRS[@]} -eq 0 ] || die "--close and --cidr cannot be combined"
else
  [ ${#CIDRS[@]} -gt 0 ] || die "give at least one --cidr (or --close to remove SSH access)"
fi
command -v python3 >/dev/null || die "python3 is required"
command -v oci >/dev/null || die "oci CLI not found (needed even for --dry-run to read the current rules)"

# Validate CIDRs; 0.0.0.0/0 and ::/0 need --allow-anywhere.
python3 - "$ANYWHERE" "${CIDRS[@]+"${CIDRS[@]}"}" <<'PY' || exit 1
import ipaddress, sys
anywhere = sys.argv[1] == "1"
for raw in sys.argv[2:]:
    try:
        net = ipaddress.ip_network(raw.strip(), strict=True)
    except ValueError as exc:
        sys.exit(f"ERROR: bad CIDR {raw!r}: {exc}")
    if net.prefixlen == 0 and not anywhere:
        sys.exit(f"ERROR: {raw} opens SSH to the whole internet; add --allow-anywhere if you mean it")
PY

echo "==> Reading ingress rules of $SECLIST"
current=$(oci network security-list get --security-list-id "$SECLIST" \
  --query 'data."ingress-security-rules"')

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf '%s' "$current" >"$work/current.json"

# Convert to the camelCase input format, drop the SSH rules, add the new ones.
python3 - "$work" "${CIDRS[@]+"${CIDRS[@]}"}" <<'PY' || exit 1
import json, os, sys

work, cidrs = sys.argv[1], [c.strip() for c in sys.argv[2:]]


def camel(obj):
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if value is None:  # omitted = not set; keeps the update input minimal
                continue
            head, *rest = key.split("-")
            out[head + "".join(p[:1].upper() + p[1:] for p in rest)] = camel(value)
        return out
    if isinstance(obj, list):
        return [camel(v) for v in obj]
    return obj


rules = camel(json.load(open(os.path.join(work, "current.json"))) or [])


def port_range(rule):
    rng = (rule.get("tcpOptions") or {}).get("destinationPortRange")
    return (rng["min"], rng["max"]) if rng else (1, 65535)


kept, removed = [], []
for rule in rules:
    proto = str(rule.get("protocol"))
    lo, hi = port_range(rule) if proto == "6" else (0, 0)
    if proto == "6" and (lo, hi) == (22, 22):
        removed.append(rule)
        continue
    if proto == "all" or (proto == "6" and lo <= 22 <= hi):
        sys.exit(
            f"ERROR: rule from {rule.get('source')} (protocol {proto}, ports {lo}-{hi}) also "
            "allows SSH; fix it in the console first, this script only manages port-22 rules"
        )
    kept.append(rule)
new = kept + [
    {
        "protocol": "6",
        "source": c,
        "sourceType": "CIDR_BLOCK",
        "isStateless": False,
        "description": "SSH (deploy/oci-security-list-ssh.sh)",
        "tcpOptions": {"destinationPortRange": {"min": 22, "max": 22}},
    }
    for c in cidrs
]
json.dump(rules, open(os.path.join(work, "backup.json"), "w"), indent=2)
json.dump(new, open(os.path.join(work, "new.json"), "w"), indent=2)
print("    SSH sources now: " + (", ".join(r.get("source", "?") for r in removed) or "none"))
print("    SSH sources new: " + (", ".join(cidrs) or "none (port 22 closed)"))
print(f"    other ingress rules kept unchanged: {len(kept)}")
PY

if [ "$DRY_RUN" = 1 ]; then
  echo "==> Dry run. New ingress rules:"
  cat "$work/new.json"
  echo
  echo "[dry-run] oci network security-list update --security-list-id $SECLIST --ingress-security-rules file://<new.json> --force"
  exit 0
fi

mkdir -p "$BACKUP_DIR"
backup="$BACKUP_DIR/security-list-ingress-backup-$(date +%Y%m%d-%H%M%S).json"
cp "$work/backup.json" "$backup"
echo "==> Saved the current rules to $backup"
echo "    Rollback: oci network security-list update --security-list-id $SECLIST --ingress-security-rules file://$backup --force"
oci network security-list update --security-list-id "$SECLIST" \
  --ingress-security-rules "file://$work/new.json" --force >/dev/null
echo "==> Updated. Test a NEW ssh connection before closing your current one."
