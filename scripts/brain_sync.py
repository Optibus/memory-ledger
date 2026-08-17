#!/usr/bin/env python3
"""brain-sync — push the local memory-ledger graph into a neo4j master.

Reads the local SQLite graph (via the graph engine) and upserts every node and
edge into neo4j using its HTTP transactional Cypher endpoint (stdlib only — no
driver needed). Idempotent: MERGE on id, so re-running just updates.

Config (endpoint + creds) is read from the PRIVATE brain home, never the repo:
  ~/.second_brain/neo4j.json  ->  {"url": "...", "db": "neo4j", "user": "...", "password": "..."}
or env: SECOND_BRAIN_NEO4J_URL / _DB / _USER / _PASSWORD.

Usage:
  python3 scripts/brain_sync.py --dry-run     # show what would be sent, push nothing
  python3 scripts/brain_sync.py               # real sync (needs creds)

Note: if the endpoint is plain HTTP, data + credentials travel in cleartext.
Add TLS (reverse proxy / tunnel) before treating this as secure.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph  # noqa: E402


def load_cfg() -> dict:
    cfg = {}
    f = graph.home_dir() / "neo4j.json"
    if f.exists():
        cfg = json.loads(f.read_text())
    return {
        # Via graph._env(), not os.environ, so both the canonical MEMORY_LEDGER_*
        # and the legacy SECOND_BRAIN_* prefixes resolve here exactly as they do
        # in the engine — the same credentials must not depend on which file
        # happens to read them.
        "url": graph._env("NEO4J_URL", cfg.get("url", "")),
        "db": graph._env("NEO4J_DB", cfg.get("db", "neo4j")),
        "user": graph._env("NEO4J_USER", cfg.get("user", "neo4j")),
        "password": graph._env("NEO4J_PASSWORD", cfg.get("password", "")),
    }


def _clean_props(props: dict) -> dict:
    """neo4j props must be primitives/arrays — JSON-encode anything nested."""
    out = {}
    for k, v in (props or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        else:
            out[k] = json.dumps(v, ensure_ascii=False)
    return out


def collect():
    con = graph.connect()
    nodes = []
    for r in con.execute("SELECT * FROM nodes").fetchall():
        d = graph._node_dict(r)
        nodes.append({
            "id": d["id"], "type": d["type"], "title": d["title"],
            "body": d["body"], "status": d["status"], "due": d["due"],
            "created": d["created"], "updated": d["updated"],
            "realm": d.get("realm") or "optibus",
            "props": _clean_props(d["props"]),
        })
    edges = [{"src": e["src"], "dst": e["dst"], "rel": e["rel"]}
             for e in con.execute("SELECT src,dst,rel FROM edges").fetchall()]
    return nodes, edges


NODE_CYPHER = (
    "UNWIND $nodes AS n MERGE (x:Node {id: n.id}) "
    "SET x += n.props, x.realm=n.realm, x.type=n.type, x.title=n.title, "
    "x.body=n.body, x.status=n.status, x.due=n.due, "
    "x.created=n.created, x.updated=n.updated"
)
EDGE_CYPHER = (
    "UNWIND $edges AS e MATCH (a:Node {id:e.src}), (b:Node {id:e.dst}) "
    "MERGE (a)-[r:REL {rel:e.rel}]->(b)"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    nodes, edges = collect()
    print(f"local graph: {len(nodes)} nodes, {len(edges)} edges")

    if args.dry_run:
        print(f"endpoint: {cfg['url'] or '(unset)'}  db: {cfg['db']}  user: {cfg['user']}")
        print("cypher (nodes):", NODE_CYPHER)
        print("cypher (edges):", EDGE_CYPHER)
        print("sample node:", json.dumps(nodes[0], ensure_ascii=False)[:300] if nodes else "-")
        print("DRY RUN — nothing sent.")
        return

    if not cfg["url"] or not cfg["password"]:
        print("ERROR: set url + password in ~/.second_brain/neo4j.json or env "
              "(SECOND_BRAIN_NEO4J_URL / _PASSWORD).", file=sys.stderr)
        sys.exit(2)
    if cfg["url"].startswith("http://"):
        print("WARNING: endpoint is plain HTTP — creds + data go in cleartext.",
              file=sys.stderr)

    endpoint = cfg["url"].rstrip("/") + f"/db/{cfg['db']}/tx/commit"
    payload = {"statements": [
        {"statement": NODE_CYPHER, "parameters": {"nodes": nodes}},
        {"statement": EDGE_CYPHER, "parameters": {"edges": edges}},
    ]}
    auth = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as e:
        print(f"ERROR calling neo4j: {e}", file=sys.stderr)
        sys.exit(1)
    errs = body.get("errors") or []
    if errs:
        print("neo4j errors:", json.dumps(errs, indent=2), file=sys.stderr)
        sys.exit(1)
    print(f"synced OK -> {cfg['url']} (per-node realm): {len(nodes)} nodes, {len(edges)} edges upserted")


if __name__ == "__main__":
    main()
