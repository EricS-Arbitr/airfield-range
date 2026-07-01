#!/bin/bash
#
# deploy.sh — three-attempt Ansible runner with hybrid retry scope.
#
# Attempt 1: full site.yml against every host
# Attempt 2: --limit @retry-file (failed hosts only) if a retry file exists
# Attempt 3: full site.yml again (safety net if retry-scoped attempt didn't cover
#            a cross-host dependency)
#
# --forks 40 (up from Ansible default 5, and up from the previous 20 in
# ansible.cfg) so the full sweep finishes in roughly a third the wall-clock
# time on our ~76-host inventory. Enough parallelism to batch all ~14
# Linux hosts and half the ~48 Windows hosts per task pass. Controller has
# enough headroom (2-4 vCPU on the SimSpace VM); 40 concurrent host workers
# is a comfortable middle ground.
#
# See PROJECT_LOG.md or the retry-pattern discussion in the session where
# this file was rewritten for the rationale.

PLAYBOOK="site.yml"
RETRY_FILE="retry/$PLAYBOOK.retry"
MAX_ATTEMPTS=3
FORKS=40

# --- Install Galaxy collections (idempotent — skips already-installed ones) ---
# Required for the pfsensible.core collection that drives the pfSense plays
# (bs-edge-fw, bs-ops-fw). Pulled through the corp proxy because the Ansible
# VM doesn't have direct internet. Failure here doesn't abort the deploy —
# ansible-playbook will surface a clear "collection not found" error if
# anything's actually missing.
sleep 120 && echo "=== Checking for Ansible Galaxy collections ===" && \

if [ -f requirements.yml ]; then
	echo "=== Installing/refreshing Ansible Galaxy collections ==="
	HTTPS_PROXY="http://10.255.240.1:3128" \
		ansible-galaxy collection install -r requirements.yml \
		|| echo "WARN: galaxy install returned non-zero; continuing"
fi

for i in $(seq 1 $MAX_ATTEMPTS); do
	# Attempt 2 gets the retry-file scope IF the previous attempt actually
	# produced one. If the file is missing (e.g. deploy exited on a global
	# error before writing it), fall through to the full sweep.
	if [ $i -eq 2 ] && [ -f "$RETRY_FILE" ]; then
		echo "=== Attempt $i (retry-file scope — failed hosts only) ==="
		if ansible-playbook $PLAYBOOK --forks $FORKS --limit @"$RETRY_FILE" "$@"; then
			echo "Success on attempt $i (retry scope)"
			break
		fi
	else
		echo "=== Attempt $i (full sweep) ==="
		if ansible-playbook $PLAYBOOK --forks $FORKS "$@"; then
			echo "Success on attempt $i"
			break
		fi
	fi

	echo "Attempt $i failed"

	# Preserve the retry file between attempts 1 and 2 (that's how attempt 2
	# knows which hosts to target). Clear it between 2 and 3 so a stale
	# retry list can't accidentally scope attempt 3 the same way attempt 2
	# was scoped.
	if [ $i -ge 2 ]; then
		rm -f "$RETRY_FILE"
	fi

	if [ $i -eq $MAX_ATTEMPTS ]; then
		echo "ERROR: Playbook failed after $MAX_ATTEMPTS attempts"
		exit 1
	fi
done
