# NovaAssist starter kit (participant-facing)

**Ships to participants.** Everything in this folder is copied into each participant's hosted workspace and into the session checkpoints. Never place instructor material here; the leakage check blocks any build that contains it.

| Folder | Contents |
|---|---|
| `novaassist/` | The agent package participants extend |
| `tools/` | Kit utilities (parsing/OCR, chunking helpers, embeddings, retrieval helpers, metrics, ablation runner) |
| `mcp_server/` | Local MCP server scaffold and NovaTech tools |
| `context_spec/` | Context Spec schema notes, example spec and system-context files |
| `labs/` | Per-module lab folders (only where a lab design exists) |
| `data/` | NovaTech data, filled at packaging time from `build/` — never edited by hand |
| `scripts/` | Health check, reset, submission and model-check scripts |
