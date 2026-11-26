"""Static checks for the deploy/ shell scripts. Nothing here executes a script's actions:
only `bash -n` (parse) and, when installed, shellcheck."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
SCRIPTS = sorted(DEPLOY.glob("*.sh"))
BASH = shutil.which("bash")


def _text(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_scripts_found() -> None:
    names = {p.name for p in SCRIPTS}
    assert {"harden.sh", "create-vm.sh", "oci-security-list-ssh.sh", "budget-alert.sh"} <= names


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_bash_syntax(script: Path) -> None:
    assert BASH is not None
    result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shellcheck(script: Path) -> None:
    result = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_strict_mode(script: Path) -> None:
    head = script.read_text(encoding="utf-8").splitlines()[:40]
    assert head[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in head


@pytest.mark.parametrize(
    "setting",
    [
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin no",
        "PubkeyAuthentication yes",
        "MaxAuthTries 3",
        "bantime = 1h",
        "findtime = 10m",
        "maxretry = 5",
        "backend = systemd",
        "journalmatch = _COMM=sshd",
        "SSHD_DROPIN=/etc/ssh/sshd_config.d/99-mybuddy.conf",
        "JAIL_LOCAL=/etc/fail2ban/jail.local",
        "SWAPFILE=/swapfile",
        "SWAP_SIZE=2G",
        "SWAPPINESS=20",
        "TIMEZONE=Asia/Kolkata",
        "APP_USER=mybuddy",
        "loginctl enable-linger",
        "unattended-upgrades",
        'APT::Periodic::Unattended-Upgrade "1";',
        "python3-venv",
        "python3-pip",
        "git",
        "rsync",
        "fail2ban",
    ],
)
def test_harden_has_setting(setting: str) -> None:
    assert setting in _text("harden.sh")


def test_harden_safety_rails() -> None:
    text = _text("harden.sh")
    assert "--dry-run" in text
    assert '"${ID:-}" = ubuntu' in text  # refuses non-Ubuntu
    # sshd config is validated before a reload, and sshd is never restarted outright.
    assert text.index("sshd -t") < text.index("try-reload-or-restart ssh")
    assert "systemctl restart ssh" not in text
    # Oracle's firewall rules are never touched (UFW conflicts with them).
    firewall_cmd = re.compile(
        r"^\s*(?:run\s+)?(?:iptables|ip6tables|nft|netfilter-persistent|ufw\s+(?!status))",
        re.MULTILINE,
    )
    assert firewall_cmd.search(text) is None
    assert "Summary" in text


def test_harden_firewall_check_is_read_only() -> None:
    text = _text("harden.sh")
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    calls = [ln for ln in code if re.search(r"\biptables\s+-", ln)]
    assert calls, "harden.sh should read Oracle's iptables INPUT chain"
    for ln in calls:
        # Only `iptables -S` (list rules) is allowed; never -A/-I/-D/-F/-P/-X/-N/-R.
        assert re.findall(r"\biptables\s+(-\S+)", ln) == ["-S"], ln
    assert "--dport 22" in text and "-j REJECT" in text


def _firewall_section() -> str:
    text = _text("harden.sh")
    start = text.index("# ---------------------------------------------------------------- 8.")
    end = text.index("# ---------------------------------------------------------------- summary")
    return text[start:end]


ORACLE_INPUT = """-P INPUT ACCEPT
-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT
-A INPUT -p icmp -j ACCEPT
-A INPUT -i lo -j ACCEPT
-A INPUT -p udp -m udp --sport 123 -j ACCEPT
-A INPUT -p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT
-A INPUT -j REJECT --reject-with icmp-host-prohibited
"""
_REJECT = "-A INPUT -j REJECT --reject-with icmp-host-prohibited\n"


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize(
    ("rules", "warnings"),
    [
        (ORACLE_INPUT, []),
        (ORACLE_INPUT.replace("--dport 22", "--dport 2222"), ["no ACCEPT rule for port 22", "22"]),
        (ORACLE_INPUT.replace(_REJECT, ""), ["does not end with a REJECT"]),
        (
            ORACLE_INPUT.replace(_REJECT, "-A INPUT -p tcp --dport 8080 -j ACCEPT\n" + _REJECT),
            ["also accepts: --dport 8080"],
        ),
    ],
    ids=["oracle-default", "no-ssh", "no-reject", "extra-port"],
)
def test_harden_firewall_check_warns(tmp_path: Path, rules: str, warnings: list[str]) -> None:
    assert BASH is not None
    fake = tmp_path / "bin"
    fake.mkdir()
    (tmp_path / "rules").write_text(rules, encoding="utf-8")
    iptables = fake / "iptables"
    # A fake that records its arguments and prints the rules: proves nothing else is called.
    iptables.write_text(
        f'#!/bin/sh\necho "$@" >> "{tmp_path}/calls"\ncat "{tmp_path}/rules"\n', encoding="utf-8"
    )
    iptables.chmod(0o755)
    script = tmp_path / "fw.sh"
    script.write_text(
        "set -euo pipefail\nWARNINGS=()\n"
        'warn() { WARNINGS+=("$1"); echo "WARNING: $1"; }\nsame() { echo "OK: $1"; }\n'
        + _firewall_section(),
        encoding="utf-8",
    )
    result = subprocess.run(
        [BASH, str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{fake}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text(encoding="utf-8").split() == ["-S", "INPUT"]
    found = [ln for ln in result.stdout.splitlines() if ln.startswith("WARNING")]
    assert len(found) == len(warnings), result.stdout
    for want in warnings:
        assert any(want in ln for ln in found), (want, result.stdout)


def test_harden_backups_outside_config_dirs() -> None:
    text = _text("harden.sh")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "BACKUP_ROOT=/var/backups/mybuddy" in code
    assert 'install -d -m 700 "$BACKUP_ROOT" "$BACKUP_DIR"' in code
    # apt reads every file in apt.conf.d (and sshd every *.conf): no backups next to originals.
    assert ".bak" not in code
    assert 'cp -p "$path" "$BACKUP_DIR$path"' in code


def _ssh_fingerprint(b64_key: str) -> str:
    digest = hashlib.sha256(base64.b64decode(b64_key)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def test_github_known_hosts_match_published_fingerprints() -> None:
    # As published on GitHub's "GitHub's SSH key fingerprints" docs page (see the file header).
    published = {
        "ssh-ed25519": "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
        "ecdsa-sha2-nistp256": "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
        "ssh-rsa": "SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s",
    }
    lines = [
        ln.split()
        for ln in _text("github_known_hosts").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    assert sorted(ln[1] for ln in lines) == sorted(published)
    for host, kind, key in lines:
        assert host == "github.com"
        assert _ssh_fingerprint(key) == published[kind]


def test_publish_reports_pins_github_host_keys() -> None:
    text = _text("publish-reports.sh")
    assert "accept-new" not in text
    assert "-o StrictHostKeyChecking=yes" in text
    assert "-o UserKnownHostsFile=$KNOWN_HOSTS" in text
    deploy = _text("deploy.sh")
    assert "install -m 644 deploy/github_known_hosts ~/.ssh/github_known_hosts" in deploy


WORKFLOWS = DEPLOY.parent / ".github" / "workflows"


def test_workflows_never_persist_checkout_credentials() -> None:
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in data["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    assert step.get("with", {}).get("persist-credentials") is False, wf.name


def test_pages_publishes_from_main_on_a_schedule() -> None:
    text = (WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))  # PyYAML reads the bare key `on` as True
    assert on["schedule"] == [{"cron": "*/10 * * * *"}]
    assert "workflow_dispatch" in on
    assert on["push"]["branches"] == ["main"]
    assert "origin/gh-pages:.data-sha" in text and "build=false" in text
    build = (DEPLOY.parent / ".github" / "scripts" / "build-pages.sh").read_text(encoding="utf-8")
    assert '"$out/.data-sha"' in build and '"$out/.site-tree"' in build


def test_create_vm_only_always_free_shapes() -> None:
    text = _text("create-vm.sh")
    shapes = set(re.findall(r"VM\.Standard\.[A-Za-z0-9.]+[A-Za-z0-9]", text))
    assert shapes == {"VM.Standard.E2.1.Micro", "VM.Standard.A1.Flex"}
    assert "A1_MAX_OCPUS=1" in text and "A1_MAX_MEMORY_GB=6" in text
    assert "BOOT_MAX_GB=50" in text
    assert "--assign-public-ip true" in text
    assert '--operating-system-version "24.04"' in text
    assert "--dry-run" in text


def test_budget_alert_never_hardcodes_an_email() -> None:
    text = _text("budget-alert.sh")
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert 'EMAIL=""' in code
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", code) is None
    assert "AMOUNT=1" in code and "THRESHOLD_PERCENT=1" in code
    assert "--type ACTUAL" in code and "--threshold-type PERCENTAGE" in code


def test_security_list_script_backs_up_and_guards_anywhere() -> None:
    text = _text("oci-security-list-ssh.sh")
    assert "--allow-anywhere" in text and "backup" in text and "--dry-run" in text
