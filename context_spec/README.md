# Context Spec

`example_spec.yaml` is the starter spec (v0.1): the contract for every context window NovaAssist assembles.
In Module 1 you copy it to your own spec, design the system-context files under `system/`, and justify every
budget and priority. Every later module amends the spec; commit each version with Git so its history is visible.

Load and validate a spec:

```bash
python -c "from novaassist.spec import load_spec; s = load_spec('context_spec/example_spec.yaml'); print(s.version, s.total_tokens)"
```

Run one request through the pipeline and look at its trace:

```bash
python -m novaassist.cli --fake --once "What does Premium support cover?"
```

The schema is listed field by field in the comments of `example_spec.yaml`.

## Knowledge section keys (Module 2)

Add these to `components.knowledge` in your spec; the loader checks them when present:

```yaml
knowledge:
  store: ../indexes/chunks.jsonl         # your chunk store, relative to the spec file
  metadata_required: [doc_id, ...]       # fields every chunk must carry
  status_rule: {superseded: down_rank}   # per document status: include | flag | down_rank | exclude
  freshness: {max_age_days: null, on_violation: flag, unknown_date: flag}   # unknown_date: flag | exclude | include_with_warning
```

They record your design; retrieval and selection in Modules 3 and 4 apply them.
