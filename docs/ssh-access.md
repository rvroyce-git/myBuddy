# SSH access: limiting port 22 to known addresses

Today the Oracle Security List allows TCP 22 from `0.0.0.0/0`, because the owner's mobile
network (Jio, carrier-grade NAT) gives a different public IP from a large shared pool every time.
A `/32` rule for "my IP" would lock the owner out within hours. The server is still protected by
keys-only SSH, no root login, `MaxAuthTries 3` and fail2ban (see [SECURITY.md](../SECURITY.md)),
but the goal is to take port 22 off the internet entirely. Three options:

| Option | Port 22 on the internet | Third party | Cost | Best for |
| --- | --- | --- | --- | --- |
| A. OCI Bastion | closed | none (Oracle only) | free | "Oracle only, no third party" |
| B. Tailscale | closed | Tailscale (coordination only) | free Personal plan | everyday convenience |
| C. Stable IP `/32` | open to listed IPs | none | free | an office/home line with a fixed IP |

**Recommendation:** A if you want nothing outside Oracle; B if you SSH often and want it to just
work from the phone hotspot. C only when you really have fixed addresses.

Placeholders: `<vm-ip>` public IP, `<vm-private-ip>` (Instance details > Primary VNIC, e.g.
`10.0.0.x`), `<subnet-cidr>` (Networking > Virtual cloud networks > your VCN > Subnets),
`<seclist-ocid>` (the subnet's Security List), `<subnet-ocid>`, `<compartment-ocid>`.

## Before you change anything (all options)

1. **Keep one SSH session open** the whole time, and test a *new* connection before closing it.
2. **The firewall rule lives in the web console, not on the VM.** Whatever goes wrong with SSH,
   you can always sign in to cloud.oracle.com and re-add the rule (see *Rollback*). Nothing in
   these steps can lock you out of the console.
3. **Optional serial-console fallback.** The VM's users have no passwords, so the serial console
   (Instance > *Console connection* > *Launch Cloud Shell connection*) shows a login prompt you
   cannot pass. To make it usable, set a password for the admin user once:
   `sudo passwd ubuntu`. sshd still refuses passwords (`PasswordAuthentication no`), so this only
   works on Oracle's console, which itself needs your Oracle login.
4. **Cloud Shell** (the `>_` icon in the console) is a free browser terminal with the `oci` CLI
   already signed in. You can run every `oci` command below from it, even without your laptop.

## Option A: OCI Bastion service (Oracle only)

The Bastion service is a managed jump host inside your VCN. You create short-lived *sessions*
(30 min to 3 h); each one is tied to your SSH public key and your Oracle login. The VM's port 22
then only needs to accept traffic from inside the VCN.

### Console

1. *Identity & Security > Bastion > Create bastion*. Name `mybuddybastion` (letters and digits),
   target VCN and subnet = the VM's. *CIDR block allowlist*: `0.0.0.0/0` (CGNAT again; the session
   itself needs your Oracle login and private key) or your operator's ranges if you know them.
   Max session TTL: 3 hours (10800 s).
2. Note the bastion's *Private endpoint IP address* (in the bastion details).
3. *Bastion > Create session*: type **SSH port forwarding session** (works without any agent on
   the VM), target the instance or its private IP `<vm-private-ip>`, port 22, paste your SSH
   public key (`~/.ssh/mybuddy.pub`), TTL up to 3 h.
4. Session menu > *Copy SSH command*. It looks like:

   ```bash
   ssh -i ~/.ssh/mybuddy -N -L 2222:<vm-private-ip>:22 -p 22 \
     ocid1.bastionsession.oc1.ap-hyderabad-1.xxxx@host.bastion.ap-hyderabad-1.oci.oraclecloud.com
   ```

   Leave it running, then in a second terminal: `ssh -i ~/.ssh/mybuddy -p 2222 ubuntu@127.0.0.1`.
5. **Only after step 4 works**, restrict port 22 to the VCN (keep your old session open):

   ```bash
   bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> --cidr <subnet-cidr> --dry-run
   bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> --cidr <subnet-cidr>
   ```

   (Tighter: `--cidr <bastion-private-ip>/32`.) Console equivalent: Security List > Ingress
   rules > edit the TCP 22 rule, *Source CIDR* = `<subnet-cidr>`.

A *managed SSH session* (no `-L` tunnel) also exists, but needs the **Bastion** plugin enabled
on the instance (Instance > *Oracle Cloud Agent* tab); port forwarding avoids that dependency.

### CLI

```bash
oci bastion bastion create --bastion-type standard --compartment-id <compartment-ocid> \
  --target-subnet-id <subnet-ocid> --client-cidr-list '["0.0.0.0/0"]' \
  --max-session-ttl 10800 --name mybuddybastion
oci bastion session create-port-forwarding --bastion-id <bastion-ocid> \
  --ssh-public-key-file ~/.ssh/mybuddy.pub --target-private-ip <vm-private-ip> \
  --target-port 22 --session-ttl 10800 --display-name maint --wait-for-state SUCCEEDED
oci bastion session get --session-id <session-ocid> --query 'data."ssh-metadata".command' --raw-output
```

### Using deploy scripts through the tunnel

Add to `~/.ssh/config` on your machine:

```text
Host mybuddy-vm
  HostName 127.0.0.1
  Port 2222
  HostKeyAlias mybuddy-vm
  IdentityFile ~/.ssh/mybuddy
```

With the tunnel open: `MYBUDDY_HOST=mybuddy-vm bash deploy/deploy.sh` (and `set-secrets.sh`).
A session ends after its TTL; create a new one for the next maintenance window.

## Option B: Tailscale (convenient, free Personal plan)

Tailscale builds an encrypted WireGuard network between your devices. The VM connects *out* to
Tailscale, so no inbound port is needed. Tailscale's servers coordinate keys; your traffic is
end-to-end encrypted between your devices. tailscaled uses a few tens of MB of RAM.

1. On the VM (from your open SSH session):

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --ssh          # prints a login URL: open it and approve the machine
   tailscale ip -4                  # the VM's 100.x.y.z address
   ```

2. Install Tailscale on your laptop/phone and sign in with the same account.
3. In the Tailscale admin console > Machines > the VM > **Disable key expiry** (otherwise the
   node drops off after 180 days and you would need the fallback to re-authenticate it).
4. Test from your laptop: `ssh ubuntu@<vm-tailnet-name>` (Tailscale SSH: access is decided by
   your tailnet policy; the default policy lets you into your own machines after a browser check).
   Plain `ssh -i ~/.ssh/mybuddy ubuntu@<100.x.y.z>` also works through the tunnel.
5. **Only after step 4 works**, close port 22 to the internet:

   ```bash
   bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> --close --dry-run
   bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> --close
   ```

6. Deploy with `MYBUDDY_HOST=<100.x.y.z> bash deploy/deploy.sh`.

Note: with `--ssh`, logins over the tailnet are authorised by your Tailscale identity rather than
by sshd and fail2ban, so protect the Tailscale account (2FA on the identity provider). Leave out
`--ssh` if you prefer sshd's key check over the tunnel. Uninstall: `sudo tailscale down &&
sudo apt-get remove tailscale` (re-open port 22 first).

## Option C: fixed IPs (`/32` rules)

Only if you have addresses that do not change (office line, home fibre with a static IP).
Find yours with `curl -s https://ifconfig.me` from that network, then:

```bash
bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> \
  --cidr 203.0.113.7/32 --cidr 198.51.100.20/32 --dry-run       # then without --dry-run
```

The script keeps every non-SSH rule, saves the current rules to
`security-list-ingress-backup-<time>.json` and prints the exact rollback command.

## Rollback (if SSH stops working)

1. **Console** (always works): Networking > Virtual cloud networks > your VCN > Security Lists >
   the list > *Add Ingress Rules*: Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port
   22. Takes effect within seconds.
2. **CLI / Cloud Shell**: run the rollback command the script printed, or
   `bash deploy/oci-security-list-ssh.sh --security-list <seclist-ocid> --cidr 0.0.0.0/0 --allow-anywhere`.
3. **Inside the VM broken** (sshd config, fail2ban): use a Bastion session, the tailnet, or the
   serial console (fallback 3 above). fail2ban bans last 1 hour; see
   [troubleshooting](troubleshooting.md#ssh-fail2ban-banned-me).
