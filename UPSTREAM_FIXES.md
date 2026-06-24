# Upstream Fixes & Enhancements — airfield-range

Running log of issues, gaps, and suggested improvements discovered while deploying `airfield-range`. Candidates for PRs or discussion with the `range-development-ansible` maintainers, or for re-copies from PowerPlant when an existing PowerPlant fix needs to be brought across.

Per the [role-sourcing memory](../../.claude/projects/-Users-eric-starace-vCity/memory/project_airfield_role_sourcing.md): when an upstream fix is needed in airfield-range too, re-copy it explicitly **and log it here the same turn** (per the [UPSTREAM_FIXES feedback memory](../../.claude/projects/-Users-eric-starace-vCity/memory/feedback_upstream_fixes_log.md)).

Severity key:
- **bug** — role malfunctions or produces incorrect results
- **gap** — missing functionality that ranges have to work around
- **enhancement** — works but could be more robust or ergonomic
- **platform** — SimSpace platform-side issue, not Ansible

Format: `## YYYY-MM-DD · <severity> · <target path / heading>` followed by Symptom → Detection (if non-obvious) → Fix (upstream) → Workaround (overlay).

---

<!-- New entries go above this line, newest first. -->
