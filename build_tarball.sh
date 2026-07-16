#!/usr/bin/env bash
#
# Build ab_mb.tgz for deployment.
#
# Per the airfield-range role-sourcing policy (memory:
# project-airfield-role-sourcing), every role used by this project must
# already exist under airfield-range/roles/ — copied from the customer
# base or PowerPlant overlay at copy-time, not referenced at build-time.
#
# This script:
#   1. Discovers role names referenced by site.yml + walks their meta deps
#   2. Validates each one is physically present under ./roles/
#   3. Stages:  roles/ host_vars/ group_vars/ hosts site.yml deploy.sh
#               requirements.yml (if present) files/ (if present)
#   4. Runs verify_vars.py against the staged bundle
#
# UPSTREAM_FIXES.md and PROJECT_LOG.md are intentionally excluded.
#
# Usage: ./build_tarball.sh
#
set -euo pipefail

AIRFIELD_RANGE="$(cd "$(dirname "$0")" && pwd)"
# Both playbooks contribute to role discovery. site.yml is the primary
# deploy; fuel_farm_playbook.yml is a standalone OT sub-deploy (per user
# direction 2026-07-08) whose roles must also ship in the tarball.
PLAYBOOKS=("$AIRFIELD_RANGE/site.yml")
[ -f "$AIRFIELD_RANGE/fuel_farm_playbook.yml" ] && PLAYBOOKS+=("$AIRFIELD_RANGE/fuel_farm_playbook.yml")
ARCHIVE="$AIRFIELD_RANGE/ab_mb.tgz"
STAGE_PARENT="$(mktemp -d)"
STAGE="$STAGE_PARENT/abmb_build"

trap 'rm -rf "$STAGE_PARENT"' EXIT

# --- Helpers ---------------------------------------------------------------

# Extract role names from a playbook's `roles:` blocks.
# Handles both "  - rolename" and "  - role: rolename" forms.
extract_playbook_roles() {
  awk '
    /^  roles:/ { inroles=1; next }
    inroles && /^  [a-z]/ { inroles=0 }
    inroles && /^    - / {
      sub(/^    - role:[[:space:]]+/, "")
      sub(/^    - /, "")
      sub(/[ \t#].*$/, "")
      if (length($0) > 0) print
    }
  ' "$1"
}

# Extract role-dependency names from a meta/main.yml.
extract_meta_deps() {
  [ -f "$1" ] || return 0
  awk '
    /^dependencies:/ { indeps=1; next }
    indeps && /^[a-z]/ { indeps=0 }
    indeps && /^[[:space:]]*-[[:space:]]+role:/ {
      sub(/^[[:space:]]*-[[:space:]]+role:[[:space:]]+/, "")
      sub(/[ \t#].*$/, "")
      print
    }
  ' "$1"
}

in_array() {
  local needle="$1"; shift
  for x in "$@"; do
    [ "$x" = "$needle" ] && return 0
  done
  return 1
}

# --- Discovery -------------------------------------------------------------

for pb in "${PLAYBOOKS[@]}"; do
  [ -f "$pb" ] || { echo "ERROR: playbook not found at $pb" >&2; exit 1; }
done
[ -d "$AIRFIELD_RANGE/roles" ] || { echo "ERROR: roles dir missing at $AIRFIELD_RANGE/roles" >&2; exit 1; }

seen=()
queue=()
for pb in "${PLAYBOOKS[@]}"; do
  while IFS= read -r r; do queue+=("$r"); done < <(extract_playbook_roles "$pb")
done

missing=()
while [ ${#queue[@]} -gt 0 ]; do
  r="${queue[0]}"
  queue=("${queue[@]:1}")
  in_array "$r" "${seen[@]:-}" && continue
  seen+=("$r")

  rolepath="$AIRFIELD_RANGE/roles/$r"
  if [ -d "$rolepath" ]; then
    while IFS= read -r dep; do
      [ -n "$dep" ] && queue+=("$dep")
    done < <(extract_meta_deps "$rolepath/meta/main.yml")
  else
    missing+=("$r")
  fi
done

# --- Stage -----------------------------------------------------------------

mkdir -p "$STAGE/roles"

echo "=== Roles bundled (from $AIRFIELD_RANGE/roles) ==="
for r in "${seen[@]}"; do
  if [ -d "$AIRFIELD_RANGE/roles/$r" ]; then
    cp -R "$AIRFIELD_RANGE/roles/$r" "$STAGE/roles/"
    echo "  ✓ $r"
  fi
done

if [ ${#missing[@]} -gt 0 ]; then
  echo ""
  echo "ERROR: roles referenced by site.yml or meta deps but not present under airfield-range/roles/:"
  for r in "${missing[@]}"; do echo "  - $r"; done
  echo ""
  echo "Per the role-sourcing policy, copy each into airfield-range/roles/ before re-running."
  echo "Sources (precedence on copy):"
  echo "  1. ../PowerPlant/ss-pp-ab/roles/"
  echo "  2. ../PowerPlant/range-development-ansible/roles/"
  exit 1
fi

# Other deployment files
cp -R "$AIRFIELD_RANGE/host_vars"  "$STAGE/"
cp -R "$AIRFIELD_RANGE/group_vars" "$STAGE/"
cp    "$AIRFIELD_RANGE/hosts"      "$STAGE/"
cp    "$AIRFIELD_RANGE/site.yml"   "$STAGE/"
cp    "$AIRFIELD_RANGE/deploy.sh"  "$STAGE/"
chmod +x "$STAGE/deploy.sh"
if [ -f "$AIRFIELD_RANGE/fuel_farm_playbook.yml" ]; then
  cp "$AIRFIELD_RANGE/fuel_farm_playbook.yml" "$STAGE/"
fi
if [ -f "$AIRFIELD_RANGE/verify_deployment.sh" ]; then
  cp "$AIRFIELD_RANGE/verify_deployment.sh" "$STAGE/"
  chmod +x "$STAGE/verify_deployment.sh"
fi
if [ -f "$AIRFIELD_RANGE/verify_fuel_farm.sh" ]; then
  cp "$AIRFIELD_RANGE/verify_fuel_farm.sh" "$STAGE/"
  chmod +x "$STAGE/verify_fuel_farm.sh"
fi
if [ -f "$AIRFIELD_RANGE/deploy-diagnostic.sh" ]; then
  cp "$AIRFIELD_RANGE/deploy-diagnostic.sh" "$STAGE/"
  chmod +x "$STAGE/deploy-diagnostic.sh"
fi
if [ -f "$AIRFIELD_RANGE/fetch-fops-log.sh" ]; then
  cp "$AIRFIELD_RANGE/fetch-fops-log.sh" "$STAGE/"
  chmod +x "$STAGE/fetch-fops-log.sh"
fi
if [ -f "$AIRFIELD_RANGE/requirements.yml" ]; then
  cp "$AIRFIELD_RANGE/requirements.yml" "$STAGE/"
fi

if [ -d "$AIRFIELD_RANGE/files" ]; then
  cp -R "$AIRFIELD_RANGE/files" "$STAGE/"
  # Strip macOS .DS_Store noise so it doesn't ride along to /etc/ansible
  find "$STAGE/files" -name '.DS_Store' -delete 2>/dev/null || true
fi

# --- Verify ----------------------------------------------------------------

if [ -x "$AIRFIELD_RANGE/verify_vars.py" ] && command -v python3 >/dev/null 2>&1; then
  echo ""
  echo "=== Verifying Jinja var references ==="
  python3 "$AIRFIELD_RANGE/verify_vars.py" "$STAGE" || true
fi

# --- Pack ------------------------------------------------------------------

cd "$STAGE"
TAR_PATHS=(roles host_vars group_vars hosts site.yml deploy.sh)
[ -f "fuel_farm_playbook.yml" ] && TAR_PATHS+=(fuel_farm_playbook.yml)
[ -f "verify_deployment.sh" ] && TAR_PATHS+=(verify_deployment.sh)
[ -f "verify_fuel_farm.sh" ] && TAR_PATHS+=(verify_fuel_farm.sh)
[ -f "deploy-diagnostic.sh" ] && TAR_PATHS+=(deploy-diagnostic.sh)
[ -f "fetch-fops-log.sh" ] && TAR_PATHS+=(fetch-fops-log.sh)
[ -f "requirements.yml" ] && TAR_PATHS+=(requirements.yml)
[ -d "files" ] && TAR_PATHS+=(files)
tar --no-xattrs -czf "$ARCHIVE" "${TAR_PATHS[@]}"

echo ""
echo "=== Archive built ==="
ls -lh "$ARCHIVE"
echo "Roles bundled: ${#seen[@]} total"
