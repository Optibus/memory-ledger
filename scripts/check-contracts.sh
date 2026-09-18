#!/usr/bin/env bash
# second-brain — skill contract + dependency-tree enforcement (CONVENTIONS §5, §5a).
# Run by .githooks/pre-commit and standalone (./scripts/check-contracts.sh).
#
#   1. CONTRACT SHAPE (hard) — every skills/*/SKILL.md declares the §5.1 sections, in order:
#      When to use · Given · Rules honored · Depends on · Deliverables · Judged against ·
#      Guardrails — must not · Reply (focus contract).
#   2. RULES HONORED (hard) — that section names the match-rules call (§5.2). A skill that
#      does not ask the genome is a skill that has forked it.
#   3. DEPENDENCY DECLARATIONS (hard) — "## Depends on" declares Requires, Reads and Writes;
#      "none" is written explicitly; named skills exist; optional kinds are ordered|enriching.
#   4. TREE AGREEMENT (hard) — every skill appears in docs/skill-dependencies.md. The document
#      and the declarations are two views of one graph (§5a); a skill missing from it is invisible.
#   5. CYCLES (hard) — the requires-graph is acyclic.
#   6. SMELL SWEEP (hard) — the §5.3 case-4 smells: a surviving "## Steps" heading, an
#      "the agent will: 1..." narration, or a hardcoded rule body instead of a rule id.
#
# Escape hatch for a deliberate exception: SECOND_BRAIN_SKIP_CONTRACTS=1.
set -u

root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$root" || exit 0

if [ "${SECOND_BRAIN_SKIP_CONTRACTS:-}" = "1" ]; then
  echo "✓ contract checks (skipped via SECOND_BRAIN_SKIP_CONTRACTS)"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-contracts: python3 not found — skipping" >&2
  exit 0
fi

python3 - <<'PY'
import glob, os, re, sys

fail = []
TREE = 'docs/skill-dependencies.md'

REQUIRED = ['## When to use', '## Given', '## Rules honored', '## Depends on',
            '## Deliverables', '## Judged against', '## Guardrails — must not',
            '## Reply (focus contract)']

skills = {os.path.basename(os.path.dirname(f)): f for f in sorted(glob.glob('skills/*/SKILL.md'))}
if not skills:
    print("CHECK FAILED: no skills found"); sys.exit(1)

tree_text = open(TREE).read() if os.path.exists(TREE) else ''
if not tree_text:
    fail.append(f"{TREE} is missing — the tree is the source of truth for how skills hand off (§5a)")

requires_graph = {}

for sid, path in skills.items():
    text = open(path).read()
    body = text.split('---', 2)[2] if text.count('---') >= 2 else text
    heads = [h for h in re.findall(r'^## .*$', body, re.M)]

    # 1. Contract shape — present AND in order.
    missing = [h for h in REQUIRED if not any(x.startswith(h) for x in heads)]
    if missing:
        fail.append(f"{path}: missing contract section(s) {', '.join(missing)} (§5.1)")
    else:
        seen = [h for h in heads if any(h.startswith(r) for r in REQUIRED)]
        order = [next(i for i, r in enumerate(REQUIRED) if h.startswith(r)) for h in seen]
        if order != sorted(order):
            fail.append(f"{path}: contract sections are out of order (§5.1) — expected "
                        + " → ".join(r[3:] for r in REQUIRED))

    # 2. Rules honored — it must actually ask the genome.
    rm = re.search(r'^## Rules honored\s*$(.*?)(?=^## )', body, re.M | re.S)
    if rm and 'match-rules' not in rm.group(1):
        fail.append(f"{path}: '## Rules honored' never calls match-rules — a skill that does not "
                    f"ask the genome has forked it (§5.2)")

    # 3. Depends on block.
    m = re.search(r'^## Depends on\s*$(.*?)(?=^## )', body, re.M | re.S)
    if not m:
        requires_graph[sid] = set()
        continue
    block = m.group(1)

    for label in ('Requires', 'Reads', 'Writes'):
        if not re.search(r'\*\*' + label + r':\*\*', block):
            fail.append(f"{path}: '## Depends on' has no **{label}:** line — write 'none' "
                        f"explicitly; silence is never a declaration (§5a)")

    for bad in re.findall(r'\*\*Optional(?! \((?:ordered|enriching)\))[^:]*:\*\*', block):
        fail.append(f"{path}: {bad.strip('*: ')} — use 'Optional (ordered)' or "
                    f"'Optional (enriching)' (§5a)")

    deps = set()
    for line in block.splitlines():
        lm = re.match(r'\s*-\s*\*\*(Requires|Optional \((?:ordered|enriching)\)):\*\*(.*)', line)
        if not lm:
            continue
        kind, rest = lm.group(1), lm.group(2)
        if re.match(r'\s*none\b', rest, re.I):
            continue
        for n in set(re.findall(r'`([a-z][a-z0-9-]*)`', rest)):
            if n not in skills:
                fail.append(f"{path}: '{kind}' names unknown skill '{n}' (§5a)")
            elif kind == 'Requires':
                deps.add(n)
    requires_graph[sid] = deps

    # 4. Tree agreement.
    if tree_text and not re.search(r'`' + re.escape(sid) + r'`', tree_text):
        fail.append(f"{sid}: absent from {TREE} — a skill not in the tree is invisible to the "
                    f"rest of the brain (§5a)")

# 5. Cycle detection over the hard requires-graph.
WHITE, GREY, BLACK = 0, 1, 2
state = {k: WHITE for k in requires_graph}

def visit(n, stack):
    state[n] = GREY
    for d in sorted(requires_graph.get(n, ())):
        if d not in state:
            continue
        if state[d] == GREY:
            cyc = stack[stack.index(d):] + [d] if d in stack else [d, n, d]
            fail.append("cycle in the requires-graph: " + " → ".join(cyc) + " (§5a)")
        elif state[d] == WHITE:
            visit(d, stack + [d])
    state[n] = BLACK

for n in sorted(requires_graph):
    if state[n] == WHITE:
        visit(n, [n])

# 6. Smell sweep (§5.3) over the skill corpus.
SMELLS = [
    (re.compile(r'^## Steps\s*$', re.M),
     'a surviving "## Steps" heading — declare the deliverable, not the procedure (§5.3, case 4)'),
    (re.compile(r'the agent will:\s*\n?\s*1[.)]', re.I),
     '"the agent will: 1…" — declare the deliverable, not the steps (§5.3, case 4)'),
    (re.compile(r'`rule_user_[a-z0-9-]+`'),
     'a rule_user_* id in the shared corpus — this plugin ships to brains it has never seen. '
     'Name the rule CLASS instead (§5.2) and let match-rules resolve it.'),
]
for path in sorted(skills.values()):
    text = open(path).read()
    for rx, why in SMELLS:
        if rx.search(text):
            fail.append(f"{path}: {why}")

# 7. House style over the corpus's own prose (§5.2). A corpus that instructs a model how to
#    write has no standing to read like unedited model output. Body prose only: frontmatter
#    descriptions are the trigger surface and are matched on, code blocks are quoted, and
#    "## Guardrails — must not" is a contract identifier the checker itself greps for.
if os.environ.get('SECOND_BRAIN_SKIP_STYLE') != '1':
    STYLE = [
        (re.compile(r'[\u2014\u2013]'),
         'a dash used as punctuation — rewrite with a comma, a colon, or two sentences'),
        (re.compile('[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF]'),
         'an emoji in skill prose'),
    ]
    for sid, path in sorted(skills.items()):
        text = open(path).read()
        body = text.split('---', 2)[2] if text.count('---') >= 2 else text
        body = re.sub(r'```.*?```', '', body, flags=re.S)          # fenced code
        body = re.sub(r'`[^`\n]*`', '', body)                      # inline code
        body = body.replace('## Guardrails — must not', '')        # the mandated heading
        for n, line in enumerate(body.splitlines(), 1):
            for rx, why in STYLE:
                if rx.search(line):
                    fail.append(f"{path}: {why}: {line.strip()[:70]}")
                    break

if fail:
    print("✗ contract checks failed (CONVENTIONS §5, §5a):")
    for f in sorted(set(fail)):
        print(f"    - {f}")
    sys.exit(1)

print(f"✓ skill contracts valid · {len(skills)} skills declare their graph surface · "
      f"tree agrees · no cycles")
PY
