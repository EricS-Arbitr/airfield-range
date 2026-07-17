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


def login(sess: requests.Session, base: str, user: str, pw: str) -> None:
    """Form-login. Raises on obvious failure. The image redirects to
    /dashboard on success and re-renders /login with an error banner on
    failure (200). We check the presence of the login form in the response
    body to detect the second case.
    """
    r = sess.post(
        urljoin(base, "/login"),
        data={"username": user, "password": pw},
        allow_redirects=False,
        timeout=10,
    )
    if r.status_code == 302:
        return
    # 200 + login form still present == bad creds. Follow up:
    r2 = sess.get(urljoin(base, "/dashboard"), timeout=10, allow_redirects=False)
    if r2.status_code == 302 and "/login" in r2.headers.get("Location", ""):
        raise SystemExit(f"login failed: bad credentials for user {user!r}")
    # If /dashboard returns 200, session cookie was set even if login POST
    # returned a non-redirect status -- ok to proceed.


# --------------------------------------------------------------------------
# Dashboard scrape
# --------------------------------------------------------------------------

_RUNTIME_RUNNING_PAT = re.compile(
    r"runtime[^<]*?status.*?running", re.IGNORECASE | re.DOTALL,
)
_CURRENT_PROG_PAT = re.compile(
    r"currently\s+running[^<]*<[^>]+>\s*([^<]+?)\s*<", re.IGNORECASE | re.DOTALL,
)


def dashboard_state(sess: requests.Session, base: str) -> tuple[str, str | None]:
    """Return ('running'|'stopped', current_program_name_or_None)."""
    r = sess.get(urljoin(base, "/dashboard"), timeout=10)
    r.raise_for_status()
    text = r.text
    state = "running" if _RUNTIME_RUNNING_PAT.search(text) else "stopped"
    m = _CURRENT_PROG_PAT.search(text)
    current = m.group(1).strip() if m else None
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

    # Phase 3: compile. This is a streaming endpoint -- OpenPLC streams the
    # matiec + gcc log as it runs. We drain the stream to completion, then
    # check the response body for compile-success sentinel.
    r = sess.get(
        urljoin(base, f"/compile-program?file={prog_file}"),
        timeout=180,
        stream=True,
    )
    compile_log = []
    for chunk in r.iter_content(chunk_size=8192, decode_unicode=True):
        if chunk:
            compile_log.append(
                chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
            )
    log_text = "".join(compile_log)
    # Sentinel varies by fork -- accept either the classic
    # "Compilation finished successfully" or the fdamador "Compiling program"
    # + absence of gcc error markers.
    lower = log_text.lower()
    if "compilation finished successfully" not in lower and (
        "error:" in lower or "make: ***" in lower
    ):
        # Print only the last ~2 KB of the log for context
        tail = log_text[-2000:]
        raise SystemExit("compile failed. Tail of compile log:\n" + tail)

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
# main
# --------------------------------------------------------------------------

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
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    wait_for_web(base, timeout=60)

    sess = requests.Session()
    login(sess, base, args.user, args.password)

    state, current = dashboard_state(sess, base)
    if state == "running" and current == args.program_name:
        print(f"OK: openplc already Running with program {current!r} -- no change")
        return 0

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
