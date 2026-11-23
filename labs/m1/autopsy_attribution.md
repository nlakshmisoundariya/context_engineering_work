# Lab A: context autopsy, failure attribution

Input: `data/novatech/m1/autopsy_transcript.md` (the window NovaBot sent for Priya's question about TCK-1042, and its answer).

One row per failure. Use **one** component per row, chosen from:
`system` · `user_input` · `history` · `knowledge` · `tools` · `memory_state` · `budget` (for cross-cutting problems).
Quote the evidence from the window in "double quotes" (or `backticks` for code and payloads). Describe what is
missing from the **design**, not what the model did wrong. Add as many rows as you find.

| # | Component | Evidence (quoted from the window) | What is missing in the design |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |

## Token table check (step 1)

- Largest component and its share of the window:
- Is there a budget at all? What would it have been under the course's 12,000-token window?

## Pair compare (step 3)

- Disagreements and how you resolved them (which quoted evidence settled it):
- Where two components both seemed responsible, the one a design change would fix first:

## Top three and fix first (step 4)

1.
2.
3.

**Fix first:** (finding #, component, and why it changes the answer most)
