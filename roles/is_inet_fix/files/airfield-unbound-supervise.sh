#!/bin/bash
# Ansible-managed. Ensure the is-inet container's unbound is running.
#
# The RC-IS-INET container image's entrypoint launches Postfix, Dovecot,
# and httpd but NOT unbound. Left alone, corp DNS forwarders that point
# at is-inet's 8.8.8.8/8.8.4.4/1.1.1.1 loopback aliases get connection-
# refused / timeout. We start unbound here and re-check every minute
# via the .timer sibling.
#
# Idempotent: safe to run every minute.

set -eu

CONTAINER=is-inet

if ! docker ps --filter "name=${CONTAINER}" --format '{{.Names}}' 2>/dev/null | grep -qx "${CONTAINER}"; then
  echo "$(date -Is) supervise: ${CONTAINER} container not running; nothing to do"
  exit 0
fi

if docker exec "${CONTAINER}" ss -lnu 2>/dev/null | grep -q ':53 '; then
  # unbound already listening; done
  exit 0
fi

echo "$(date -Is) supervise: unbound not listening in ${CONTAINER}; starting"

# Make sure the log file exists + is unbound-owned (image ships neither)
docker exec "${CONTAINER}" touch /var/log/unbound.log 2>/dev/null || true
docker exec "${CONTAINER}" chown unbound:unbound /var/log/unbound.log 2>/dev/null || true

# Clear any stale PID from a previous crash
docker exec "${CONTAINER}" rm -f /var/run/unbound.pid 2>/dev/null || true

# Start unbound daemonized
docker exec -d "${CONTAINER}" /usr/sbin/unbound -c /etc/unbound/unbound.conf

# Give it a beat to bind, then confirm
sleep 2
if docker exec "${CONTAINER}" ss -lnu 2>/dev/null | grep -q ':53 '; then
  echo "$(date -Is) supervise: unbound is up"
else
  echo "$(date -Is) supervise: unbound failed to start; check /var/log/unbound.log in the container"
  exit 1
fi
