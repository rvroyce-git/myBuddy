"""Static checks for the deploy/ shell scripts. Nothing here executes a script's actions:
only `bash -n` (parse) and, when installed, shellcheck."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
