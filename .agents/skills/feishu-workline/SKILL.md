---
name: feishu-workline
description: Link16 explicitly invokes this skill at the start of every Feishu turn to choose and commit the semantic card title and progress handoff.
---

# Feishu Workline

This is an explicit Link16 bridge protocol skill. Use it only when the bridge
injects a `[Link16 feishu-workline]` turn contract.

Before any other tool call or substantive work, read the current request and
choose exactly one action:

- `keep`: the request continues the same concrete task and the existing
  semantic workline is still accurate.
- `replace`: this is a new task, or the existing project/task is missing or no
  longer accurate. Supply `project`, `task`, and `progress`.
- `progress`: the concrete task is unchanged but its visible Stage chain has
  materially changed. Supply the new `progress`.

Run the exact `session_work.py decide` command supplied by the injected
contract. The semantic fields are model-owned:

- `project`: the short project or repository label the owner recognizes.
- `task`: a concrete object + action + deliverable. A reader must understand
  the work without opening the chat. Do not write placeholders such as
  “implementation and tests”.
- `progress`: the current Stage chain with `✅` / `🔄` / `⏳`; when a structured
  plan exists, preserve its meaning and order.

The bridge owns identity, session, runtime, route, timestamps, turn key,
revision, status, card layout, colors, truncation, and summary. Never invent or
override those fields. A `keep` decision is valid only when an earlier
model-written workline exists. If the command rejects a stale turn or missing
field, correct the semantic fields and retry once before continuing.

Do not narrate this protocol to the user. After the command succeeds, continue
the original request normally.
