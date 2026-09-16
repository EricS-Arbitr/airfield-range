#!/bin/bash
#
# deploy.sh — three-attempt Ansible runner with hybrid retry scope.
#
# Attempt 1: full site.yml against every host
# Attempt 2: --limit @retry-file (failed hosts only) if a retry file exists
# Attempt 3: full site.yml again (safety net if retry-scoped attempt didn't cover
#            a cross-host dependency)
#
# See PROJECT_LOG.md or the retry-pattern discussion in the session where
# this file was rewritten for the rationale.

PLAYBOOK="site.yml"
# Ansible names the retry file after the playbook with the EXTENSION STRIPPED
# (site.yml -> site.retry), so this has to strip it too. Built as
# "$PLAYBOOK.retry" it yields site.yml.retry, which never exists -- and the
# `[ -f "$RETRY_FILE" ]` guard below then reads that as "no retry file was
# produced" and silently falls through to a full sweep. Attempt 2 had never
# once been retry-scoped. Caught on airfield 2026-09-15.
RETRY_FILE="retry/$(basename "${PLAYBOOK%.*}").retry"
MAX_ATTEMPTS=3
# FORKS is DERIVED, not chosen: it is the largest single play target plus a
# small margin. Recomputed 2026-09-15.
#
#   largest play target : 74  (windows,linux — the `common` play)
#   FORKS               : 76
#
# Sized so the widest play runs in ONE batch. At 40 forks that 74-host play
# ran two rounds, the second only 34 wide, and those are the long plays. The
# margin is free: Ansible never spawns more workers than the play has hosts,
# so excess forks cost nothing, while being one short costs a whole extra
# round.
#
# TRADEOFF: each fork is a separate Python process, so this is a memory
# question rather than a CPU one -- workers are almost always blocked on
# WinRM/SSH I/O, not computing. UNVERIFIED ON THIS CONTROLLER: 76 forks is
# roughly 4-6 GB resident. If it swaps during a full sweep, drop this rather
# than assuming the deploy is slow for another reason.
#
# RECOUNT, do not increment, when hosts are added or removed. Adding a host
# to [windows] or [linux] moves the target this is derived from.
FORKS=76

# --- Speed knobs -------------------------------------------------------------
# Trims 5-10 minutes off a full-fleet run vs Ansible defaults.
#   ANSIBLE_PIPELINING=True     — one SSH exec per task on Linux instead of
#                                 three (open/exec/close). Safe on SimSpace
#                                 images (requiretty is off by default).
#                                 No effect on Windows/WinRM.
#   ANSIBLE_GATHERING=smart     — Gather facts once per host per run; skip
#                                 subsequent plays that also gather. Ansible
#                                 remembers what it already gathered.
#   ANSIBLE_CACHE_PLUGIN=jsonfile + fact_cache dir + 24h TTL — persist facts
#                                 across runs, so back-to-back deploys don't
#                                 re-gather on unchanged hosts.
export ANSIBLE_PIPELINING=True
export ANSIBLE_GATHERING=smart
export ANSIBLE_CACHE_PLUGIN=jsonfile
export ANSIBLE_CACHE_PLUGIN_CONNECTION="$HOME/.ansible/fact_cache"
export ANSIBLE_CACHE_PLUGIN_TIMEOUT=86400
mkdir -p "$ANSIBLE_CACHE_PLUGIN_CONNECTION"

# --- Install Galaxy collections (idempotent — skips already-installed ones) ---
# Required for the pfsensible.core collection that drives the pfSense plays
# (bs-edge-fw, bs-ops-fw). Pulled through the corp proxy because the Ansible
# VM doesn't have direct internet. Failure here doesn't abort the deploy —
# ansible-playbook will surface a clear "collection not found" error if
# anything's actually missing.
#
# NOTE: a `sleep 120` here was removed 2026-07-02 in a speed pass, reasoning
# that the retry loop already handles a VM that is not ready yet. RESTORED
# 2026-09-15 as BOOT_DELAY (see below), because that reasoning did not survive
# contact with a fresh range -- the same conclusion ss-pp-so reached on
# 2026-08-05. The retry loop does "handle" it, but only by paying for a full
# multi-hour sweep to discover the host was still booting. The legitimate half
# of the 2026-07-02 argument survives as the override: BOOT_DELAY=0.
# --- Elapsed-time accounting -------------------------------------------------
# Reported through an EXIT trap rather than at the bottom of the script, because
# the bottom is only reached on two of the three ways this ends. The third --
# someone killing a run that has stopped making progress -- is the one where
# knowing the elapsed time matters most, and it never reaches the last line.
#
# Two clocks, because they answer different questions:
#   ansible elapsed   what was asked for: first attempt start -> finish
#   pre-ansible       galaxy install + BOOT_DELAY, several minutes of wall clock
#                     that is not Ansible and should not be blamed on it
SCRIPT_START=$(date +%s)
ANSIBLE_START=""
DEPLOY_RESULT="interrupted before Ansible started"

fmt_elapsed() {
	local s=$1
	printf '%dh %02dm %02ds' $((s / 3600)) $(((s % 3600) / 60)) $((s % 60))
}

report_elapsed() {
	rc=$?
	now=$(date +%s)
	echo
	echo "================== deploy.sh timing =================="
	if [ -n "$ANSIBLE_START" ]; then
		printf '  ansible elapsed  : %s\n' "$(fmt_elapsed $((now - ANSIBLE_START)))"
		printf '  pre-ansible      : %s   (galaxy + BOOT_DELAY)\n' \
			"$(fmt_elapsed $((ANSIBLE_START - SCRIPT_START)))"
	else
		printf '  ansible elapsed  : never started\n'
	fi
	printf '  total wall clock : %s\n' "$(fmt_elapsed $((now - SCRIPT_START)))"
	printf '  outcome          : %s\n' "$DEPLOY_RESULT"
	echo "====================================================="
	exit $rc
}
trap report_elapsed EXIT
trap 'DEPLOY_RESULT="INTERRUPTED by signal"; exit 130' INT TERM

echo "=== Checking for Ansible Galaxy collections ==="

if [ -f requirements.yml ]; then
	echo "=== Installing/refreshing Ansible Galaxy collections ==="
	HTTPS_PROXY="http://10.255.240.1:3128" \
		ansible-galaxy collection install -r requirements.yml \
		|| echo "WARN: galaxy install returned non-zero; continuing"
fi

# --- Unattended prerequisites + vault guard ----------------------------------
# Ported from PowerPlant/ss-pp-ab after finding group_vars/vault.yml shipping
# PLAINTEXT in the tarball while ansible.cfg pointed vault_password_file at a
# file nobody had created. Seven credentials in the clear, and nothing to
# notice it -- exactly the case PowerPlant's guard was written for.
#
# `sudo -n` is non-interactive on purpose: a password prompt would hang a
# blueprint-driven deploy forever waiting on stdin.
ANSIBLE_OWNER="${ANSIBLE_OWNER:-simspace}"
VAULT_PASS_FILE="${VAULT_PASS_FILE:-/home/simspace/.vault_pass}"
RETRY_DIR="${RETRY_DIR:-/etc/ansible/retry}"
# group_vars/all/vault.yml, NOT group_vars/vault.yml. The latter maps to a
# GROUP named "vault", which does not exist in this inventory -- so the seven
# vault_* variables were never loaded by anything. fuel.yml's
# `vault_openplc_admin_password | default(...)` had silently been using the
# default all along. Moved under all/ 2026-08-11 so they actually apply.
VAULT_FILE="group_vars/all/vault.yml"

as_root() {
	if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo -n "$@"; fi
}

echo "=== Asserting prerequisites the platform is responsible for ==="

owner_of() {
	stat -c %U "$1" 2>/dev/null || stat -f %Su "$1" 2>/dev/null || echo unknown
}

# The tarball extracts as root, so this lands root-owned and the ansible user
# cannot write retry files -- a failed attempt 1 then loses its retry scope and
# attempt 2 silently degrades to a full sweep. Assert the END STATE
# unconditionally; chown is idempotent and costs milliseconds.
as_root mkdir -p "$RETRY_DIR" 2>/dev/null || true
as_root chown -R "$ANSIBLE_OWNER:$ANSIBLE_OWNER" "$RETRY_DIR" 2>/dev/null || true
as_root chmod 0755 "$RETRY_DIR" 2>/dev/null || true
retry_owner="$(owner_of "$RETRY_DIR")"
if [ "$retry_owner" = "$ANSIBLE_OWNER" ]; then
	echo "  $RETRY_DIR owned by $ANSIBLE_OWNER"
else
	echo "  WARN: $RETRY_DIR still owned by '$retry_owner' — retry scoping will be lost; deploy continues"
fi

if [ -f "$VAULT_PASS_FILE" ]; then
	as_root chown "$ANSIBLE_OWNER:$ANSIBLE_OWNER" "$VAULT_PASS_FILE" 2>/dev/null || true
	as_root chmod 0600 "$VAULT_PASS_FILE" 2>/dev/null || true
fi

# --- Vault guard, FAIL-CLOSED -----------------------------------------------
# Three separate checks, all fatal. Written this way because the equivalent
# guard in so-ansible was `if [ -f <path> ] && ! head -1 ... ` -- a MISSING
# file short-circuited the whole test to false, so it passed on every run and
# had never once fired.
if [ ! -f "$VAULT_FILE" ]; then
	echo "ERROR: $VAULT_FILE not found. Refusing to deploy."
	exit 1
fi

if ! head -1 "$VAULT_FILE" | grep -q '^\$ANSIBLE_VAULT'; then
	echo "ERROR: $VAULT_FILE is PLAINTEXT. Refusing to deploy."
	echo "       It ships inside ab_mb.tgz. Encrypt it:"
	echo "         ansible-vault encrypt $VAULT_FILE"
	exit 1
fi

# CREATE the password file if the platform did not. These deployments are
# blueprint-driven with nobody at a keyboard, so "the blueprint must place
# this" is a defect, not documentation -- the same reasoning that moved the
# retry-dir chown in here.
#
# THE TRADE, STATED SO NOBODY REDISCOVERS IT LATER: the vault password now
# ships inside ab_mb.tgz alongside the encrypted vault, so anyone holding the
# tarball can decrypt it. What encryption still buys is real but narrower --
# credentials stay out of the repo, out of `git log`, and out of a casual grep
# of a checkout. It is NOT protection against someone with the artifact.
#
# Revisit when the tarball moves to the in-platform Nexus: the platform can
# inject VAULT_PASS_VALUE as a real secret, and this default should go away.
VAULT_PASS_VALUE="${VAULT_PASS_VALUE:-simspace1}"

if [ ! -f "$VAULT_PASS_FILE" ]; then
	echo "  $VAULT_PASS_FILE missing — creating it"
	as_root install -m 0600 -o "$ANSIBLE_OWNER" -g "$ANSIBLE_OWNER" /dev/null "$VAULT_PASS_FILE" 2>/dev/null \
		|| as_root touch "$VAULT_PASS_FILE" 2>/dev/null || true
	# `tee` via as_root rather than a redirect: the redirect is performed by
	# THIS shell, which is not root, so `as_root echo ... > file` writes as the
	# unprivileged user and fails on a root-owned path.
	printf '%s' "$VAULT_PASS_VALUE" | as_root tee "$VAULT_PASS_FILE" >/dev/null 2>&1 || true
	as_root chown "$ANSIBLE_OWNER:$ANSIBLE_OWNER" "$VAULT_PASS_FILE" 2>/dev/null || true
	as_root chmod 0600 "$VAULT_PASS_FILE" 2>/dev/null || true
fi

if [ ! -f "$VAULT_PASS_FILE" ]; then
	echo "ERROR: $VAULT_PASS_FILE does not exist and could not be created."
	echo "       $VAULT_FILE is encrypted, and ansible.cfg points"
	echo "       vault_password_file here, so every play would fail at parse"
	echo "       time. Check that the deploy account has passwordless sudo."
	exit 1
fi

# READABILITY, not existence -- the chown above may have failed (sudo -n is
# deliberately non-interactive), and a file that exists but cannot be read
# fails later as a confusing decrypt error on the first vaulted variable.
if ! head -c1 "$VAULT_PASS_FILE" >/dev/null 2>&1; then
	echo "ERROR: $VAULT_PASS_FILE exists but is not readable by $(id -un)."
	ls -l "$VAULT_PASS_FILE" 2>&1 | sed 's/^/       /'
	exit 1
fi

if [ ! -s "$VAULT_PASS_FILE" ]; then
	echo "ERROR: $VAULT_PASS_FILE is empty. Refusing to deploy."
	exit 1
fi

# PROVE THE PASSWORD ACTUALLY DECRYPTS THE VAULT. Existence, readability and
# non-emptiness are all satisfiable by a WRONG password -- and a wrong one
# fails much later as an opaque parse error on the first vaulted variable,
# which reads as a YAML problem rather than a credential one.
if command -v ansible-vault >/dev/null 2>&1; then
	if ! ansible-vault view "$VAULT_FILE" --vault-password-file "$VAULT_PASS_FILE" >/dev/null 2>&1; then
		echo "ERROR: $VAULT_PASS_FILE does not decrypt $VAULT_FILE."
		echo "       A pre-existing password file may hold a different secret."
		echo "       Remove it and re-run to have deploy.sh recreate it, or set"
		echo "       VAULT_PASS_VALUE to the correct password."
		exit 1
	fi
	echo "  vault encrypted; password file present and DECRYPTS"
else
	echo "  vault encrypted; password file present and readable (ansible-vault absent, decrypt unverified)"
fi

# --- Let a freshly provisioned range finish booting --------------------------
# THIS IS A DIFFERENT LEVER FROM init_wait_timeout, and they are not
# interchangeable:
#
#   BOOT_DELAY          flat wall clock, paid ONCE before ansible starts, while
#                       the PLATFORM finishes provisioning. Covers hosts that do
#                       not exist yet -- no IP, no NIC, nothing to connect to.
#                       wait_for_connection cannot help there; it can only wait
#                       on a host that is at least present.
#   init_wait_timeout   per-host ceiling inside init. Costs NOTHING when a host
#                       is ready -- wait_for_connection returns the moment the
#                       connection succeeds -- so it is the cheap lever, and it
#                       only bites on hosts that are genuinely slow or dead.
#
# 300s, not ss-pp-so's 180s: this is the largest range of the four (86 hosts vs
# 74), and the provisioning tail scales with it. Observed twice on fresh
# deploys (2026-09-14 and 2026-09-15) that not every host had provisioned by
# the time the playbook reached Init. Five minutes against a ~5 hour deploy is
# noise; a wasted sweep is not.
BOOT_DELAY="${BOOT_DELAY:-300}"
if [ "$BOOT_DELAY" -gt 0 ]; then
	echo "=== Waiting ${BOOT_DELAY}s for range VMs to finish booting ==="
	echo "    (override with BOOT_DELAY=0 ./deploy.sh on an already-up range)"
	sleep "$BOOT_DELAY"
fi

ANSIBLE_START=$(date +%s)
DEPLOY_RESULT="INCOMPLETE — interrupted mid-run"

for i in $(seq 1 $MAX_ATTEMPTS); do
	ATTEMPT_START=$(date +%s)
	# Attempt 2 gets the retry-file scope IF the previous attempt actually
	# produced one. If the file is missing (e.g. deploy exited on a global
	# error before writing it), fall through to the full sweep.
	# A CLEAN REPAIR PASS IS NOT A DEPLOYED RANGE. This deliberately never
	# breaks out of the loop, however well it goes.
	#
	# The retry file lists the hosts that FAILED. Running site.yml limited to
	# them repairs those hosts -- but every play whose targets were dropped
	# when they failed still has not run. airfield 2026-09-15: bs-dc01 (sole
	# member of [pdc_blackstone]) failed on an ADWS race in attempt 1, so
	# Create Users, dns and BOTH domain joins lost their target, and the SO
	# phases never started. Attempt 2 scoped to bs-dc01 fixed bs-dc01, passed,
	# and deploy.sh reported "Success on attempt 2" over a range that had no
	# domain joins and no Security Onion.
	#
	# So the repair pass is a REPAIR, and attempt 3's full sweep is what
	# actually confirms the range. Eric caught this by re-running deploy.sh
	# manually and watching it pass a full sweep -- this automates exactly
	# that.
	#
	# Reachable only since 2026-09-15: before the RETRY_FILE path was fixed
	# the -f guard never matched, so attempt 2 was always a full sweep and
	# "success on attempt 2" really did mean a full sweep had passed.
	if [ $i -eq 2 ] && [ -f "$RETRY_FILE" ]; then
		echo "=== Attempt $i (retry-file scope — REPAIR PASS over failed hosts) ==="
		if ansible-playbook $PLAYBOOK --forks $FORKS --limit @"$RETRY_FILE" "$@"; then
			echo "Repair pass clean after $(fmt_elapsed $(($(date +%s) - ATTEMPT_START)))"
			echo "NOT declaring success — a full sweep must confirm the range"
		else
			echo "Attempt $i failed after $(fmt_elapsed $(($(date +%s) - ATTEMPT_START)))"
		fi
		rm -f "$RETRY_FILE"
		continue
	fi

	echo "=== Attempt $i (full sweep) ==="
	if ansible-playbook $PLAYBOOK --forks $FORKS "$@"; then
		echo "Success on attempt $i after $(fmt_elapsed $(($(date +%s) - ATTEMPT_START)))"
		DEPLOY_RESULT="SUCCESS on attempt $i"
		break
	fi

	echo "Attempt $i failed after $(fmt_elapsed $(($(date +%s) - ATTEMPT_START)))"

	# Preserve the retry file between attempts 1 and 2 (that's how attempt 2
	# knows which hosts to target). Clear it between 2 and 3 so a stale
	# retry list can't accidentally scope attempt 3 the same way attempt 2
	# was scoped.
	if [ $i -ge 2 ]; then
		rm -f "$RETRY_FILE"
	fi

	if [ $i -eq $MAX_ATTEMPTS ]; then
		echo "ERROR: Playbook failed after $MAX_ATTEMPTS attempts"
		DEPLOY_RESULT="FAILED after $MAX_ATTEMPTS attempts"
		exit 1
	fi
done
