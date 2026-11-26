#!/usr/bin/env bash
# Create the MyBuddy VM on Oracle Cloud with the OCI CLI, Always Free shapes only.
# Run it on YOUR machine after `oci setup config` (the owner's own API key); it never
# stores credentials.
#
#   bash deploy/create-vm.sh --dry-run \
#     --compartment ocid1.compartment.oc1..xxx --ad "XXXX:AP-HYDERABAD-1-AD-1" \
#     --subnet ocid1.subnet.oc1.ap-hyderabad-1.xxx --ssh-key ~/.ssh/mybuddy.pub
#
# Options (each also settable by the env var in brackets):
#   --compartment OCID   compartment for the VM            [OCI_COMPARTMENT_ID]
#   --ad NAME            availability domain               [OCI_AD]
#   --subnet OCID        public subnet                     [OCI_SUBNET_ID]
#   --ssh-key PATH       SSH PUBLIC key file (.pub)        [MYBUDDY_SSH_PUBKEY]
#   --name NAME          display name (default mybuddy)
#   --boot-gb N          boot volume, 47..50 GB (default 50)
#   --a1                 use VM.Standard.A1.Flex (Arm) instead of VM.Standard.E2.1.Micro
#   --ocpus N --memory-gb N   A1 only: at most 1 OCPU and 6 GB (defaults 1 and 6)
#   --dry-run            print the oci commands, create nothing
# Ubuntu 24.04 is looked up for the chosen shape; a public IP is assigned and printed.
set -euo pipefail

COMPARTMENT=${OCI_COMPARTMENT_ID:-}
AD=${OCI_AD:-}
SUBNET=${OCI_SUBNET_ID:-}
SSH_KEY=${MYBUDDY_SSH_PUBKEY:-}
NAME=mybuddy
BOOT_GB=50
USE_A1=0
OCPUS=1
MEMORY_GB=6
DRY_RUN=0

# The only shapes this script will ever launch (Oracle Always Free).
MICRO_SHAPE=VM.Standard.E2.1.Micro
A1_SHAPE=VM.Standard.A1.Flex
A1_MAX_OCPUS=1
A1_MAX_MEMORY_GB=6
BOOT_MIN_GB=47
BOOT_MAX_GB=50

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $1" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --compartment) COMPARTMENT=${2:?}; shift ;;
    --ad) AD=${2:?}; shift ;;
    --subnet) SUBNET=${2:?}; shift ;;
    --ssh-key) SSH_KEY=${2:?}; shift ;;
    --name) NAME=${2:?}; shift ;;
    --boot-gb) BOOT_GB=${2:?}; shift ;;
    --a1) USE_A1=1 ;;
    --ocpus) OCPUS=${2:?}; shift ;;
    --memory-gb) MEMORY_GB=${2:?}; shift ;;
    --shape) die "--shape is not accepted: the shape is $MICRO_SHAPE, or $A1_SHAPE with --a1" ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ---------------------------------------------------------------- validate (Always Free only)
# is_placeholder VALUE: "<something>" (allowed only with --dry-run, to print the plan).
is_placeholder() { [[ "$1" =~ ^\<[^\<\>]+\>$ ]]; }
missing=()
[ -n "$COMPARTMENT" ] || missing+=("--compartment OCID [OCI_COMPARTMENT_ID]")
[ -n "$AD" ] || missing+=("--ad NAME [OCI_AD] (oci iam availability-domain list)")
[ -n "$SUBNET" ] || missing+=("--subnet OCID [OCI_SUBNET_ID] (a public subnet of your VCN)")
[ -n "$SSH_KEY" ] || missing+=("--ssh-key PATH [MYBUDDY_SSH_PUBKEY] (your .pub file)")
if [ "${#missing[@]}" -gt 0 ]; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "NOTE: dry run with placeholders for: ${missing[*]%% *}" >&2
    COMPARTMENT=${COMPARTMENT:-<compartment-ocid>}
    AD=${AD:-<availability-domain>}
    SUBNET=${SUBNET:-<subnet-ocid>}
    SSH_KEY=${SSH_KEY:-<ssh-public-key.pub>}
  else
    { echo "ERROR: missing required arguments:"; printf '  %s\n' "${missing[@]}"; } >&2
    exit 1
  fi
fi
for v in "$COMPARTMENT" "$AD" "$SUBNET" "$SSH_KEY"; do
  if is_placeholder "$v" && [ "$DRY_RUN" = 0 ]; then die "placeholder $v is only allowed with --dry-run"; fi
done
is_placeholder "$COMPARTMENT" || [[ "$COMPARTMENT" == ocid1.compartment.* || "$COMPARTMENT" == ocid1.tenancy.* ]] ||
  die "--compartment must be a compartment or tenancy OCID"
is_placeholder "$SUBNET" || [[ "$SUBNET" == ocid1.subnet.* ]] || die "--subnet must be a subnet OCID"
[[ "$NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "--name: letters, digits, . _ - only"
if ! is_placeholder "$SSH_KEY"; then
  [ -r "$SSH_KEY" ] || die "cannot read SSH key file $SSH_KEY"
  grep -q 'PRIVATE KEY' "$SSH_KEY" && die "$SSH_KEY is a PRIVATE key; pass the .pub file"
  grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+) ' "$SSH_KEY" ||
    die "$SSH_KEY does not look like an SSH public key"
fi
[[ "$BOOT_GB" =~ ^[0-9]+$ ]] && [ "$BOOT_GB" -ge "$BOOT_MIN_GB" ] && [ "$BOOT_GB" -le "$BOOT_MAX_GB" ] ||
  die "--boot-gb must be $BOOT_MIN_GB..$BOOT_MAX_GB (Always Free: 200 GB of block storage in total)"

SHAPE_ARGS=()
if [ "$USE_A1" = 1 ]; then
  SHAPE=$A1_SHAPE
  [[ "$OCPUS" =~ ^[0-9]+$ ]] && [ "$OCPUS" -ge 1 ] && [ "$OCPUS" -le "$A1_MAX_OCPUS" ] ||
    die "--ocpus must be 1..$A1_MAX_OCPUS for $A1_SHAPE"
  [[ "$MEMORY_GB" =~ ^[0-9]+$ ]] && [ "$MEMORY_GB" -ge 1 ] && [ "$MEMORY_GB" -le "$A1_MAX_MEMORY_GB" ] ||
    die "--memory-gb must be 1..$A1_MAX_MEMORY_GB for $A1_SHAPE"
  SHAPE_ARGS=(--shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEMORY_GB}")
  echo "NOTE: $A1_SHAPE is Arm (aarch64); free A1 capacity is often unavailable." >&2
else
  SHAPE=$MICRO_SHAPE
  { [ "$OCPUS" = 1 ] && [ "$MEMORY_GB" = 6 ]; } || die "--ocpus/--memory-gb need --a1"
fi
case "$SHAPE" in
  "$MICRO_SHAPE" | "$A1_SHAPE") ;;
  *) die "refusing shape $SHAPE: not an Always Free shape" ;;
esac

if [ "$DRY_RUN" = 0 ]; then
  command -v oci >/dev/null || die "oci CLI not found: https://docs.oracle.com/iaas/Content/API/SDKDocs/cliinstall.htm"
  [ -f "${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}" ] || die "run 'oci setup config' first"
fi

# show ARGS...: print an oci command, single-quoting arguments that need it.
show() {
  local out="[dry-run] oci" a q="'"
  for a in "$@"; do
    if [[ "$a" =~ ^[A-Za-z0-9._:/=@,+-]+$ ]]; then out+=" $a"; else out+=" '${a//$q/$q\\$q$q}'"; fi
  done
  printf '%s\n' "$out"
}

echo "==> Shape $SHAPE, Ubuntu 24.04, boot volume ${BOOT_GB} GB, name $NAME"

# ---------------------------------------------------------------- refuse duplicates
# shellcheck disable=SC2016 # backticks are a JMESPath literal, not a command
NOT_TERMINATED='data[?"lifecycle-state"!=`TERMINATED`].id | [0]'
EXISTING_ARGS=(compute instance list --compartment-id "$COMPARTMENT" --display-name "$NAME"
  --query "$NOT_TERMINATED" --raw-output)
if [ "$DRY_RUN" = 1 ]; then
  show "${EXISTING_ARGS[@]}"
else
  existing=$(oci "${EXISTING_ARGS[@]}")
  if [ -n "$existing" ] && [ "$existing" != null ]; then
    die "an instance named $NAME already exists: $existing (use --name or reuse it)"
  fi
fi

# ---------------------------------------------------------------- image lookup
IMAGE_ARGS=(compute image list --compartment-id "$COMPARTMENT"
  --operating-system "Canonical Ubuntu" --operating-system-version "24.04"
  --shape "$SHAPE" --sort-by TIMECREATED --sort-order DESC --all
  --query "data[?!contains(\"display-name\", 'Minimal')] | [0].id" --raw-output)
if [ "$DRY_RUN" = 1 ]; then
  show "${IMAGE_ARGS[@]}"
  IMAGE_ID="<ubuntu-24.04-image-ocid>"
else
  IMAGE_ID=$(oci "${IMAGE_ARGS[@]}")
  [[ "$IMAGE_ID" == ocid1.image.* ]] || die "no Ubuntu 24.04 image found for $SHAPE"
  echo "    image $IMAGE_ID"
fi

# ---------------------------------------------------------------- launch
LAUNCH_ARGS=(compute instance launch --compartment-id "$COMPARTMENT" --availability-domain "$AD"
  --display-name "$NAME" --shape "$SHAPE" "${SHAPE_ARGS[@]+"${SHAPE_ARGS[@]}"}"
  --image-id "$IMAGE_ID" --boot-volume-size-in-gbs "$BOOT_GB"
  --subnet-id "$SUBNET" --assign-public-ip true
  --ssh-authorized-keys-file "$SSH_KEY"
  --wait-for-state RUNNING --max-wait-seconds 900
  --query 'data.id' --raw-output)
if [ "$DRY_RUN" = 1 ]; then
  show "${LAUNCH_ARGS[@]}"
  INSTANCE_ID="<instance-ocid>"
else
  echo "==> Launching (waits until RUNNING; 'Out of host capacity' = try another AD or later)"
  INSTANCE_ID=$(oci "${LAUNCH_ARGS[@]}" | grep -E '^ocid1\.instance\.' | tail -n 1)
  [ -n "$INSTANCE_ID" ] || die "launch did not return an instance id"
  echo "    instance $INSTANCE_ID"
fi

# ---------------------------------------------------------------- public IP
IP_ARGS=(compute instance list-vnics --instance-id "$INSTANCE_ID"
  --query 'data[0]."public-ip"' --raw-output)
if [ "$DRY_RUN" = 1 ]; then
  show "${IP_ARGS[@]}"
  echo "==> Dry run: nothing was created."
else
  IP=$(oci "${IP_ARGS[@]}")
  echo "==> VM is running. Public IP: $IP"
  echo "Next: scp deploy/harden.sh ubuntu@$IP: && ssh ubuntu@$IP 'sudo bash harden.sh'"
fi
