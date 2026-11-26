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


def test_publish_reports_keeps_dispatch_token_off_the_command_line() -> None:
    text = _text("publish-reports.sh")
    assert '-H "Authorization: Bearer $token"' not in text
    assert "-H @-" in text and '<<<"Authorization: Bearer $token"' in text


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


def test_build_pages_runs_without_data_sha(tmp_path) -> None:
    """publish-data.yml (data branch) calls build-pages.sh without DATA_SHA/SITE_TREE; under
    `set -u` that must not abort (it did on bash 5, so no report was published)."""
    import os

    if shutil.which("git") is None:
        pytest.skip("git not installed")
    script = DEPLOY.parent / ".github" / "scripts" / "build-pages.sh"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    (data / "reports.json").write_text('{"topics": []}', encoding="utf-8")
    git = ["git", "-C", str(data), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(data)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "data"], check=True)
    data_sha = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f'[url "{remote}"]\n\tinsteadOf = https://x-access-token:T@github.com/o/r.git\n',
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in ("DATA_SHA", "SITE_TREE")}
    env.update(
        GITHUB_TOKEN="T",
        GITHUB_REPOSITORY="o/r",
        GITHUB_SHA="abcdef0123",
        GIT_CONFIG_GLOBAL=str(gitconfig),
    )
    subprocess.run(
        ["bash", str(script), str(site), str(data / "reports.json")], env=env, check=True
    )
    shown = subprocess.run(
        ["git", "-C", str(remote), "show", "gh-pages:.data-sha"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shown.stdout.strip() == data_sha


# ------------------------------------------------- harden.sh: idempotency on effective settings


def _harden_helpers() -> str:
    text = _text("harden.sh")
    head = text[: text.index("usage()")]
    start = text.index(
        "# ---------------------------------------------------------------- effective"
    )
    end = text.index("# ---------------------------------------------------------------- preflight")
    return head.replace("set -euo pipefail", "set -uo pipefail") + text[start:end]


def _run_helper(
    tmp_path: Path, body: str, bins: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    for name, out in bins.items():
        (tmp_path / f"{name}.out").write_text(out, encoding="utf-8")
        exe = fake / name
        exe.write_text(f'#!/bin/sh\ncat "{tmp_path}/{name}.out"\n', encoding="utf-8")
        exe.chmod(0o755)
    script = tmp_path / "h.sh"
    script.write_text(_harden_helpers() + body, encoding="utf-8")
    return subprocess.run(
        [BASH, str(script)],
        capture_output=True,
        text=True,
        env={"PATH": f"{fake}:/usr/bin:/bin"},
    )


SSHD_T_OK = (
    "port 22\npermitrootlogin no\npubkeyauthentication yes\npasswordauthentication no\n"
    "kbdinteractiveauthentication no\nmaxauthtries 3\n"
)


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize(
    ("sshd_t", "ok"), [(SSHD_T_OK, True), (SSHD_T_OK.replace("3", "6"), False)]
)
def test_harden_sshd_effective(tmp_path: Path, sshd_t: str, ok: bool) -> None:
    r = _run_helper(tmp_path, "sshd_effective_ok && echo OK || echo DIFF\n", {"sshd": sshd_t})
    assert r.stdout.strip() == ("OK" if ok else "DIFF"), r.stderr


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_harden_jail_compares_settings_not_bytes(tmp_path: Path) -> None:
    reordered = (
        "# hardened by hand earlier\n[sshd]\njournalmatch=_COMM=sshd\nenabled = true\n\n"
        "[DEFAULT]\nbackend = systemd\nmaxretry = 5\nfindtime = 10m\nbantime = 1h\n"
    )
    jail = tmp_path / "jail.local"
    jail.write_text(reordered, encoding="utf-8")
    body = f'JAIL_LOCAL="{jail}"\njail_effective_ok && echo OK || echo DIFF\n'
    assert _run_helper(tmp_path, body, {}).stdout.strip() == "OK"
    jail.write_text(reordered.replace("maxretry = 5", "maxretry = 9"), encoding="utf-8")
    assert _run_helper(tmp_path, body, {}).stdout.strip() == "DIFF"


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize(
    ("live", "files", "ok"),
    [
        ("20", {"90-mybuddy.conf": "# old\nvm.swappiness=20\n"}, True),
        ("60", {"90-mybuddy.conf": "vm.swappiness = 20\n"}, False),
        ("20", {"10-other.conf": "vm.swappiness = 20\n"}, False),
        ("20", {"99-mybuddy.conf": "vm.swappiness = 10\n"}, False),
    ],
)
def test_harden_swappiness_existing_file(
    tmp_path: Path, live: str, files: dict[str, str], ok: bool
) -> None:
    d = tmp_path / "sysctl.d"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    body = f'SYSCTL_DIR="{d}"\nswappiness_ok && echo OK || echo DIFF\n'
    r = _run_helper(tmp_path, body, {"sysctl": live + "\n"})
    assert r.stdout.strip() == ("OK" if ok else "DIFF"), r.stderr


def test_harden_skips_writes_when_effective_settings_match() -> None:
    text = _text("harden.sh")
    assert text.index("if sshd_effective_ok") < text.index('put_file "$SSHD_DROPIN"')
    assert text.index("if jail_effective_ok") < text.index('put_file "$JAIL_LOCAL"')
    assert text.index("if swappiness_ok") < text.index('put_file "$SYSCTL_FILE"')
    assert "apt upgrade (would run)" in text


# ------------------------------------------------- create-vm / budget-alert arguments


def _run_script(name: str, *args: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    return subprocess.run(
        [BASH, str(DEPLOY / name), *args], capture_output=True, text=True, env=env
    )


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_create_vm_reports_all_missing_args(tmp_path: Path) -> None:
    r = _run_script("create-vm.sh", tmp_path=tmp_path)
    assert r.returncode == 1
    for opt in ("--compartment", "--ad", "--subnet", "--ssh-key"):
        assert opt in r.stderr
    r = _run_script("create-vm.sh", "--dry-run", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<compartment-ocid>" in r.stdout and "<subnet-ocid>" in r.stdout
    assert "Dry run: nothing was created" in r.stdout
    placeholders = ["--compartment", "<compartment-ocid>", "--ad", "x"]
    placeholders += ["--subnet", "<subnet-ocid>", "--ssh-key", "<k.pub>"]
    r = _run_script("create-vm.sh", *placeholders, tmp_path=tmp_path)
    assert r.returncode == 1 and "only allowed with --dry-run" in r.stderr


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_budget_alert_reports_all_missing_args(tmp_path: Path) -> None:
    r = _run_script("budget-alert.sh", tmp_path=tmp_path)
    assert r.returncode == 1 and "--email" in r.stderr and "--tenancy" in r.stderr
    r = _run_script("budget-alert.sh", "--dry-run", tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "<tenancy-ocid>" in r.stdout and "<email>" in r.stdout
