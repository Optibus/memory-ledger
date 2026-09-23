# Developer documentation

The [main README](../README.md) is the tour: what the brain is and how to use it.
These pages are the reference: how it works and how to change it.

| Page | Read it for |
|---|---|
| [algorithm.md](algorithm.md) | capture, memory strength, spreading activation, Hebbian wiring, consolidation, the genome |
| [queries.md](queries.md) | every read path, its flags, and which one to reach for |
| [search.md](search.md) | the FTS5 index, cue construction, budgets, sharp edges |
| [api.md](api.md) | the local HTTP server — the FastAPI app, its envelope, its venv, and how to extend it. Its interactive reference is `/api/docs` on a running server, generated from the routes |
| [frontend.md](frontend.md) | the React single-page app — layout, why its build is committed, how to work on it, and what the two test suites cover |
| [performance.md](performance.md) | measured latency, storage, growth projections, scaling, known limits |
| [neo4j.md](neo4j.md) | running a real graph database instead of the SQLite mirror — `neo4j_local.py up` (Docker, one command), the `backend` setting, and whether it is worth it |
| [comparison.md](comparison.md) | how this compares to MemPalace and other second brains, and measured SQLite vs neo4j vs ChromaDB |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | setup, layout, how to add a node type / command / endpoint / skill |

Also: [`CONVENTIONS.md`](../CONVENTIONS.md) is not developer documentation — it is
the operating contract every skill loads at runtime. Changing it changes behaviour.

## The one-paragraph version

Every interaction is written to a local SQLite graph (`nodes`, `edges`, and an
FTS5 index) by three hooks that never call a model. Retrieval seeds on full-text
search, spreads activation across edges with distance decay and fan-out
normalisation, reranks by a memory strength that rises with use and decays with
disuse, and returns a budgeted snippet payload. Nodes that keep co-activating get
wired together after the third repetition. Nothing is ever deleted — decay
changes rank, not existence. Optionally the whole graph mirrors to neo4j, where
SQLite becomes the local cache and neo4j the master.
