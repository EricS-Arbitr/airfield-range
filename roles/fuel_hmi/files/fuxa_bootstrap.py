#!/usr/bin/env python3
"""
fuxa_bootstrap.py -- import a FUXA project.json via HTTP API.

FUXA doesn't auto-load JSON files from the bind-mounted _projects
directory -- that path is used by the editor's Save/Load buttons but
not by server startup. To activate an Ansible-managed project we
POST it to /api/project on the running FUXA server.

Idempotent-by-content: GETs the current /api/project first and
compares by device+view IDs. If our fuel_farm device + both views are
already present, exits 0 with "no change". Otherwise POSTs the file.

Auth handling: modern FUXA supports optional auth. This script tries
unauth first; if that returns 401, it tries an /api/signin login with
the vault-supplied creds and retries with the returned JWT.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print("ERROR: python3-requests not installed on target", file=sys.stderr)
    sys.exit(2)


EXPECTED_DEVICE_NAME = "PLC-FuelFarm"
EXPECTED_DEVICE_TYPE = "ModbusTCP"
EXPECTED_VIEW_IDS = {"v_process_overview", "v_rack_detail"}


def wait_for(base: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(base, timeout=5)
            if r.status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise SystemExit(f"FUXA at {base} did not answer within {timeout}s")


def _headers(token: str | None) -> dict:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _signin(base: str, user: str, pw: str) -> str | None:
    """Try to auth against FUXA and return a JWT, or None if auth is off.

    FUXA's auth endpoint varies -- /api/signin in most versions, /api/login
    in a few. Try both.
    """
    for path in ("/api/signin", "/api/login"):
        try:
            r = requests.post(
                urljoin(base, path),
                json={"username": user, "password": pw},
                timeout=10,
            )
        except requests.RequestException:
            continue
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                continue
            for key in ("token", "access_token", "jwt"):
                if key in data:
                    return data[key]
    return None


def get_current_project(base: str, token: str | None) -> dict | None:
    """Fetch the current project. Returns None if server has no project."""
    r = requests.get(
        urljoin(base, "/api/project"),
        headers=_headers(token),
        timeout=15,
    )
    if r.status_code == 401:
        raise PermissionError("auth required")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return None


def project_matches(current: dict, target: dict) -> bool:
    """Cheap idempotency: PLC-FuelFarm exists AND has type == Modbus AND
    both view IDs are present. Type check is critical after 2026-07-17
    schema fix where we changed 'ModbusTCP' (unknown to plugin registry)
    to 'Modbus' (registered plugin type). Without checking type, a
    project uploaded before the fix would look OK to this check and
    the fixed upload would get skipped.
    """
    if not current:
        return False
    devs_by_name = {}
    for d in (current.get("devices") or {}).values():
        if isinstance(d, dict) and d.get("name"):
            devs_by_name[d["name"]] = d
    plc = devs_by_name.get(EXPECTED_DEVICE_NAME)
    if not plc:
        return False
    if plc.get("type") != EXPECTED_DEVICE_TYPE:
        return False
    cur_views = {
        v.get("id")
        for v in ((current.get("hmi") or {}).get("views") or [])
        if isinstance(v, dict)
    }
    return EXPECTED_VIEW_IDS.issubset(cur_views)


def post_project(base: str, token: str | None, project: dict) -> None:
    r = requests.post(
        urljoin(base, "/api/project"),
        json=project,
        headers=_headers(token),
        timeout=30,
    )
    if r.status_code >= 400:
        raise SystemExit(
            f"POST /api/project returned {r.status_code}: "
            + r.text[:400].replace("\n", " ")
        )


def ensure_broadcast_all(base: str, token: str | None) -> None:
    """Set settings.broadcastAll = true so the server pushes ALL tag values
    to every client every poll, without the client having to send an
    explicit tag-subscription first. Without this, the runtime's
    updateDeviceValues() function (see server/runtime/index.js:555) takes
    the "subscription-only" branch and sends an empty values array to
    clients that haven't subscribed. Our compound-widget project doesn't
    trigger the subscription registration path on load, so widgets get
    starved of values indefinitely.

    Setting persists to _appdata/mysettings.json. Idempotent -- POSTing
    the same value repeatedly is a no-op on FUXA's side.
    """
    r = requests.get(urljoin(base, "/api/settings"), headers=_headers(token), timeout=10)
    if r.status_code == 200:
        try:
            current = r.json()
            if current.get("broadcastAll") is True:
                return  # already set, no action needed
        except ValueError:
            pass
    # POST the setting
    r = requests.post(
        urljoin(base, "/api/settings"),
        json={"broadcastAll": True},
        headers=_headers(token),
        timeout=10,
    )
    if r.status_code >= 400:
        # Non-fatal -- warn but continue
        print(
            f"WARN: POST /api/settings broadcastAll=true returned {r.status_code}. "
            "Widgets may show empty values until this is set manually.",
            file=sys.stderr,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=1881)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--project-file", required=True, help="Path to the JSON on the target")
    ap.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass idempotency check and always POST. Use when the schema "
            "changed inside the project (tag memaddress format, item widget "
            "type, etc.) without changing device/view names -- the shallow "
            "idempotency check can't detect those and would skip the upload."
        ),
    )
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    wait_for(base, timeout=60)

    with Path(args.project_file).open() as f:
        target_project = json.load(f)

    token: str | None = None
    try:
        current = get_current_project(base, token)
    except PermissionError:
        token = _signin(base, args.user, args.password)
        if not token:
            raise SystemExit(
                "FUXA requires auth but neither /api/signin nor /api/login "
                "accepted the vault-supplied creds."
            )
        current = get_current_project(base, token)

    # Persist broadcastAll:true regardless of project upload path. It's an
    # orthogonal setting that just needs to be right for widgets to bind.
    ensure_broadcast_all(base, token)

    if not args.force and project_matches(current or {}, target_project):
        print("OK: FUXA already has fuel_farm project loaded -- no change")
        return 0
    if args.force:
        print("--force set -- bypassing idempotency check, uploading fresh")

    print("FUXA project mismatch or empty -- uploading fuel_farm project")
    post_project(base, token, target_project)

    # Verify by re-fetching
    time.sleep(1)
    current = get_current_project(base, token)
    if not project_matches(current or {}, target_project):
        raise SystemExit(
            "POST succeeded but re-fetch of /api/project doesn't show the "
            "fuel_farm device + both views. FUXA may not have persisted."
        )
    print("OK: fuel_farm project uploaded and verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
