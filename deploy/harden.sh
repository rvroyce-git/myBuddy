#!/usr/bin/env bash
# Reproduce the MyBuddy server hardening on a fresh Oracle Always Free Ubuntu 24.04 VM.
# Idempotent: safe to run again; it only changes what differs from the wanted state.
#
#   sudo bash deploy/harden.sh --dry-run    # print what would change, change nothing
#   sudo bash deploy/harden.sh              # apply
#
# Options: --dry-run | --admin-user NAME (whose authorized_keys mybuddy gets; default
# $SUDO_USER or ubuntu) | -h/--help.
#
# What it does (see SECURITY.md "What was hardened"):
#   apt upgrade; unattended-upgrades on; timezone Asia/Kolkata; 2 GB /swapfile and
#   vm.swappiness=20; user `mybuddy` (no sudo) with the admin's SSH key; sshd drop-in
#   99-mybuddy.conf (keys only, no root, MaxAuthTries 3), validated with `sshd -t` before a
#   reload (the reload never drops the SSH session you are in); linger for mybuddy;
#   python3-venv/pip, git, rsync; fail2ban sshd jail; a read-only check of Oracle's iptables
#   INPUT chain. Every file it replaces is first copied to /var/backups/mybuddy/<timestamp>/
#   (mode 0700), never next to the original (apt and sshd would read a copy left there).
# What it never does: change iptables/nftables (Oracle's rules stay as they are; UFW is not
# used because it conflicts with them), open ports, or restart sshd.
set -euo pipefail

DRY_RUN=0
ADMIN_USER=${SUDO_USER:-ubuntu}
APP_USER=mybuddy
SWAPFILE=/swapfile
SWAP_SIZE=2G
SWAPPINESS=20
TIMEZONE=Asia/Kolkata
SSHD_DROPIN=/etc/ssh/sshd_config.d/99-mybuddy.conf
JAIL_LOCAL=/etc/fail2ban/jail.local
SYSCTL_FILE=/etc/sysctl.d/99-mybuddy.conf
AUTO_UPGRADES=/etc/apt/apt.conf.d/20auto-upgrades
BACKUP_ROOT=/var/backups/mybuddy
BACKUP_DIR=$BACKUP_ROOT/$(date +%Y%m%d%H%M%S)
PACKAGES=(unattended-upgrades python3-venv python3-pip git rsync fail2ban)

SSHD_CONTENT='# MyBuddy hardening (deploy/harden.sh): SSH keys only, no root login.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
MaxAuthTries 3
'

JAIL_CONTENT='# MyBuddy hardening (deploy/harden.sh)
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
backend = systemd

[sshd]
enabled = true
journalmatch = _COMM=sshd
'

SYSCTL_CONTENT="# MyBuddy hardening (deploy/harden.sh): use the swap file only under real pressure.
vm.swappiness = $SWAPPINESS
"

AUTO_UPGRADES_CONTENT='APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
'

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --admin-user) ADMIN_USER=${2:?--admin-user needs a name}; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

CHANGED=()
UNCHANGED=()
WARNINGS=()
changed() { CHANGED+=("$1"); }
same() { UNCHANGED+=("$1"); }
warn() { WARNINGS+=("$1"); echo "WARNING: $1" >&2; }
die() { echo "ERROR: $1" >&2; exit 1; }

# run CMD...: execute, or only print it in dry-run mode.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] %s\n' "$*"
  else
    printf '+ %s\n' "$*"
    "$@"
  fi
}

# backup_file PATH: copy PATH to $BACKUP_DIR/PATH (e.g. .../<ts>/etc/fstab), keeping its mode.
# Backups live outside /etc so that apt.conf.d, sshd_config.d etc. never read them.
backup_file() {
  local path=$1
  [ -f "$path" ] || return 0
  install -d -m 700 "$BACKUP_ROOT" "$BACKUP_DIR"
  mkdir -p "$BACKUP_DIR$(dirname "$path")"
  cp -p "$path" "$BACKUP_DIR$path"
  echo "+ backed up $path to $BACKUP_DIR$path"
}

# put_file PATH MODE CONTENT LABEL: write CONTENT when it differs. Returns 0 if (would be) changed.
put_file() {
  local path=$1 mode=$2 content=$3 label=$4
  if [ -f "$path" ] && [ "$(cat "$path")" = "$(printf '%s' "$content")" ]; then
    same "$label ($path)"
    return 1
  fi
  if [ "$DRY_RUN" = 1 ]; then
    printf '[dry-run] write %s (mode %s):\n%s\n' "$path" "$mode" "$content"
  else
    backup_file "$path"
    local tmp
    tmp=$(mktemp "$path.XXXXXX")
    printf '%s' "$content" >"$tmp"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$path"
    echo "+ wrote $path"
  fi
  changed "$label ($path)"
  return 0
}

# ---------------------------------------------------------------- preflight
[ -r /etc/os-release ] || die "cannot read /etc/os-release; this script is for Ubuntu 24.04"
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = ubuntu ] || die "refusing to run on '${ID:-unknown}': this script is for Ubuntu 24.04"
[ "${VERSION_ID:-}" = 24.04 ] || warn "tested on Ubuntu 24.04, this is ${VERSION_ID:-unknown}"
if [ "$(id -u)" -ne 0 ]; then
  [ "$DRY_RUN" = 1 ] || die "run as root: sudo bash $0 (or add --dry-run to preview)"
  warn "not root: dry-run output may be incomplete (some files are unreadable)"
fi
id "$ADMIN_USER" >/dev/null 2>&1 || die "admin user '$ADMIN_USER' does not exist (use --admin-user)"
ADMIN_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
ADMIN_KEYS=$ADMIN_HOME/.ssh/authorized_keys
# Turning password login off is only safe when key login already works for the admin.
if [ "$DRY_RUN" = 0 ] && ! grep -qE '^(ssh-|ecdsa-|sk-)' "$ADMIN_KEYS" 2>/dev/null; then
  die "$ADMIN_KEYS has no SSH public key; refusing to disable password login"
fi
echo "==> MyBuddy hardening on $(hostname) (Ubuntu ${VERSION_ID:-?}), admin user $ADMIN_USER" \
  "$([ "$DRY_RUN" = 1 ] && echo '[DRY RUN: nothing will change]')"

export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- 1. packages
echo "==> 1. Packages: upgrade and install ${PACKAGES[*]}"
run apt-get update -q
run apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
missing=()
for p in "${PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
  run apt-get install -y -q "${missing[@]}"
  changed "installed ${missing[*]}"
else
  same "packages ${PACKAGES[*]}"
fi
changed "apt upgrade (ran)"

# ---------------------------------------------------------------- 2. unattended upgrades
echo "==> 2. Unattended security upgrades"
put_file "$AUTO_UPGRADES" 644 "$AUTO_UPGRADES_CONTENT" "unattended-upgrades schedule" || true
if systemctl is-enabled --quiet unattended-upgrades 2>/dev/null; then
  same "unattended-upgrades service enabled"
else
  run systemctl enable --now unattended-upgrades
  changed "unattended-upgrades service enabled"
fi

# ---------------------------------------------------------------- 3. timezone
echo "==> 3. Timezone $TIMEZONE"
if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" = "$TIMEZONE" ]; then
  same "timezone $TIMEZONE"
else
  run timedatectl set-timezone "$TIMEZONE"
  changed "timezone $TIMEZONE"
fi

# ---------------------------------------------------------------- 4. swap
echo "==> 4. Swap: $SWAP_SIZE $SWAPFILE, vm.swappiness=$SWAPPINESS"
if [ -f "$SWAPFILE" ]; then
  same "$SWAPFILE exists (size left as is: $(du -h "$SWAPFILE" | cut -f1))"
else
  run fallocate -l "$SWAP_SIZE" "$SWAPFILE"
  run chmod 600 "$SWAPFILE"
  run mkswap "$SWAPFILE"
  changed "created $SWAP_SIZE $SWAPFILE"
fi
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$SWAPFILE"; then
  same "$SWAPFILE active"
else
  run swapon "$SWAPFILE"
  changed "$SWAPFILE active"
fi
if grep -qE "^${SWAPFILE}[[:space:]]" /etc/fstab; then
  same "$SWAPFILE in /etc/fstab"
else
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] append '$SWAPFILE none swap sw 0 0' to /etc/fstab"
  else
    backup_file /etc/fstab
    echo "$SWAPFILE none swap sw 0 0" >>/etc/fstab
  fi
  changed "$SWAPFILE in /etc/fstab"
fi
if put_file "$SYSCTL_FILE" 644 "$SYSCTL_CONTENT" "vm.swappiness"; then
  run sysctl -q -p "$SYSCTL_FILE"
fi

# ---------------------------------------------------------------- 5. app user
echo "==> 5. User $APP_USER (no sudo, same SSH key as $ADMIN_USER, linger)"
if id "$APP_USER" >/dev/null 2>&1; then
  same "user $APP_USER exists"
else
  run adduser --disabled-password --gecos "" "$APP_USER"
  changed "created user $APP_USER"
fi
for g in sudo admin; do
  if id -nG "$APP_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$g"; then
    run gpasswd -d "$APP_USER" "$g"
    changed "removed $APP_USER from group $g"
  fi
done
APP_HOME=$(getent passwd "$APP_USER" | cut -d: -f6 || true)
APP_HOME=${APP_HOME:-/home/$APP_USER}
APP_KEYS=$APP_HOME/.ssh/authorized_keys
added_key=0
if [ -r "$ADMIN_KEYS" ]; then
  while IFS= read -r key; do
    case "$key" in ssh-* | ecdsa-* | sk-*) ;; *) continue ;; esac
    if ! grep -qxF "$key" "$APP_KEYS" 2>/dev/null; then
      added_key=1
      [ "$DRY_RUN" = 1 ] && continue
      install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
      printf '%s\n' "$key" >>"$APP_KEYS"
    fi
  done <"$ADMIN_KEYS"
fi
if [ "$added_key" = 1 ]; then
  [ "$DRY_RUN" = 1 ] && echo "[dry-run] copy $ADMIN_USER's SSH public keys to $APP_KEYS"
  if [ "$DRY_RUN" = 0 ]; then
    chown "$APP_USER:$APP_USER" "$APP_KEYS"
    chmod 600 "$APP_KEYS"
  fi
  changed "SSH key for $APP_USER"
else
  same "SSH key for $APP_USER"
fi
if [ -f "/var/lib/systemd/linger/$APP_USER" ]; then
  same "linger for $APP_USER"
else
  run loginctl enable-linger "$APP_USER"
  changed "linger for $APP_USER"
fi

# ---------------------------------------------------------------- 6. sshd
echo "==> 6. SSH server: $SSHD_DROPIN"
grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config ||
  warn "/etc/ssh/sshd_config does not include sshd_config.d/*.conf; the drop-in has no effect"
backup=""
[ -f "$SSHD_DROPIN" ] && backup=$(mktemp) && cp -p "$SSHD_DROPIN" "$backup"
if put_file "$SSHD_DROPIN" 644 "$SSHD_CONTENT" "sshd drop-in"; then
  if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] sshd -t   (validate), then systemctl try-reload-or-restart ssh"
  elif install -d -m 755 /run/sshd && sshd -t; then
    # reload = re-read config; existing sessions (including this one) stay connected.
    run systemctl try-reload-or-restart ssh
  else
    echo "sshd -t rejected the new config; restoring the previous state" >&2
    if [ -n "$backup" ]; then cp -p "$backup" "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
    die "sshd config invalid; nothing reloaded, your SSH session is unaffected"
  fi
fi
[ -n "$backup" ] && rm -f "$backup"
# First match wins in sshd: an earlier drop-in (e.g. 50-cloud-init.conf) could override ours.
if [ "$DRY_RUN" = 0 ] && command -v sshd >/dev/null; then
  effective=$(sshd -T 2>/dev/null || true)
  for want in "passwordauthentication no" "kbdinteractiveauthentication no" \
    "permitrootlogin no" "pubkeyauthentication yes" "maxauthtries 3"; do
    grep -qx "$want" <<<"$effective" || warn "effective sshd setting is not '$want' (check other files in /etc/ssh/sshd_config.d/)"
  done
fi

# ---------------------------------------------------------------- 7. fail2ban
echo "==> 7. fail2ban sshd jail: $JAIL_LOCAL"
if put_file "$JAIL_LOCAL" 644 "$JAIL_CONTENT" "fail2ban jail.local"; then
  if [ "$DRY_RUN" = 0 ]; then
    fail2ban-client -t >/dev/null || die "fail2ban rejected $JAIL_LOCAL (previous copy saved under $BACKUP_DIR)"
  fi
  run systemctl enable fail2ban
  run systemctl restart fail2ban
elif systemctl is-active --quiet fail2ban 2>/dev/null; then
  same "fail2ban running"
else
  run systemctl enable --now fail2ban
  changed "fail2ban running"
fi

# ---------------------------------------------------------------- 8. firewall (read-only check)
# Oracle's Ubuntu image ships an iptables INPUT chain that accepts SSH (tcp 22) and ends in a
# catch-all REJECT. This only reads the chain (`iptables -S` lists rules) and warns when it
# looks different; it never adds, removes or reorders a rule.
echo "==> 8. Firewall: Oracle's iptables rules are checked, never changed"
if ! command -v iptables >/dev/null; then
  warn "iptables not found; cannot check the INPUT chain"
elif ! input_rules=$(iptables -S INPUT 2>/dev/null); then
  warn "could not read the iptables INPUT chain (run as root to check it)"
else
  appended=$(grep -E '^-A INPUT ' <<<"$input_rules" || true)
  if grep -E -- '--dport 22( |$)' <<<"$appended" | grep -q -- '-j ACCEPT'; then
    same "iptables INPUT accepts tcp 22"
  else
    warn "iptables INPUT has no ACCEPT rule for port 22; SSH may be blocked after a reboot"
  fi
  last_rule=$(tail -n 1 <<<"$appended")
  if grep -q -- '-j REJECT' <<<"$last_rule"; then
    same "iptables INPUT ends with REJECT"
  else
    warn "iptables INPUT does not end with a REJECT rule (last: '${last_rule:-none}'); other ports may be open"
  fi
  other_ports=$(grep -- '-j ACCEPT' <<<"$appended" | grep -oE -- '--dport [0-9:]+' | grep -vx -- '--dport 22' || true)
  [ -z "$other_ports" ] || warn "iptables INPUT also accepts: $(tr '\n' ' ' <<<"$other_ports")(MyBuddy needs none)"
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
  warn "UFW is active; it conflicts with Oracle's iptables rules. Not changing it: decide yourself"
fi

# ---------------------------------------------------------------- summary
echo
echo "==> Summary $([ "$DRY_RUN" = 1 ] && echo '(dry run: would change)')"
for c in "${CHANGED[@]+"${CHANGED[@]}"}"; do echo "  changed:   $c"; done
for u in "${UNCHANGED[@]+"${UNCHANGED[@]}"}"; do echo "  unchanged: $u"; done
for w in "${WARNINGS[@]+"${WARNINGS[@]}"}"; do echo "  WARNING:   $w"; done
echo "Next: from your machine, check 'ssh mybuddy@<vm-ip> true' works BEFORE closing this session."
