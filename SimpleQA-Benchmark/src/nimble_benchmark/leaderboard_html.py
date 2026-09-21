"""Capture the fully-rendered Mintlify leaderboard as standalone HTML.

Workflow:

1. Generate the public MDX from a run directory into a temp scaffold.
2. Boot ``mintlify dev`` against the scaffold on a free port.
3. Wait for the dev server to compile the page.
4. Fetch the rendered HTML over HTTP.
5. Inline the ``/_next/static/css/*`` bundles so the artifact is viewable
   without the dev server (the styled tables, callouts, code blocks, and
   typography all need that CSS to look right).
6. Strip ``<script>`` tags — the SSR HTML already contains every cell,
   header, and chart we care about, and hydration scripts would 404
   against the missing ``/_next/static/chunks/*`` paths once the file is
   downloaded out of CI as an artifact.
7. Write the result to ``<run_dir>/leaderboard.html`` so it rides along
   with the existing ``runs/run_*/`` artifact upload in CI.

Used by ``make leaderboard-html`` to produce a publish-ready preview of the
leaderboard alongside each eval run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from nimble_benchmark.leaderboard import generate_public_docs_mdx

# A minimal standalone Mintlify site, just enough to render the leaderboard page.
# The nav must use the ``tabs[].pages[]`` shape that Mintlify's docs.json schema
# requires: under the older bare ``navigation.pages`` shorthand (dropped between
# mintlify CLI 4.2.575 and 4.2.579) the dev server does not register
# ``/web-api-leaderboard`` as a route, and the capture 404s until it times out.
SCAFFOLD_DOCS_JSON: dict = {
    "$schema": "https://mintlify.com/docs.json",
    "theme": "aspen",
    "name": "Nimble Eval Leaderboard",
    "colors": {"primary": "#edc602", "light": "#edc602", "dark": "#edc602"},
    "navigation": {
        "tabs": [
            {
                "tab": "Leaderboard",
                "pages": ["web-api-leaderboard"],
            }
        ]
    },
    "appearance": {"default": "dark", "strict": True},
    "styling": {"codeblocks": "system"},
}

PAGE_PATH = "/web-api-leaderboard"
# Mintlify dev's "preparing local preview" phase takes ~25-30s on a clean
# scaffold (npm-installed CLI, no warm cache), and Next.js compiles the
# route lazily on the first request once boot finishes. 180s leaves the
# real ceiling well inside CI's job timeout while absorbing the variance
# we saw between 30s (warm) and 90s (cold).
READY_TIMEOUT_S = 180
READY_POLL_INTERVAL_S = 0.5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nimble_benchmark.leaderboard_html")
    parser.add_argument("run_dir", type=Path, help="Analyzed run directory containing the eval artifacts.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Where to write the standalone HTML. Defaults to <run_dir>/leaderboard.html.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run mintlify dev on. Defaults to a free port chosen by the OS.",
    )
    parser.add_argument(
        "--keep-scripts",
        action="store_true",
        help="Keep <script> tags in the output. Default strips them so the artifact stays standalone.",
    )
    args = parser.parse_args(argv)

    if shutil.which("mintlify") is None:
        print(
            "error: mintlify CLI not found on PATH. Install it with `npm install -g mintlify`.",
            file=sys.stderr,
        )
        return 1

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"error: run dir {run_dir} does not exist", file=sys.stderr)
        return 1

    output = Path(args.output) if args.output else run_dir / "leaderboard.html"
    port = args.port if args.port else _pick_free_port()

    scaffold = Path(run_dir) / ".mintlify-scaffold"
    if scaffold.exists():
        shutil.rmtree(scaffold)
    scaffold.mkdir(parents=True)

    try:
        _write_scaffold(scaffold, run_dir)
        proc = _spawn_mintlify(scaffold, port)
        try:
            _wait_for_ready(port)
            html = _fetch_page(port, PAGE_PATH)
            html = _inline_css(html, port)
            if not args.keep_scripts:
                html = _strip_scripts(html)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(html, encoding="utf-8")
            print(f"Wrote {output} ({output.stat().st_size:,} bytes)")
            return 0
        finally:
            _shutdown(proc)
    finally:
        shutil.rmtree(scaffold, ignore_errors=True)


def _write_scaffold(scaffold: Path, run_dir: Path) -> None:
    (scaffold / "docs.json").write_text(json.dumps(SCAFFOLD_DOCS_JSON, indent=2), encoding="utf-8")
    generate_public_docs_mdx(run_dir, scaffold / "web-api-leaderboard.mdx")


def _spawn_mintlify(scaffold: Path, port: int) -> subprocess.Popen[bytes]:
    # ``preexec_fn=os.setsid`` puts the child in its own process group so we
    # can kill the whole tree on shutdown (mintlify spawns next.js as a
    # grandchild and a plain ``proc.kill()`` would orphan it). We keep
    # ``stderr`` on the parent's stream so a docs.json schema rejection or
    # port collision is visible in CI logs; ``stdout`` stays suppressed
    # because mintlify's spinner ANSI noise drowns out the eval output.
    print(f"Booting mintlify dev in {scaffold} on port {port}...")
    return subprocess.Popen(
        ["mintlify", "dev", "--port", str(port)],
        cwd=str(scaffold),
        stdout=subprocess.DEVNULL,
        stderr=None,
        start_new_session=True,
    )


def _wait_for_ready(port: int) -> None:
    """Block until the dev server responds with HTTP 200 to the page path.

    Mintlify's first request can take longer than the initial boot because
    Next.js compiles the route lazily; we poll the actual page URL (not
    just ``/``) so we know the MDX has finished rendering before we
    capture.
    """
    deadline = time.time() + READY_TIMEOUT_S
    url = f"http://127.0.0.1:{port}{PAGE_PATH}"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = exc
        time.sleep(READY_POLL_INTERVAL_S)
    raise RuntimeError(f"mintlify dev did not become ready within {READY_TIMEOUT_S}s; last error: {last_error}")


def _fetch_page(port: int, path: str) -> str:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


_STYLESHEET_PATTERN = re.compile(
    # Match both `rel="stylesheet"` and `rel="preload" as="style"` link tags.
    # ``href`` may appear before or after ``rel``/``as``, so capture loosely
    # and post-filter on the captured URL.
    r'<link\b[^>]*?href=["\']([^"\']+\.css(?:\?[^"\']*)?)["\'][^>]*?/?>',
    re.IGNORECASE,
)

_SCRIPT_PATTERN = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


def _inline_css(html: str, port: int) -> str:
    """Replace each ``/_next/static/css/*`` stylesheet link with an inline
    ``<style>`` so the saved HTML renders standalone.

    External CDN stylesheets (KaTeX, etc.) are left as-is — they load over
    the public internet and don't need the dev server.
    """
    base = f"http://127.0.0.1:{port}"
    seen: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        href = match.group(1)
        full = href if href.startswith(("http://", "https://")) else urllib.parse.urljoin(base + "/", href)
        if not full.startswith(base):
            # Skip external stylesheets; keep the original <link> tag so the
            # browser can fetch them at view time.
            return match.group(0)
        if full not in seen:
            try:
                with urllib.request.urlopen(full, timeout=15) as resp:
                    seen[full] = resp.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                print(f"warning: failed to inline {full}: {exc}", file=sys.stderr)
                return match.group(0)
        return f'<style data-inlined-from="{href}">\n{seen[full]}\n</style>'

    return _STYLESHEET_PATTERN.sub(replace, html)


def _strip_scripts(html: str) -> str:
    """Drop every ``<script>`` block. Hydration JS would 404 the
    ``/_next/static/chunks/*`` paths once the file is downloaded out of CI,
    and the SSR HTML already contains all the rendered table content.
    """
    return _SCRIPT_PATTERN.sub("", html)


def _shutdown(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM the whole group
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL fallback
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=5)


def _pick_free_port() -> int:
    """Ask the OS for an unused TCP port. We bind+close just to learn the
    number; mintlify will bind it immediately after so the race window is
    negligible in practice."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    raise SystemExit(main())
