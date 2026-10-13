#!/usr/bin/env python3
"""Local neo4j in Docker, wired to the brain — one command, no model call.

    python3 scripts/neo4j_local.py up        # install/start + point the brain at it
    python3 scripts/neo4j_local.py status    # container + config + reachability
    python3 scripts/neo4j_local.py down      # stop it (stays down until `up`)

`up` is idempotent and does the whole job:

  1. creates the store under the brain home — `~/.second_brain/neo4j/{data,logs}`,
     a bind mount, so the graph is a directory you can back up and copy, not an
     opaque docker volume;
  2. runs `neo4j:5` with **--restart unless-stopped**, so it comes back after a
     reboot or a Docker restart but stays down when you deliberately stop it;
  3. binds to 127.0.0.1 only — the HTTP endpoint is cleartext and the brain is
     personal data (CONVENTIONS §2d, docs/neo4j.md "Security");
  4. generates a password once, stores it in `~/.second_brain/neo4j.json`
     (chmod 600), and **never prints it**;
  5. makes neo4j the default backend (`backend` setting), so the CLI reads it too
     rather than only `scripts/serve.py`;
  6. backfills the existing brain into it (`brain_sync.py`) and drains the outbox.

Nothing here is required to use the brain: SQLite stays the durable mirror and is
written first, always. This only sets up the master/mirror pair locally.

**It will not silently repoint an existing master.** If `neo4j.json` already
names a different host, `up` stops and asks for `--switch` (or `--keep-config` to
start the container and leave the config alone) — losing a configured remote
mirror by accident is exactly the kind of write-splitting this design avoids.

The password is passed to docker through the environment (`-e NEO4J_AUTH` with no
value), never as an argv value, so it stays out of the process list and out of
any transcript that records the command.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph  # noqa: E402  (same-dir engine import, as the other scripts do)

DEFAULT_NAME = "second-brain-neo4j"
DEFAULT_IMAGE = "neo4j:5"
DEFAULT_HTTP_PORT = 7474
DEFAULT_BOLT_PORT = 7687
DEFAULT_BIND = "127.0.0.1"
DEFAULT_HEAP = "512m"
DEFAULT_PAGECACHE = "256m"
WAIT_SECONDS = 180


# ---------------------------------------------------------------------------
# docker plumbing
# ---------------------------------------------------------------------------
def _docker_bin() -> str | None:
    """Docker Desktop on macOS is not always on a non-login shell's PATH."""
    return shutil.which("docker") or next(
        (p for p in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                     "/Applications/Docker.app/Contents/Resources/bin/docker")
         if Path(p).exists()), None)


class DockerError(RuntimeError):
    pass


def _run(bin_, args, env=None, stream=False, timeout=None):
    """Run docker. Returns stdout (stripped). Raises DockerError on failure."""
    cmd = [bin_, *args]
    if stream:                      # long pulls: let the user see progress
        rc = subprocess.call(cmd, env=env)
        if rc != 0:
            raise DockerError(f"`docker {' '.join(args)}` exited {rc}")
        return ""
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise DockerError((p.stderr or p.stdout or "").strip()
                          or f"`docker {' '.join(args)}` exited {p.returncode}")
    return p.stdout.strip()


def _inspect(bin_, name) -> dict | None:
    """Container state, or None when it does not exist."""
    try:
        out = _run(bin_, ["inspect", name], timeout=30)
    except DockerError:
        return None
    try:
        return json.loads(out)[0]
    except (ValueError, IndexError, KeyError):
        return None


def _daemon_up(bin_) -> bool:
    try:
        _run(bin_, ["info", "--format", "{{.ServerVersion}}"], timeout=30)
        return True
    except (DockerError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# paths, config, health
# ---------------------------------------------------------------------------
def store_dir() -> Path:
    """The graph store lives inside the brain home, beside graph.db."""
    return graph.home_dir() / "neo4j"


def _url(bind: str, port: int) -> str:
    host = "127.0.0.1" if bind in ("0.0.0.0", "", "::") else bind
    return f"http://{host}:{port}"


def _same_host(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _http_ok(url: str, timeout=3) -> bool:
    try:
        urllib.request.urlopen(url.rstrip("/") + "/", timeout=timeout).read()
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_ready(bin_, name: str, url: str, seconds: int) -> tuple[bool, str]:
    """Wait for neo4j to answer HTTP. Returns (ok, reason).

    Also watches the container: a bad bind mount makes neo4j exit and
    `--restart unless-stopped` then retries it forever, so waiting out the full
    timeout would hide the actual error. Bail on the first non-running state and
    hand back the log tail. (neo4j's first boot builds the store — that one is
    legitimately slow, so the timeout is generous.)
    """
    deadline = time.time() + seconds
    waited = 0
    while time.time() < deadline:
        if _http_ok(url):
            return True, ""
        info = _inspect(bin_, name) or {}
        status = ((info.get("State") or {}).get("Status") or "").lower()
        if status in ("restarting", "exited", "dead", "paused"):
            try:
                logs = _run(bin_, ["logs", "--tail", "12", name], timeout=30)
            except DockerError:
                logs = ""
            return False, f"the container is {status}:\n{logs}"
        time.sleep(2)
        waited += 2
        if waited % 20 == 0:
            print(f"   … still starting ({waited}s)")
    return False, f"no answer within {seconds}s"


def _write_config(url: str, password: str, db="neo4j", user="neo4j") -> dict:
    """Write <home>/neo4j.json (chmod 600), backing up anything different first.

    Same file and shape `scripts/serve.py` writes from the Settings page, so the two
    paths cannot drift.
    """
    f = graph.home_dir() / "neo4j.json"
    backup = None
    if f.exists():
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if old.get("url") and not _same_host(old["url"], url):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = f.with_name(f"neo4j.json.bak-{stamp}")
            backup.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                backup.chmod(0o600)
            except OSError:
                pass
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"url": url, "db": db, "user": user,
                             "password": password}, indent=2) + "\n",
                 encoding="utf-8")
    try:
        f.chmod(0o600)          # it holds a password
    except OSError:
        pass
    return {"file": str(f), "backup": str(backup) if backup else None}


def _dir_size(p: Path) -> str:
    try:
        total = float(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()))
    except OSError:
        return "size unknown"
    for unit in ("B", "KiB", "MiB"):
        if total < 1024:
            return f"{total:.0f} {unit}"
        total /= 1024
    return f"{total:.1f} GiB"


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------
def cmd_up(args) -> int:
    bin_ = _docker_bin()
    if not bin_:
        print("docker not found on PATH.\n"
              "  macOS:  brew install --cask docker   (then open Docker.app)\n"
              "  Linux:  https://docs.docker.com/engine/install/\n"
              "Nothing was changed — the brain keeps using SQLite.", file=sys.stderr)
        return 2
    if not _daemon_up(bin_):
        print("docker is installed but the daemon is not answering — start Docker "
              "Desktop (or `sudo systemctl start docker`) and re-run.", file=sys.stderr)
        return 2

    url = _url(args.bind, args.http_port)
    cur = graph._neo4j_cfg()
    env_keys = [k for k in ("SECOND_BRAIN_NEO4J_URL", "SECOND_BRAIN_NEO4J_PASSWORD")
                if os.environ.get(k)]
    write_config = not args.keep_config

    # Refuse to silently steal a configured master that is not this container.
    # A dry run still reports it — refusing to even show the plan would be silly.
    conflict = bool(write_config and cur["url"] and not _same_host(cur["url"], url)
                    and not args.switch)
    if conflict and not args.dry_run:
        print(f"the brain is already pointed at a neo4j master: {cur['url']}\n"
              f"This would repoint it at the local container ({url}).\n\n"
              "  --switch        repoint it (the old neo4j.json is backed up first)\n"
              "  --keep-config   start the container but leave the config alone\n\n"
              "Nothing was changed.", file=sys.stderr)
        return 3
    if write_config and env_keys:
        print("note: " + ", ".join(env_keys) + " is set in this shell and WINS over "
              "the file — unset it or the config written here has no effect.")

    store = store_dir()
    data, logs = store / "data", store / "logs"
    existing = _inspect(bin_, args.name)

    # The password is baked into the store on first boot, so it can only be
    # reused, supplied, or generated for a brand-new store.
    password = args.password or ""
    if not password and _same_host(cur["url"] or url, url) and cur["password"]:
        password = cur["password"]                     # same container as before
    if not password and existing:
        print(f"container `{args.name}` already exists but no password for it is on "
              "file — neo4j bakes the password into the store on first boot, so it "
              "cannot be re-derived.\n"
              "  • pass the one you set:   --password '<pw>'\n"
              f"  • or start over (DELETES the graph in {data}):\n"
              f"      {Path(bin_).name} rm -f {args.name} && rm -rf {store}\n"
              "The SQLite brain is untouched either way — a rebuilt container is "
              "refilled from it by the backfill step.", file=sys.stderr)
        return 3
    generated = not password
    if generated:
        password = secrets.token_urlsafe(18)

    if args.dry_run:
        print("\n".join([
            "DRY RUN — nothing was changed.",
            *([f"  BLOCKED      the brain already points at {cur['url']} — `up` will "
               "refuse\n               without --switch (repoint) or --keep-config "
               "(container only)"] if conflict else []),
            f"  store        {store}  (bind mount → /data, /logs)",
            f"  container    {args.name}" + ("  (exists — start + ensure restart policy)"
                                             if existing else "  (create)"),
            f"  image        {args.image}",
            f"  ports        {args.bind}:{args.http_port}→7474  "
            f"{args.bind}:{args.bolt_port}→7687",
            f"  restart      unless-stopped",
            f"  memory       heap {args.heap} · pagecache {args.pagecache}",
            f"  password     {'generate a new one' if generated else 'reuse the one on file'}"
            " (stored in neo4j.json, chmod 600, never printed)",
            f"  config       {'write ' + str(graph.home_dir() / 'neo4j.json') if write_config else 'left alone (--keep-config)'}",
            f"  backend      {('set to ' + args.default_backend) if write_config else 'left alone'}",
            f"  backfill     {'no (--no-sync)' if args.no_sync else 'brain_sync.py, then flush the outbox'}",
        ]))
        return 0

    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    print(f"store        {store}")

    denv = {**os.environ, "NEO4J_AUTH": f"neo4j/{password}"}
    try:
        if existing:
            # Adopt whatever is there: enforce the restart policy, then start it.
            _run(bin_, ["update", "--restart", "unless-stopped", args.name], timeout=60)
            running = bool((existing.get("State") or {}).get("Running"))
            if not running:
                _run(bin_, ["start", args.name], timeout=120)
            print(f"container    {args.name} ({'already running' if running else 'started'})"
                  " · restart=unless-stopped")
        else:
            try:
                _run(bin_, ["image", "inspect", args.image], timeout=60)
            except DockerError:
                print(f"pulling {args.image} (first run only, ~600 MB)…")
                try:
                    _run(bin_, ["pull", args.image], stream=True)
                except DockerError as e:
                    raise DockerError(
                        f"could not pull {args.image}: {e}\n"
                        "If this is a registry TLS/proxy error, the image has to come "
                        "from somewhere your docker can reach — a mirror, a VPN, or "
                        "`docker load` of a saved tar from a machine that can pull it:\n"
                        f"  docker save {args.image} | ssh <other-host> docker load"
                    ) from None
            run_args = [
                "run", "-d",
                "--name", args.name,
                "--restart", "unless-stopped",
                "-p", f"{args.bind}:{args.http_port}:7474",
                "-p", f"{args.bind}:{args.bolt_port}:7687",
                "-v", f"{data}:/data",
                "-v", f"{logs}:/logs",
                # value-less -e: docker reads it from our environment, so the
                # password never appears in argv or the process list
                "-e", "NEO4J_AUTH",
                "-e", f"NEO4J_server_memory_heap_max__size={args.heap}",
                "-e", f"NEO4J_server_memory_pagecache_size={args.pagecache}",
            ]
            if os.name != "nt":
                # bind-mounted /data is owned by the host user; the image's neo4j
                # user (uid 7474) cannot write it unless we run as ourselves
                run_args += ["--user", f"{os.getuid()}:{os.getgid()}"]
            run_args += [args.image]
            _run(bin_, run_args, env=denv, timeout=300)
            print(f"container    {args.name} created · restart=unless-stopped · "
                  f"{args.bind}:{args.http_port} (HTTP) {args.bind}:{args.bolt_port} (Bolt)")
    except DockerError as e:
        print(f"docker failed: {e}\n"
              f"logs: {Path(bin_).name} logs {args.name}", file=sys.stderr)
        return 1

    print(f"waiting for neo4j on {url} (up to {args.wait}s)…")
    ok, why = _wait_ready(bin_, args.name, url, args.wait)
    if not ok:
        print(f"neo4j is not answering on {url} — {why}\n", file=sys.stderr)
        if "not accessible for user" in why or "permissions" in why:
            print("That is the bind mount: the store directory has to be readable by "
                  "the docker VM.\n"
                  f"  • is {store} inside a path Docker Desktop is allowed to share? "
                  "(Settings › Resources › File sharing — your home dir is shared by "
                  "default, a 0700 dir under /tmp usually is not)\n"
                  "  • $SECOND_BRAIN_HOME pointing somewhere exotic is the usual cause.",
                  file=sys.stderr)
        print(f"full logs: {Path(bin_).name} logs {args.name}\n"
              "`up` is idempotent — fix the cause and re-run. Config was NOT written, "
              "so the brain is still reading what it read before.", file=sys.stderr)
        return 1
    print("neo4j        up")

    if write_config:
        res = _write_config(url, password)
        print(f"config       {res['file']} (chmod 600"
              + (", password generated" if generated else "") + ")")
        if res["backup"]:
            print(f"             previous master backed up → {res['backup']}")
        graph.save_settings({"backend": args.default_backend})
        print(f"backend      default read backend = {args.default_backend}"
              f" ({graph.settings_path()})")
        ok, err = graph._neo4j_call([{"statement": "RETURN 1"}])
        if not ok:
            print(f"auth check failed: {err}\n"
                  "The container is up but the brain cannot authenticate to it.",
                  file=sys.stderr)
            return 1
        print("auth         ok")
    else:
        print("config       left alone (--keep-config)")

    if args.no_sync:
        print("backfill     skipped (--no-sync)")
    elif not write_config:
        print("backfill     skipped (the brain is not pointed at this container)")
    else:
        print("backfill     pushing the existing brain into neo4j…")
        sync = subprocess.run([sys.executable, str(Path(__file__).parent / "brain_sync.py")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (sync.stdout or sync.stderr or "").strip().splitlines()
        for line in tail[-6:]:
            print("             " + line)
        if sync.returncode != 0:
            print("backfill failed — the container is up and configured; re-run "
                  "`python3 scripts/brain_sync.py` to retry.", file=sys.stderr)
            return 1
        flushed = graph._flush(graph.connect())
        print(f"outbox       {'drained' if flushed.get('ok') else 'still pending: ' + str(flushed)}")

    print("\nready.  browser http://127.0.0.1:%d  ·  stop it with "
          "`python3 scripts/neo4j_local.py down`" % args.http_port)
    return 0


# ---------------------------------------------------------------------------
# down / status
# ---------------------------------------------------------------------------
def cmd_down(args) -> int:
    bin_ = _docker_bin()
    if not bin_ or not _daemon_up(bin_):
        print("docker is not answering — nothing to stop.", file=sys.stderr)
        return 2
    if not _inspect(bin_, args.name):
        print(f"no container named `{args.name}`.")
        return 0
    try:
        _run(bin_, ["stop", args.name], timeout=120)
    except DockerError as e:
        print(f"docker failed: {e}", file=sys.stderr)
        return 1
    print(f"stopped {args.name}. `--restart unless-stopped` means a deliberate stop "
          "sticks — it will NOT come back on reboot until you run `up` again.")
    if args.remove:
        try:
            _run(bin_, ["rm", args.name], timeout=120)
            print(f"removed the container. The graph is still on disk in {store_dir()} — "
                  "`up` recreates the container against it (same password from "
                  "neo4j.json).")
        except DockerError as e:
            print(f"could not remove it: {e}", file=sys.stderr)
            return 1
    if graph.setting("backend") in ("neo4j",):
        print("note: the default backend is `neo4j`, so CLI reads will now fail. "
              "Set it to `auto` (falls back to the SQLite mirror) in the UI's "
              "Settings › Defaults, or run:\n"
              "  python3 -c \"import sys;sys.path.insert(0,'scripts');"
              "import graph;graph.save_settings({'backend':'auto'})\"")
    else:
        print("reads fall back to the local SQLite mirror; writes queue in the "
              "outbox and drain on the next `up`.")
    return 0


def cmd_status(args) -> int:
    bin_ = _docker_bin()
    store = store_dir()
    print(f"store        {store} ({'exists, ' + _dir_size(store) if store.exists() else 'not created'})")
    if not bin_:
        print("docker       not installed")
    elif not _daemon_up(bin_):
        print("docker       installed, daemon not answering")
    else:
        info = _inspect(bin_, args.name)
        if not info:
            print(f"container    `{args.name}` does not exist — run `up`")
        else:
            state = info.get("State") or {}
            host = info.get("HostConfig") or {}
            policy = (host.get("RestartPolicy") or {}).get("Name") or "no"
            ports = (info.get("NetworkSettings") or {}).get("Ports") or {}
            binding = ", ".join(
                f"{b[0].get('HostIp')}:{b[0].get('HostPort')}→{k}"
                for k, b in sorted(ports.items()) if b)
            print(f"container    {args.name} · {state.get('Status')} · restart={policy}")
            print(f"image        {(info.get('Config') or {}).get('Image')}")
            if binding:
                print(f"ports        {binding}")
    cfg = graph._neo4j_cfg()
    local = _url(args.bind, args.http_port)
    if not cfg["url"]:
        print("config       none — the brain is SQLite-only")
    else:
        which = "this container" if _same_host(cfg["url"], local) else "a DIFFERENT host"
        print(f"config       {cfg['url']} ({which}) · password "
              f"{'set' if cfg['password'] else 'MISSING'}")
        print(f"reachable    {'yes' if graph._neo4j_up() else 'no'}")
    print(f"backend      default read backend = {graph.setting('backend') or 'sqlite'}")
    con = graph.connect()
    print(f"outbox       {con.execute('SELECT COUNT(*) c FROM outbox').fetchone()['c']}"
          " queued write(s)")
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="neo4j_local.py", description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--name", default=DEFAULT_NAME, help=f"container name (default {DEFAULT_NAME})")
        sp.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT,
                        help=f"host port for neo4j HTTP (default {DEFAULT_HTTP_PORT})")
        sp.add_argument("--bind", default=DEFAULT_BIND,
                        help="host address to bind (default 127.0.0.1 — localhost only)")

    up = sub.add_parser("up", help="install/start the container and wire the brain to it")
    common(up)
    up.add_argument("--bolt-port", type=int, default=DEFAULT_BOLT_PORT,
                    help=f"host port for Bolt (default {DEFAULT_BOLT_PORT})")
    up.add_argument("--image", default=DEFAULT_IMAGE, help=f"docker image (default {DEFAULT_IMAGE})")
    up.add_argument("--heap", default=DEFAULT_HEAP, help=f"JVM max heap (default {DEFAULT_HEAP})")
    up.add_argument("--pagecache", default=DEFAULT_PAGECACHE,
                    help=f"page cache (default {DEFAULT_PAGECACHE})")
    up.add_argument("--password", help="use this password instead of generating/reusing one")
    up.add_argument("--switch", action="store_true",
                    help="repoint the brain even if neo4j.json names another host")
    up.add_argument("--keep-config", action="store_true",
                    help="start the container but do not touch neo4j.json / settings")
    up.add_argument("--default-backend", choices=["auto", "neo4j", "sqlite"], default="auto",
                    help="the `backend` setting to write (default auto: neo4j when "
                         "reachable, SQLite mirror when not)")
    up.add_argument("--no-sync", action="store_true", help="skip the backfill")
    up.add_argument("--wait", type=int, default=WAIT_SECONDS,
                    help=f"seconds to wait for neo4j to answer (default {WAIT_SECONDS})")
    up.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    up.set_defaults(func=cmd_up)

    down = sub.add_parser("down", help="stop the container (it stays stopped)")
    common(down)
    down.add_argument("--remove", action="store_true",
                      help="also delete the container (the graph on disk survives)")
    down.set_defaults(func=cmd_down)

    st = sub.add_parser("status", help="container + config + reachability + outbox")
    common(st)
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
