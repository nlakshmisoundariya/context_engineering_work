# Lab A: context autopsy, failure attribution

Input: `data/novatech/m1/autopsy_transcript.md` (the window NovaBot sent for Priya's question about TCK-1042, and its answer).

One row per failure. Use **one** component per row, chosen from:
`system` · `user_input` · `history` · `knowledge` · `tools` · `memory_state` · `budget` (for cross-cutting problems).
Quote the evidence from the window in "double quotes" (or `backticks` for code and payloads). Describe what is
missing from the **design**, not what the model did wrong. Add as many rows as you find.

| # | Component | Evidence (quoted from the window) | What is missing in the design |
|---|---|---|---|
| 1  | system          | "Never tell a customer no if you can avoid it."                                  | Missing: Should say it cannot support, try contacting any human.<br><br>Should never try guessing what to say, if model doesn't know it should say "No"                              |
| 2   | system                | "our premium customers expect fast service — Premium customers get 1-hour responses."                                                                               | Missing: Status indicator<br><br>Assistants should provide instant responses.                                                                                                             |
| 2  | memory_state                | "```text<br>(empty)<br>```"                                  | <b>Missing: User profile and data linked to them.<br><br>Assistant has no way to verify the user's status—forcing it to either guess, ask the user manually, or fail.  Even it can not know if they are a Premium or Normal customer.                              |
| 3  | knowledge                            | "Standard customers are not eligible for hotfix builds; they receive workarounds"                                  | Missing:  linking customers profile/tier (standard, premium or elite) to memory_state.<br><br>This will not have a record of who has to get the workarounds and without memory it an not generate that when elite and premium customers get their hotfixes.                               |
| 4  | tool_definitions                                     | "customer_lookup: Look up a customer account profile, tier summary and environment."<br>"ticket_search: Search tickets by customer, product, status or text."                                  | Missing: customer_lookup and ticket_search don't specify whether to pass an email, user ID, or company name which fails when memory_state is empty.                              |
| 5  | tool_results                                                     | "body"                                  | Missing: Tool returns full, unfiltered long paragraphs instead of concise summary, wasting tokens and hiding facts.                               |
| 6  | history                                                                 |                                   | Missing: Every  conversation is kept,  instead should have summarized for history. Missed sliding window .                              |
| 7  | user_input                                                                        |                                   |                               |

## Token table check (step 1)

- Largest component and its share of the window:
- Is there a budget at all? What would it have been under the course's 12,000-token window?

## Pair compare (step 3)

- Disagreements and how you resolved them (which quoted evidence settled it):
- Where two components both seemed responsible, the one a design change would fix first:

## Top three and fix first (step 4)

1. memory_state
2. clear histoy
3. in system, assistant should say NO if it doesn't know anything instead of hallucinating.

**Fix first:** (finding #, component, and why it changes the answer most)
