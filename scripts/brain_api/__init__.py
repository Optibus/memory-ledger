"""The brain's HTTP API, as a FastAPI app (docs/api.md).

`scripts/serve.py` is the entry point: it owns the environment, the UI build and
the reload loop, then runs `brain_api.app.create_app()` under uvicorn. Nothing
is imported here on purpose — `payloads` and `commands` are stdlib-only, so a
stdlib test can use them without the server's dependencies installed.
"""
