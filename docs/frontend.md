# The frontend

The brain UI is a **React single-page app** in `frontend/`, built with Vite and
TypeScript. `scripts/serve.py` serves it from `frontend/dist`, which is
**committed** — see below.

```
frontend/
  src/
    api/          the typed client and the wire shapes — one way to reach the server
    components/   the component library: everything shared lives here
    features/     one directory per section, plus the shell they sit in
    hooks/        polling, one-shot fetches, debounce, shortcuts
    lib/          pure logic with no React in it — filters, physics, colours, formatting
    styles/       the design tokens everything paints with
    test/         fixtures, the mock server, and the render helpers
  dist/           the built app. IN git, deliberately. See below.
```

## The build ships, and stays in step

`frontend/dist` is committed, which is the opposite of the usual instinct about
generated files. It is committed because of a promise made one layer up: the
plugin says `python3 scripts/serve.py` gives you your brain in a browser, and
the people who install it have Python and often no node at all. A compiled SPA
and that promise can only both hold if the compiled SPA ships. Asking someone to
run `npm install` before they can read their own notes would be a worse product
than any argument about artifacts in git.

The real objection to a committed artifact is that it drifts from its source —
a UI change lands, the bundle is not rebuilt, and the repo quietly ships
behaviour nobody wrote. Three things answer it:

1. **CI rebuilds and diffs.** `npm run build` runs on every push, and the job
   fails if `frontend/dist` moved. A stale bundle cannot reach `main`.
2. **The server rebuilds locally.** On startup, before it binds the port,
   `scripts/serve.py` compares mtimes and rebuilds when your source is ahead of
   the bundle — so whoever is changing the UI sees the change without
   remembering a command.
3. **The suite checks the bundle itself.** `tests/test_contracts.py` asserts
   `index.html` is there, that every asset it references is on disk, and that
   the script parses (`node --check`). A half-committed build is the worst
   failure here: the server starts, the API answers, and the page is blank.

**Rebuild it in the same commit as the source change** (CONVENTIONS §4). CI will
tell you if you forget, but it is your commit that should carry it.

### When the server rebuilds

1. no `frontend/dist/index.html` → build;
2. any file in `frontend/src/`, or `package.json` / `package-lock.json` /
   `vite.config.ts` / `tsconfig.json` / `index.html`, more than two seconds
   newer than the bundle → rebuild;
3. `frontend/node_modules` missing → `npm ci` first (`npm install` when there is
   no lockfile).

A `*.test.tsx` edit is deliberately **not** a rebuild: tests never reach the
bundle, and rebuilding on every one would put a compile in front of every start
while someone is writing them.

The two-second margin is what keeps rule 2 from firing on a fresh clone: git
writes every file at one instant in index order, which puts `src/` after
`dist/`, so without a margin every checkout would compile its own committed
bundle before serving it. A real edit lands minutes after a build.

```bash
python3 scripts/serve.py              # rebuilds if your source moved, then serves
python3 scripts/serve.py --rebuild    # build even when it looks current
python3 scripts/serve.py --no-build   # never run npm; serve the committed bundle
```

**None of this is required to run the UI.** With no npm on the machine the
server serves the committed bundle exactly as it is; it only says, on the
terminal, that it could not refresh one it thought was stale. It never deletes
the bundle to force a rebuild, because on that machine the trade would be a
possibly-stale page for no page at all. Only a checkout with no
`frontend/dist` at all loses the page, and then it returns a 503 naming the
reason while every endpoint keeps answering.

## Working on it

```bash
cd frontend
npm install
npm run dev          # localhost:5173, proxying /api to a real serve.py on 8770
npm test             # every suite
npm run test:unit    # components, hooks, pure logic
npm run test:integration   # whole sections against a mock server
npm run typecheck
npm run build        # typecheck + build; this is what CI runs
```

`npm run dev` proxies `/api` straight to `scripts/serve.py`, so the dev server is
a hot-reloading front end over your **actual brain**, not a mock. Start the
backend in one terminal and Vite in another.

## How it is put together

**Nothing hard-codes vocabulary.** Realms, kinds, node types, edge relations,
task statuses and the two contract shapes all come from `/api/meta` at runtime.
A realm invented today is a filter chip on the next poll, with no code change. A
heading written into a component would be a second definition of CONVENTIONS
§5.1 — and the one nothing checks.

**Components are shared, not copied.** `src/components/` is the whole visual
vocabulary: one `Button` with variants rather than the eleven button styles the
old single-file page had drifted into, one `Dropdown` that closes on Escape and
on an outside click, one `Modal` that stacks and traps focus, one `Field` that
actually connects its label to its control. Features import from
`@/components`, never from a file path.

**The interesting logic is pure.** `src/lib/` holds the filter cascade, the force
simulation, the colour derivation and the date formatting, with no React and no
canvas in any of it. That is what makes them testable directly rather than
through the DOM — and it is where the tests that would actually catch a
regression live.

**The canvas does not re-render.** `GraphCanvas` runs the simulation and the
paint on one `requestAnimationFrame` loop against refs; React re-renders it only
when the data or the filters change. Pushing 800 nodes through the virtual DOM
sixty times a second is the one thing a canvas exists to avoid.

**The section is in the URL.** `/map`, `/dashboard`, `/skills`, `/settings`, on a
hash router. A reload lands where you were and a link to the dashboard is a link
to the dashboard — neither was true of the old page, which kept the section in a
body class. The hash means `serve.py` never has to learn about the app's routes.

## Testing

Two suites, both with vitest and Testing Library, and one mock server (MSW)
shared between them.

| Suite | What it covers |
|---|---|
| **unit** — `src/components`, `src/hooks`, `src/lib`, `src/api` | One thing at a time: does the cascade unwind a chain, does a facet say `4/9`, does polling ever overlap two requests, does an aborted fetch get reported as an error. |
| **integration** — `src/features` | A whole section, rendered against the mock server, driven the way a person drives it: switch the view, tick a chip, expand a card, complete a task, and assert on the write that actually reached the wire. |

The mock server is configured with `onUnhandledRequest: "error"`, so a component
that quietly calls an endpoint nobody stubbed fails the run rather than passing
silently. Its fixtures are the shapes `serve.py` really sends — they were read
off `brain_query._slim`, `graph.skill_trees` and `settings_payload`, and
`tests/test_openapi.py` keeps the spec those came from honest.

What the integration tests are actually for is the behaviour that is easy to
break and invisible when broken:

- a write is never sent for a value the person did not change (a change handler
  on a pre-filled field fires on a bare focus-and-blur);
- `unlink` sends the edge's real direction, not the one it happens to be
  rendered from;
- a relation the person re-pointed survives further typing in the composer;
- `--no-write` and "could not read `/api/meta`" are reported as the different
  things they are;
- a refused contract names the section that is missing, before the round trip.

## Accessibility and theming

The palette is the same one the old page used — this refactor changed how the UI
is built, not what it looks like. Tokens live in `src/styles/tokens.css` and
nothing outside that file names a hex value.

Every interactive element is a real control with a real accessible name, because
the tests drive the app by role and name and a control nobody can name is a
control nobody can test. `prefers-reduced-motion` is honoured.
