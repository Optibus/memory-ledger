#!/usr/bin/env python3
"""The OpenAPI document and the server it describes cannot drift apart.

This is the whole reason `scripts/openapi.py` is a Python module rather than a
checked-in YAML file. A spec written beside a router is a SECOND description of
the same surface, and the second description is the one that rots: an endpoint
gets added to the dispatch table, the doc is "updated later", and from then on
the published contract quietly lies.

So this suite reads serve.py's ACTUAL routes out of its source — the `path ==`
chain for reads, the dispatch dict for writes — and fails when either side holds
an endpoint the other has never heard of.

    python3 tests/test_openapi.py

Stdlib only, like every suite here.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import openapi  # noqa: E402

SERVE = ROOT / "scripts" / "serve.py"


# ---------------------------------------------------------------------------
# Reading the server's real route table, without importing it
#
# serve.py imports graph and brain_query and would touch a real brain, so this
# parses the source instead. It is also the more honest check: it reads what the
# dispatcher is written to do, not what a module-level constant claims.
# ---------------------------------------------------------------------------

def _tree() -> ast.Module:
    return ast.parse(SERVE.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"serve.py has no {name} — this test needs rewriting")


def get_paths() -> set[str]:
    """Every `/api/...` literal do_GET compares the request path against."""
    found = set()
    for node in ast.walk(_function(_tree(), "do_GET")):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id == "path"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                if comparator.value.startswith("/api/"):
                    found.add(comparator.value)
            elif isinstance(comparator, (ast.Tuple, ast.List)):
                for element in comparator.elts:
                    if isinstance(element, ast.Constant) and str(element.value).startswith("/api/"):
                        found.add(element.value)
    return found


def post_paths() -> set[str]:
    """The keys of do_POST's dispatch dict."""
    for node in ast.walk(_function(_tree(), "do_POST")):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            keys = [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if keys and all(k.startswith("/api/") for k in keys):
                return set(keys)
    raise AssertionError("serve.py's do_POST no longer holds a dict of route -> handler")


class TestRoutesAreDocumented(unittest.TestCase):
    """Neither side may grow an endpoint the other has never heard of."""

    def test_every_get_route_is_in_the_spec(self):
        missing = get_paths() - set(openapi.GET_ROUTES)
        self.assertEqual(
            missing, set(),
            "serve.py answers GET routes the OpenAPI document does not describe. "
            "Add them to openapi.GET_ROUTES in the SAME change (CONVENTIONS §4): "
            f"{sorted(missing)}")

    def test_the_spec_describes_no_get_route_that_does_not_exist(self):
        # `/api/{view}` is the one documented path with no literal in the source:
        # the date views are dispatched by name off the tail of the path.
        extra = set(openapi.GET_ROUTES) - get_paths()
        self.assertEqual(
            extra, set(),
            "The OpenAPI document promises GET routes serve.py does not answer. "
            f"A client following the spec would get a 404: {sorted(extra)}")

    def test_every_post_route_is_in_the_spec(self):
        missing = post_paths() - set(openapi.POST_ROUTES)
        self.assertEqual(
            missing, set(),
            "serve.py accepts writes the OpenAPI document does not describe: "
            f"{sorted(missing)}")

    def test_the_spec_describes_no_post_route_that_does_not_exist(self):
        extra = set(openapi.POST_ROUTES) - post_paths()
        self.assertEqual(
            extra, set(),
            "The OpenAPI document promises writes serve.py would answer with a 404: "
            f"{sorted(extra)}")

    def test_the_dashboard_views_are_documented_as_one_path(self):
        doc = openapi.build()
        self.assertIn(openapi.VIEW_PATH, doc["paths"])
        enum = doc["paths"][openapi.VIEW_PATH]["get"]["parameters"][0]["schema"]["enum"]
        import brain_query
        self.assertEqual(
            sorted(enum), sorted(brain_query.VIEWS),
            "The documented view names and brain_query.VIEWS have drifted apart.")


class TestTheDocumentIsValid(unittest.TestCase):
    """Not a full OpenAPI validator — the things that actually break a reader."""

    @classmethod
    def setUpClass(cls):
        cls.doc = openapi.build()

    def test_it_is_json_serialisable(self):
        # It is served over the wire, so a value json cannot encode is an outage.
        json.dumps(self.doc)

    def test_it_declares_a_version_and_a_title(self):
        self.assertEqual(self.doc["openapi"], "3.1.0")
        self.assertTrue(self.doc["info"]["title"])
        self.assertTrue(self.doc["info"]["version"])

    def test_every_operation_has_a_summary_and_a_unique_id(self):
        seen = {}
        for path, item in self.doc["paths"].items():
            for method, op in item.items():
                where = f"{method.upper()} {path}"
                self.assertTrue(op.get("summary"), f"{where} has no summary")
                op_id = op.get("operationId")
                self.assertTrue(op_id, f"{where} has no operationId")
                self.assertNotIn(
                    op_id, seen,
                    f"operationId {op_id!r} is used by both {seen.get(op_id)} and {where}; "
                    "generated clients would collide")
                seen[op_id] = where

    def test_every_operation_documents_a_200(self):
        for path, item in self.doc["paths"].items():
            for method, op in item.items():
                self.assertIn("200", op["responses"],
                              f"{method.upper()} {path} documents no success response")

    def test_every_write_documents_the_no_write_403(self):
        # --no-write is a documented mode, so a client has to know 403 is a
        # configuration answer and not an authentication one.
        for path in openapi.POST_ROUTES:
            op = self.doc["paths"][path]["post"]
            self.assertIn("403", op["responses"],
                          f"POST {path} does not document the --no-write refusal")

    def test_every_write_requires_a_body(self):
        for path in openapi.POST_ROUTES:
            op = self.doc["paths"][path]["post"]
            self.assertTrue(op["requestBody"]["required"],
                            f"POST {path} is documented as accepting no body")

    def test_every_schema_reference_resolves(self):
        schemas = set(self.doc["components"]["schemas"])
        missing = set()

        def walk(node):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    name = ref.rsplit("/", 1)[-1]
                    if name not in schemas:
                        missing.add(ref)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.doc)
        self.assertEqual(missing, set(), f"dangling $ref: {sorted(missing)}")

    def test_every_schema_is_actually_used(self):
        """An orphan schema is a leftover from an endpoint that already changed."""
        used = set()

        def walk(node):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    used.add(ref.rsplit("/", 1)[-1])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.doc["paths"])
        walk(self.doc["components"]["schemas"])
        orphans = set(self.doc["components"]["schemas"]) - used
        self.assertEqual(orphans, set(), f"schemas nothing references: {sorted(orphans)}")

    def test_every_tag_used_is_declared(self):
        declared = {t["name"] for t in self.doc["tags"]}
        used = set()
        for item in self.doc["paths"].values():
            for op in item.values():
                used.update(op.get("tags", []))
        self.assertEqual(used - declared, set(),
                         f"tags used but never described: {sorted(used - declared)}")

    def test_it_warns_about_the_unauthenticated_write_api(self):
        # The one thing a reader must not miss. If this ever gets edited out, the
        # document starts reading like a service that is safe to expose.
        text = self.doc["info"]["description"]
        self.assertIn("unauthenticated", text.lower())
        self.assertIn("127.0.0.1", text)
        self.assertIn("--no-write", text)


class TestTheVocabularyIsNotHardcodedTwice(unittest.TestCase):
    """What the engine owns, the document must not re-state from memory."""

    def test_the_documented_task_types_match_what_the_server_accepts(self):
        body = openapi.POST_ROUTES["/api/add-task"]["body"]
        self.assertEqual(body["properties"]["type"]["enum"], ["task", "subtask"],
                         "add-task's documented types no longer match §2g")

    def test_the_import_modes_match_the_three_the_ui_offers(self):
        body = openapi.POST_ROUTES["/api/import-bundle"]["body"]
        modes = body["properties"]["select"]["additionalProperties"]["enum"]
        self.assertEqual(sorted(modes), ["ignore", "replace", "update"])


class TestTheServerCanServeIt(unittest.TestCase):
    def test_the_swagger_page_points_at_the_local_document(self):
        self.assertIn('url: "/api/openapi.json"', openapi.SWAGGER_HTML)

    def test_the_swagger_page_says_what_to_do_when_the_cdn_is_unreachable(self):
        # The API is local and offline-capable; only the viewer is remote, and a
        # blank page would suggest otherwise.
        self.assertIn("curl", openapi.SWAGGER_HTML)
        self.assertIn("openapi.json", openapi.SWAGGER_HTML)

    def test_serve_py_answers_both_documentation_routes(self):
        source = SERVE.read_text(encoding="utf-8")
        self.assertIn('"/api/openapi.json"', source)
        self.assertIn('"/api/docs"', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
