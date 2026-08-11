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
#   1. Discovers role names referenced by site.yml, every playbooks/*.yml it
#      imports, and their meta deps
#   2. Validates each one is physically present under ./roles/
#   3. Stages:  roles/ playbooks/ host_vars/ group_vars/ hosts site.yml
#               deploy.sh rules/ requirements.yml (if present) files/ (if present)
#   3b. Asserts every bundled role's required data payloads are staged
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
# Everything site.yml pulls in with `import_playbook`. WITHOUT THIS the SO
# roles are invisible to discovery and ship in no tarball at all -- the
# archive builds clean, reports "41 roles bundled", and the deploy dies on
# the controller with "the role 'so_base' was not found". Caught 2026-08-11
# by asserting the tarball CONTAINS the new files instead of trusting the
# build's own success message; same lesson as the `|| true` regression that
# silently froze the archive for eight commits.
if [ -d "$AIRFIELD_RANGE/playbooks" ]; then
  while IFS= read -r pb; do PLAYBOOKS+=("$pb"); done \
    < <(find "$AIRFIELD_RANGE/playbooks" -maxdepth 1 -name '*.yml' | sort)
fi
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

# Extract role names from `import_role:` / `include_role:` blocks, which the
# `roles:` scanner above cannot see. playbooks/20-vyos.yml reaches vyos_mirror
# ONLY this way (twice, via tasks_from), so without this the role is missing
# from the bundle while every other SO role is present -- the most confusing
# possible failure mode.
extract_included_roles() {
  awk '
    /(import_role|include_role):/ { inrole=1; next }
    inrole && /^[[:space:]]*name:[[:space:]]*/ {
      sub(/^[[:space:]]*name:[[:space:]]*/, "")
      sub(/[ \t#].*$/, "")
      gsub(/["'"'"']/, "")
      if (length($0) > 0) print
      inrole=0
      next
    }
    inrole && /^[[:space:]]*[a-z_]+:/ && !/tasks_from|vars|apply|public|defaults_from/ { inrole=0 }
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
  while IFS= read -r r; do queue+=("$r"); done < <(extract_included_roles "$pb")
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
# site.yml's `import_playbook` paths are relative to site.yml, so this
# directory has to land beside it at the same depth.
if [ -d "$AIRFIELD_RANGE/playbooks" ]; then
  cp -R "$AIRFIELD_RANGE/playbooks" "$STAGE/"
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

# Detection rulesets that MUST ship inside the tarball. These ranges target
# platforms with NO external access -- not even a proxy -- so downloading the
# ETOPEN ruleset at deploy time is not an option. so_apt_mirror reads it from
# /etc/ansible/rules/ and fails by name if it is absent.
if [ -d "$AIRFIELD_RANGE/rules" ]; then
  cp -R "$AIRFIELD_RANGE/rules" "$STAGE/"
fi

if [ -d "$AIRFIELD_RANGE/files" ]; then
  cp -R "$AIRFIELD_RANGE/files" "$STAGE/"
  # Strip macOS .DS_Store noise so it doesn't ride along to /etc/ansible
  find "$STAGE/files" -name '.DS_Store' -delete 2>/dev/null || true
fi

# --- Required payloads -----------------------------------------------------
# ROLES ARE NOT SELF-CONTAINED. Some read a data file from the controller that
# lives OUTSIDE roles/, so copying the role in is only half the job -- and the
# half that is missing costs a full deploy to discover, because nothing fails
# until the role runs.
#
# so_apt_mirror was ported 2026-08-11 without rules/emerging.rules.tar.gz.
# Everything downstream verified clean: the archive built, 48 roles bundled,
# site.yml parsed from a clean extraction. The deploy then failed at phase 10
# with "was not bundled at /etc/ansible/rules/emerging.rules.tar.gz" -- the
# role's own error, working exactly as designed, ~40 minutes in.
#
# The TAR_PATHS assertion below cannot catch this class: it compares what was
# STAGED against what gets PACKED, and this file was never staged at all.
# Declare the dependency instead, keyed on the role actually being bundled.
declare -a REQUIRED_PAYLOADS=(
  "so_apt_mirror:rules/emerging.rules.tar.gz"
)
payload_missing=0
for req in "${REQUIRED_PAYLOADS[@]}"; do
  need_role="${req%%:*}"
  need_file="${req#*:}"
  in_array "$need_role" "${seen[@]:-}" || continue
  if [ ! -f "$STAGE/$need_file" ]; then
    echo "ERROR: role '$need_role' is bundled but requires '$need_file', which is not staged." >&2
    echo "       It is read from /etc/ansible/$need_file at deploy time and cannot be" >&2
    echo "       downloaded -- these ranges have no egress. Add it to the repo." >&2
    payload_missing=1
  fi
done
[ "$payload_missing" -eq 0 ] || exit 1

# --- Verify ----------------------------------------------------------------

if [ -x "$AIRFIELD_RANGE/verify_vars.py" ] && command -v python3 >/dev/null 2>&1; then
  echo ""
  echo "=== Verifying Jinja var references ==="
  python3 "$AIRFIELD_RANGE/verify_vars.py" "$STAGE" || true
fi

# --- Pack ------------------------------------------------------------------

cd "$STAGE"
TAR_PATHS=(roles host_vars group_vars hosts site.yml deploy.sh)
[ -d "playbooks" ] && TAR_PATHS+=(playbooks)
[ -d "rules" ] && TAR_PATHS+=(rules)
[ -f "fuel_farm_playbook.yml" ] && TAR_PATHS+=(fuel_farm_playbook.yml)
[ -f "verify_deployment.sh" ] && TAR_PATHS+=(verify_deployment.sh)
[ -f "verify_fuel_farm.sh" ] && TAR_PATHS+=(verify_fuel_farm.sh)
[ -f "deploy-diagnostic.sh" ] && TAR_PATHS+=(deploy-diagnostic.sh)
[ -f "fetch-fops-log.sh" ] && TAR_PATHS+=(fetch-fops-log.sh)
[ -f "requirements.yml" ] && TAR_PATHS+=(requirements.yml)
[ -d "files" ] && TAR_PATHS+=(files)
# macOS junk, stripped from the whole stage before packing.
find "$STAGE" \( -name '.DS_Store' -o -name '._*' \) -delete 2>/dev/null || true

# THIS SCRIPT HAS TWO ALLOWLISTS. The Stage section above decides what gets
# copied into $STAGE; TAR_PATHS decides what actually gets packed. Adding to
# one and not the other produces an archive that builds clean, reports the
# right role count, and is missing the files -- which is exactly what happened
# to playbooks/ on 2026-08-11: staged correctly, absent from TAR_PATHS,
# invisible in the build output.
#
# Rather than merge them (TAR_PATHS gives deliberate control over ordering and
# over staged-but-unshipped artifacts), assert they agree. Anything staged and
# not packed is a mistake; say so and stop.
for entry in *; do
  packed=0
  for p in "${TAR_PATHS[@]}"; do [ "$p" = "$entry" ] && packed=1 && break; done
  if [ "$packed" -eq 0 ]; then
    echo "ERROR: '$entry' was staged but is not in TAR_PATHS -- it would be" >&2
    echo "       silently missing from ab_mb.tgz. Add it to TAR_PATHS." >&2
    exit 1
  fi
done

# COPYFILE_DISABLE=1 is the load-bearing setting. Apple's tar emits an
# AppleDouble "._name" companion for every file carrying an extended
# attribute, and com.apple.provenance is set on anything downloaded --
# i.e. most of a checked-out repo. `--no-xattrs` does NOT suppress them,
# despite the comment this line used to carry. Measured 2026-08-07:
# plain tar 2 junk members, --no-xattrs 2, COPYFILE_DISABLE=1 zero.
#
# This archive was 836 members with 418 junk -- one companion per real file,
# half the tarball, on every deploy. Extraction is ADDITIVE, so every one of
# them persists in /etc/ansible.
#
# Apple's `tar -tzf` HIDES AppleDouble members when listing, so macOS tar
# cannot verify this. Check with python3 tarfile.
COPYFILE_DISABLE=1 tar --no-xattrs \
  --exclude='.DS_Store' --exclude='._*' \
  -czf "$ARCHIVE" "${TAR_PATHS[@]}"

echo ""
echo "=== Archive built ==="
ls -lh "$ARCHIVE"
echo "Roles bundled: ${#seen[@]} total"
