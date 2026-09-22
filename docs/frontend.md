# The frontend

The brain UI is a **React single-page app** in `frontend/`, built with Vite and
TypeScript, served by `scripts/serve.py` from the committed `frontend/dist`.

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
  dist/           the BUILT app. Committed. See below.
```

## Why the build output is committed

The plugin's promise is that `python3 scripts/serve.py` gives you the UI. Someone
who installs memory-ledger has Python — they may well not have node, and asking
them to run `npm install` before they can see their own notes would be a worse
product.

A compiled SPA and that promise can only both hold if the compiled artifact
ships. So `frontend/dist` is in git, `.gitignore` carries an explicit exception
saying why, and two checks keep it honest:

- `tests/test_contracts.py` asserts the bundle is present, that its entry point
  references assets that exist, and that it parses. A half-committed build is the
  worst failure mode here: the server starts, the API answers, and the person
  gets a blank page.
- CI rebuilds it and fails if the working tree moved, so a source change that was
  never rebuilt cannot merge.

**Rebuild it in the same commit as the source change that caused it:**

```bash
cd frontend && npm run build
```

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
