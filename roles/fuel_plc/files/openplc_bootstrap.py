#!/usr/bin/env python3
"""
openplc_bootstrap.py -- one-shot program upload + runtime start for
OpenPLC v3 (fdamador/openplc image) via its web UI on :8080.

Idempotent. Two-phase check:

  1. Dashboard says "Runtime: Running" AND its "Currently running" panel
     lists our program name -> nothing to do, exit 0.
  2. Otherwise: stop_plc (if running) -> upload the .st -> upload-program-action
     (metadata record) -> compile -> start_plc -> poll dashboard until Running.

Exit codes:
  0  success (already running with our program, or just started it)
  1  fatal (upload failed, compile failed, runtime did not come up)
  2  usage / missing dep

Notes on the fdamador/openplc image (v3, based on OpenPLC_v3/webserver):
  * openplc.db lives at /workdir/webserver/openplc.db (INSIDE the container).
    We don't touch it -- we drive the web UI so schema quirks between forks
    don't bite us.
  * /login is form-encoded (username/password), sets a session cookie, 302s
    on success, 200 (with error page) on failure.
  * /upload-program POST multipart {file} -> 302 -> /upload-program-action
    form: {prog_file, prog_name, prog_descr, epoch_time}. On the fdamador
    image, when the multipart POST succeeds, the response body already
    contains the temp filename in a hidden input we scrape.
  * /compile-program?file=<temp_name> streams the compile log. We consume
    the stream to completion, then check /programs to confirm success.
  * /start_plc / /stop_plc are GETs (yes, GETs) that flip runtime state.
  * /dashboard shows runtime status text; on the fdamador image the running
    program name is in a paragraph after "Currently running:".
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print("ERROR: python3-requests not installed on target", file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def wait_for_web(base: str, timeout: int = 60) -> None:
    """Block until the web UI answers *any* HTTP response, up to timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(base, timeout=5, allow_redirects=False)
            if r.status_code in (200, 302, 401):
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise SystemExit(f"OpenPLC web at {base} did not answer within {timeout}s")


_OPENPLC_DEFAULT_USER = "openplc"
_OPENPLC_DEFAULT_PW = "openplc"


def _try_login(sess: requests.Session, base: str, user: str, pw: str) -> bool:
    """Return True iff (user, pw) successfully authenticated a fresh session.

    We can't reuse a Session across attempts -- a failed login taints the
    cookie state on some builds and subsequent GET /dashboard doesn't
    redirect back to /login the way you'd expect. So each attempt clears
    cookies first and tests validity via GET /dashboard on the new cookie.
    """
    sess.cookies.clear()
    r = sess.post(
        urljoin(base, "/login"),
        data={"username": user, "password": pw},
        allow_redirects=False,
        timeout=10,
    )
    if r.status_code == 302 and "/dashboard" in r.headers.get("Location", ""):
        return True
    # Some builds return 200 with the dashboard on success and 200 with the
    # login form on failure. Distinguish by fetching /dashboard on the new
    # cookie -- a valid session gets 200 (dashboard HTML), invalid gets 302
    # to /login.
    r2 = sess.get(urljoin(base, "/dashboard"), timeout=10, allow_redirects=False)
    return r2.status_code == 200


def login(sess: requests.Session, base: str, user: str, pw: str) -> str:
    """Try the configured (user, pw) first. If that fails, fall back to
    OpenPLC's hardcoded default (openplc / openplc) -- this covers the
    case where the fdamador image didn't seed the env-supplied creds on
    first boot (image variant mismatch, or the DB was persistent across
    a boot that predated the env).

    Returns the credential set that succeeded ("configured" or "default")
    so the caller can log whether rotation is still owed.
    """
    if _try_login(sess, base, user, pw):
        return "configured"
    if (user, pw) != (_OPENPLC_DEFAULT_USER, _OPENPLC_DEFAULT_PW) and _try_login(
        sess, base, _OPENPLC_DEFAULT_USER, _OPENPLC_DEFAULT_PW
    ):
        # We're now authenticated as the default admin. Emit a WARN so the
        # role can flag a rotation TODO -- doing the rotation here would
        # invalidate the current session and require re-login, and the
        # /change_password endpoint URL varies across forks, so keep this
        # a follow-up.
        print(
            "WARN: authenticated with OpenPLC DEFAULT creds (openplc/openplc). "
            "Vault creds not applied -- rotation still owed. Proceeding.",
            file=sys.stderr,
        )
        return "default"
    raise SystemExit(
        f"login failed: neither configured user {user!r} nor the "
        f"OpenPLC default ('openplc'/'openplc') was accepted"
    )


# --------------------------------------------------------------------------
# Dashboard scrape
# --------------------------------------------------------------------------

# fdamador/openplc dashboard markup (verified 2026-07-17 against a live
# container):
#     <b>Status: <font color = 'Red'>Stopped</font></b>
#     <b>Program:</b> fuel_farm</p>
# The color attribute isn't reliable (varies by fork), so we anchor on the
# state word inside the <font>...</font> and the label text of the Program
# row. Fall back to the header banner if the panel isn't present.
_RUNTIME_STATE_PAT = re.compile(
    r"<b>\s*Status:\s*<font[^>]*>\s*(\w+)\s*</font>", re.IGNORECASE,
)
_HEADER_STATE_PAT = re.compile(
    r"<span[^>]*>\s*(Running|Stopped|Compiling)\s*[:\s]", re.IGNORECASE,
)
_CURRENT_PROG_PAT = re.compile(
    r"<b>\s*Program\s*:\s*</b>\s*([^<]+?)\s*</p>", re.IGNORECASE,
)


def dashboard_state(sess: requests.Session, base: str) -> tuple[str, str | None]:
    """Return ('running'|'stopped'|'compiling', current_program_name_or_None)."""
    r = sess.get(urljoin(base, "/dashboard"), timeout=10)
    r.raise_for_status()
    text = r.text
    m = _RUNTIME_STATE_PAT.search(text) or _HEADER_STATE_PAT.search(text)
    state_word = m.group(1).lower() if m else "stopped"
    # Normalize: 'Started' or 'Active' shouldn't appear in this fork but
    # future-proof by mapping anything non-'stopped'/'compiling' to running.
    if state_word in ("stopped",):
        state = "stopped"
    elif state_word in ("compiling",):
        state = "compiling"
    else:
        state = "running"
    m2 = _CURRENT_PROG_PAT.search(text)
    current = m2.group(1).strip() if m2 else None
    return state, current


# --------------------------------------------------------------------------
# Program upload + compile + start
# --------------------------------------------------------------------------

class _HiddenInputScraper(HTMLParser):
    """Pulls <input type='hidden' name='...' value='...'> pairs from HTML.

    OpenPLC's /upload-program response embeds prog_file (the server-side
    temp filename) in a hidden input on the resulting form. We need that
    exact value to POST /upload-program-action correctly -- guessing at
    it would break on any filename-mangling logic the server does.
    """
    def __init__(self) -> None:
        super().__init__()
        self.hidden: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        d = dict(attrs)
        if d.get("type") == "hidden" and "name" in d and "value" in d:
            self.hidden[d["name"]] = d["value"] or ""


def _extract_prog_file(html_body: str) -> str | None:
    s = _HiddenInputScraper()
    s.feed(html_body)
    return s.hidden.get("prog_file")


def upload_and_start(
    sess: requests.Session,
    base: str,
    name: str,
    description: str,
    st_path: str,
) -> None:
    # Stop the current runtime if running -- switching programs requires it.
    # Idempotent: /stop_plc on a stopped runtime is a no-op.
    sess.get(urljoin(base, "/stop_plc"), timeout=10, allow_redirects=False)

    # Phase 1: multipart POST the .st file. Server writes it to a temp path
    # under /workdir/webserver/st_files/ and renders a metadata form with
    # the temp filename in a hidden input.
    with open(st_path, "rb") as f:
        files = {
            "file": (
                st_path.rsplit("/", 1)[-1],
                f,
                "application/octet-stream",
            ),
        }
        r = sess.post(
            urljoin(base, "/upload-program"),
            files=files,
            timeout=30,
        )
    if r.status_code >= 400:
        raise SystemExit(f"/upload-program returned {r.status_code}")
    prog_file = _extract_prog_file(r.text)
    if not prog_file:
        raise SystemExit(
            "/upload-program response did not contain a prog_file hidden "
            "input -- fdamador image variant mismatch? Response head: "
            + r.text[:300].replace("\n", " ")
        )

    # Phase 2: POST metadata. This creates the Programs row and sets
    # Current_program in the Settings table.
    r = sess.post(
        urljoin(base, "/upload-program-action"),
        data={
            "prog_file": prog_file,
            "prog_name": name,
            "prog_descr": description,
            "epoch_time": str(int(time.time())),
        },
        allow_redirects=False,
        timeout=30,
    )
    if r.status_code not in (200, 302):
        raise SystemExit(f"/upload-program-action returned {r.status_code}")

    # Phase 3: compile. On fdamador/openplc, /compile-program is ASYNC --
    # it returns an HTML shell almost immediately, then a background
    # process runs matiec + gcc. The actual compile log is served
    # separately at /compilation-logs, which is what the web UI's JS
    # polls (every 1 s). We do the same server-side.
    r = sess.get(
        urljoin(base, f"/compile-program?file={prog_file}"),
        timeout=15,
    )
    if r.status_code != 200:
        raise SystemExit(f"/compile-program returned {r.status_code}")

    _wait_for_compile(sess, base, timeout=300)

    # Phase 4: start runtime and poll for Running.
    r = sess.get(urljoin(base, "/start_plc"), timeout=10, allow_redirects=False)
    if r.status_code not in (200, 302):
        raise SystemExit(f"/start_plc returned {r.status_code}")

    for _ in range(30):
        time.sleep(2)
        state, _ = dashboard_state(sess, base)
        if state == "running":
            return
    raise SystemExit(
        "runtime did not report Running within 60s after /start_plc. "
        "Check /runtime_logs on the web UI for details."
    )


# --------------------------------------------------------------------------
# Compile-log polling
# --------------------------------------------------------------------------

_COMPILE_OK = "Compilation finished successfully!"
_COMPILE_ERR = "Compilation finished with errors!"


def _wait_for_compile(sess: requests.Session, base: str, timeout: int) -> None:
    """Poll /compilation-logs until one of the two sentinels appears.

    The fdamador image (and stock OpenPLC v3) writes one of these exact
    strings to the log tail when the background compile process reaps:
      - "Compilation finished successfully!"
      - "Compilation finished with errors!"
    Anything else means the compile is still running (or the log endpoint
    isn't answering, which we treat as a fatal timeout).
    """
    deadline = time.time() + timeout
    log_text = ""
    while time.time() < deadline:
        r = sess.get(urljoin(base, "/compilation-logs"), timeout=10)
        log_text = r.text
        if _COMPILE_OK in log_text:
            return
        if _COMPILE_ERR in log_text:
            tail = log_text[-2500:]
            raise SystemExit(
                "compile FAILED. Last 2.5 KB of /compilation-logs:\n" + tail
            )
        time.sleep(2)
    tail = log_text[-2500:] if log_text else "(empty)"
    raise SystemExit(
        f"compile did not finish within {timeout}s. Log tail:\n" + tail
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def slave_device_exists(sess: requests.Session, base: str, name: str) -> bool:
    """Check whether a slave device with `name` is already configured.

    The /modbus page lists configured slave devices as a table. Name matches
    are literal so we grep the response body. Cheap idempotency guard --
    fdamador's /modbus doesn't offer a JSON API for the slave list.
    """
    r = sess.get(urljoin(base, "/modbus"), timeout=10)
    if r.status_code != 200:
        return False
    return name in r.text


def _query_slave_dev_field(name: str, field: str) -> str | None:
    """Query openplc.db directly for one column of the fuel-farm-sim row.
    Returns the value as a string, or None if the row doesn't exist or the
    query fails. Runs `docker exec sqlite3` under the hood -- bootstrap
    executes on the target host (ff-plc-1), not inside the container, so
    docker CLI is available. Requires root or docker-group access; the
    role runs with become: yes so this is fine.
    """
    try:
        r = subprocess.run(
            [
                "docker", "exec", "openplc",
                "sqlite3", "/workdir/webserver/openplc.db",
                f"SELECT {field} FROM Slave_dev WHERE dev_name='{name}';",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        out = r.stdout.strip()
        return out if out else None
    except (subprocess.SubprocessError, OSError):
        return None


def _delete_slave_dev_row(name: str) -> None:
    """Delete a slave device row from openplc.db. Used to force a re-add
    when the existing row's config differs from what we want (idempotency
    beyond bare name matching).
    """
    subprocess.run(
        [
            "docker", "exec", "openplc",
            "sqlite3", "/workdir/webserver/openplc.db",
            f"DELETE FROM Slave_dev WHERE dev_name='{name}';",
        ],
        capture_output=True, text=True, timeout=10,
    )


def add_slave_device(
    sess: requests.Session,
    base: str,
    *,
    name: str,
    ip: str,
    port: int,
    slave_id: int,
    di_size: int,
    ai_size: int,
    aor_size: int = 0,
) -> None:
    """POST /add-modbus-device with the fuel-farm-sim TCP mapping.

    Reads: di (discrete inputs), ai (input registers), and optionally aor
    (holding registers READ). do/aow always 0 -- OpenPLC never WRITES to
    fuelsim's coils or HRs; fuelsim's own state_machine is authoritative
    for those, and letting OpenPLC clobber them would break physics.
    aor_size=8 mirrors fuelsim's HRs (LR*_ACTIVE_TRUCK, LR*_SRC_TANK,
    LR*_PRESET_GAL, etc.) into OpenPLC's %QW100+ region for FUXA/Grafana
    to display.
    """
    data = {
        "device_name": name,
        "device_protocol": "TCP",
        "device_id": str(slave_id),
        "device_ip": ip,
        "device_port": str(port),
        # Serial fields required by the form but ignored for TCP -- send
        # sensible defaults so the server-side validator doesn't reject.
        "device_cport": "/dev/ttyS0",
        "device_baud": "19200",
        "device_parity": "None",
        "device_data": "8",
        "device_stop": "1",
        # Register mapping. Start=0 for each (first slave device -> memory
        # image aligns 1:1 with fuel_farm.st's %IX / %IW declarations at
        # addresses 0-10 / 0-9).
        "di_start": "0",
        "di_size": str(di_size),
        "do_start": "0",
        "do_size": "0",   # do not overwrite fuelsim's coils
        "ai_start": "0",
        "ai_size": str(ai_size),
        "aor_start": "0",
        "aor_size": str(aor_size),   # 0 skips HR mirror, 8 pulls fuelsim's HRs
        "aow_start": "0",
        "aow_size": "0",  # do not write fuelsim's HRs
    }
    r = sess.post(
        urljoin(base, "/add-modbus-device"),
        data=data,
        timeout=30,
        allow_redirects=False,
    )
    if r.status_code not in (200, 302):
        raise SystemExit(
            f"/add-modbus-device returned {r.status_code}: {r.text[:400]!r}"
        )


def restart_runtime(sess: requests.Session, base: str) -> None:
    """Cycle the OpenPLC runtime so newly-added slave devices take effect."""
    sess.get(urljoin(base, "/stop_plc"), timeout=15, allow_redirects=False)
    time.sleep(2)
    sess.get(urljoin(base, "/start_plc"), timeout=15, allow_redirects=False)
    # Poll dashboard until Running again
    for _ in range(30):
        time.sleep(2)
        state, _ = dashboard_state(sess, base)
        if state == "running":
            return
    raise SystemExit("runtime did not report Running within 60s after restart")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, help="OpenPLC web host (usually the PLC's IP)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--program-name", required=True)
    ap.add_argument("--program-file", required=True, help="Path to the .st on the target")
    ap.add_argument(
        "--program-description",
        default="Fuel farm interlocks (Ansible-managed)",
    )
    # Slave device config (fuel-farm-sim). All optional -- if --slave-name
    # is unset, the slave-device step is skipped.
    ap.add_argument("--slave-name", help="If set, ensure a slave device by this name")
    ap.add_argument("--slave-ip")
    ap.add_argument("--slave-port", type=int, default=502)
    ap.add_argument("--slave-id", type=int, default=1)
    ap.add_argument("--slave-di-size", type=int, default=11)
    ap.add_argument("--slave-ai-size", type=int, default=10)
    ap.add_argument(
        "--slave-aor-size", type=int, default=0,
        help="Holding registers to mirror from slave into %%QW100+ (0 skips)"
    )
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    wait_for_web(base, timeout=60)

    sess = requests.Session()
    login(sess, base, args.user, args.password)

    state, current = dashboard_state(sess, base)
    program_ok = state == "running" and current == args.program_name

    if program_ok:
        print(f"OK: openplc already Running with program {current!r} -- program step no change")
    else:
        print(
            f"openplc state={state} current={current!r} -- "
            f"uploading + (re)starting with {args.program_name!r}"
        )
        upload_and_start(
            sess,
            base,
            args.program_name,
            args.program_description,
            args.program_file,
        )
        print(f"OK: openplc now Running with program {args.program_name!r}")

    # Slave device step. Only runs if the caller opted in via --slave-name.
    # Idempotency: query openplc.db directly for hr_read_size. If the row
    # exists AND all expected sizes match, skip. Otherwise DELETE + re-add
    # (add-modbus-device rejects duplicate dev_name, so we must delete
    # first to change config).
    if args.slave_name:
        if not args.slave_ip:
            raise SystemExit("--slave-ip required when --slave-name is set")

        current_di = _query_slave_dev_field(args.slave_name, "di_size")
        current_ai = _query_slave_dev_field(args.slave_name, "ir_size")
        current_aor = _query_slave_dev_field(args.slave_name, "hr_read_size")
        desired = (str(args.slave_di_size), str(args.slave_ai_size), str(args.slave_aor_size))
        found = (current_di, current_ai, current_aor)

        if found == desired:
            print(
                f"OK: slave device {args.slave_name!r} already configured "
                f"(di={current_di}, ai={current_ai}, aor={current_aor}) -- no change"
            )
        else:
            if current_di is not None:
                print(
                    f"slave device {args.slave_name!r} exists with "
                    f"(di={current_di}, ai={current_ai}, aor={current_aor}), "
                    f"desired {desired} -- delete + re-add"
                )
                _delete_slave_dev_row(args.slave_name)
            else:
                print(f"slave device {args.slave_name!r} not present -- configuring")
            add_slave_device(
                sess,
                base,
                name=args.slave_name,
                ip=args.slave_ip,
                port=args.slave_port,
                slave_id=args.slave_id,
                di_size=args.slave_di_size,
                ai_size=args.slave_ai_size,
                aor_size=args.slave_aor_size,
            )
            # The runtime must be cycled to pick up the new slave device.
            restart_runtime(sess, base)
            print(f"OK: slave device {args.slave_name!r} configured, runtime restarted")

    return 0


if __name__ == "__main__":
    sys.exit(main())
