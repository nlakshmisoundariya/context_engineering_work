import json
from pathlib import Path #it handles file paths the same way windows/mac

WINDOW_FILE = Path("data/novatech/m1/autopsy_window.json")
window = json.loads(WINDOW_FILE.read_text(encoding="utf-8"))

parts = {}
for component in window["components"]: #like lists in python we have components in window.
    parts[component["component"]] = component

# ----- Step 1: open the window -----
print()
print("STEP 1: the request")
print("Asked by:", window["persona"])
print("Question:", parts["user_input"]["content"])
print("NovaBot answered:")
print(window["response"])

# ---- Step 2: count tokens -----
BUDGET = 12000
total = window["total_window_tokens"]
print()
print("STEP 2: Tokens by component")
for name, component in parts.items():
    share = 100*component["tokens"]/total
    print(f"{name:<18}{component['tokens']:>7} {share:5.1f}%")
print(f"{'total':<18}{total: >7}")
print(f"BUDGET {BUDGET}: the window is {total/BUDGET:.1f} times the budget")

# ---- Step 3: replay the conversation -----
print()
print("STEP 3: The system context, line by line")
for num, line in enumerate(parts["system"]["content"].splitlines(), 1):
    print(f"{num:>3} {line}")

# ----- Step 4: what knowledge was retrieved? -----
print()
print("STEP 4: retrieved knowledge, in rank order")
for chunk in window["knowledge_chunks"]:
    print("---", chunk["chunk"])
    print(chunk["text"])