# Module 1 lab: context autopsy, then Context Spec v1

Session 1 · 2.5 h · Lab A (35 min) and Lab B (45 min), then the design review.
Run every command from your workspace folder in the JupyterLab terminal. Nothing here needs `jq`.

**What you hand in (committed to Git):** `labs/m1/autopsy_attribution.md`, `context_spec/spec_v1.yaml`,
the four files in `context_spec/system/v1/`, two run reports in `labs/m1/runs/`, and `labs/m1/decision_records.md`.
This Context Spec v1 is your capstone's spec: every later module amends it.

| File | What it is for |
|---|---|
| `autopsy_attribution.md` | Lab A template: your findings table |
| `load_autopsy_history.py` | Replays the autopsy conversation (22 turns) into a NovaAssist session |
| `probes.yaml` | The three Lab B probes and the scoring rubric |
| `policy_excerpts.md` | The policy clauses supplied with probe P-2 |
| `run_probes.py` | Runs the probes with a spec you choose and writes a run report to `runs/` |
| `trace_summary.py` | Summarises any trace file: tokens by channel, kept / excluded items and why, violations |
| `decision_record_template.md` | Copy to `decision_records.md` for D-M1-01 and D-M1-02 |
| `check_m1.py` | The exit check (structure only) |

## 0. Setup (5 min)

```bash
python scripts/preflight.py
git status
```

## Lab A: context autopsy (35 min)

Open `data/novatech/m1/autopsy_transcript.md`. It is the exact window NovaBot, a first-generation support
assistant, sent to the model for Priya's question about TCK-1042 (Orbital Foods), component by component, with
NovaBot's answer at the end. The same window is in `data/novatech/m1/autopsy_window.json`.

1. **(5 min)** Read the token table at the top. Which component dominates? Is there a budget at all?
2. **(15 min)** Fill `labs/m1/autopsy_attribution.md`. For each failure in the answer or the window: name **one**
   component, quote the evidence from the window, and state what is missing from the design (not "what the
   model did wrong").
3. **(10 min)** Compare with your pair. Settle disagreements by pointing at the quoted evidence. Where two
   components both seem responsible, decide which one a design change would fix first and write it down.
4. **(5 min)** Rank your findings by how much fixing them would change the answer. Mark your top three and the one
   to fix first.

Commit: `git add labs/m1/autopsy_attribution.md && git commit -m "M1 autopsy"`

## Lab B: Context Spec v1 and system context (45 min)

### BUILD (20 min)

```bash
cp context_spec/example_spec.yaml context_spec/spec_v1.yaml
```

Amend `spec_v1.yaml` into **your** spec (set `spec_version: "1.0"`), then write the four system-context files in
`context_spec/system/v1/` (`role.md`, `grounding.md`, `conflicts.md`, `tone.md`; the comments in each say what it
must decide). Decide, and be ready to defend:

1. **Budget allocation.** Component budgets must fit in `total_tokens - response_reserve` (the example uses
   10,500 of 12,000). Move tokens only where your reasoning supports it.
2. **Priorities and eviction order** (`overflow.evict_order`). System and user input are never evicted.
3. **Assembly order** (`assembly_order`): where each component appears in the window, and why.
4. **What does not belong in system context:** facts that change (policies, SLAs, versions, customer data) belong
   in sources with provenance.
5. **The four system files:** who is served (P1 Priya, P2 Daniel, P3 Meera; see
   `data/novatech/stores/csv/personas.csv`), when and how to cite, the source-authority order and how a conflict
   is shown, structure and length per persona.
6. **Session state fields** that must survive a long conversation (state is free-form `key=value`).

What the S1 pipeline acts on today: per-channel budgets, `history.selection.recent_turns`, `assembly_order` and
the system files. Priorities and `evict_order` are checked by the loader, but they are design commitments until the selection policy you build in M4 to M7 enforces them.

Validate after every change (the loader lists every problem at once):

```bash
python -m novaassist.cli --spec context_spec/spec_v1.yaml --fake --once "hello"
```

### BREAK (part of the protected 15 min)

```bash
python labs/m1/load_autopsy_history.py --session autopsy
python labs/m1/run_probes.py --spec context_spec/spec_v1.yaml --session autopsy
python labs/m1/run_probes.py --spec context_spec/example_spec.yaml --session autopsy
```

The loader writes the 22 autopsy turns into session `autopsy` (turn `autopsy-T01` is the transcript's
`CONV-0044-T01`); running it again starts the session from scratch. `run_probes.py` runs each probe **twice**
with the real model, each run in a fresh copy of that session, so both specs see the same history. It prints
one block per run and writes `labs/m1/runs/<spec name>_<time>.md`. Both specs list the same system files, so
this baseline compares budgets, priorities and assembly order.

| Probe | Persona / state | What it tests |
|---|---|---|
| P-1 Grounding and history | P1 | Does the assistant invent policy facts the context cannot support? Does the customer's product version survive the history budget? |
| P-2 Conflict | P1 | With `policy_excerpts.md` in the input: does your `conflicts.md` let the model arbitrate between two current policies and still show the conflict with both citations? |
| P-3 Persona format | P2 | Do `role.md` and `tone.md` produce the persona's format, without inventing account data? |

There is no knowledge, memory or tool channel yet. When a fact is not in the supplied context, the right
behaviour is to say so.

### OBSERVE and MEASURE (rest of the protected 15 min)

Read the trace, do not guess. For P-1 find which history turns are in the window, which are excluded and why,
whether any budget violation occurred, and the window tokens by channel:

```bash
python labs/m1/trace_summary.py traces/<the P-1 trace file printed by run_probes>.jsonl
```

Score each run in the run report: tick each applicable rubric item (R1 to R5, defined in `probes.yaml`) that the
answer meets. Answers vary between runs (the model does not accept temperature 0): **score the worse of the two
runs.** Compare spec v1 with the example spec on window tokens by channel, history kept / excluded, violations,
CCU and the rubric.

### IMPROVE (10 min)

Make **one** change justified by what you measured (a system-context rule, a budget or priority, or a state
field). Re-run only the probe it targets, for example `--probe P-1`, and note the before / after rubric score and
window tokens in the run report's Notes.

Then `cp labs/m1/decision_record_template.md labs/m1/decision_records.md` and fill both records, three to six
lines each: **D-M1-01** budgets, priorities, assembly order; **D-M1-02** source-authority and conflict rule
(with your P-2 evidence).

### Optional extension (only if time remains)

In the CLI, set state and re-run P-1's question:

```bash
python -m novaassist.cli --spec context_spec/spec_v1.yaml --session autopsy
you> :state version=2.3 customer=CUST-0001 ticket=TCK-1042
you> Is Orbital entitled to a hotfix for this, and what response time do we owe them?
```

What changes in the window and in the answer? Where should such facts be written in the real system?
(This session appends to `autopsy`; run the loader again before any further probe runs.)

## Exit step (5 min)

```bash
git add context_spec labs/m1
git commit -m "M1: Context Spec v1"
python labs/m1/check_m1.py
```

`check_m1.py` prints PASS/FAIL for seven structural items: spec v1 loads, budgets fit, the four system files are
designed, at least five attributed findings with quoted evidence, a run report for each spec, both decision
records, and your M1 work committed. It never checks whether your answers are "right".

**Start again:** `ce-reset --to S01` restores the starting state and keeps a backup of your current work.
Probe runs write only to `labs/m1/runs/`, `traces/` and `sessions/`.
