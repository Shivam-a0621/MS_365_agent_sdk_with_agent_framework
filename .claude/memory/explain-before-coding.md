---
name: explain-before-coding
description: Explain code changes (what + why) before writing them; keep the coding style simple and basic
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bfcac8ac-7e73-4384-a543-7715ee5479ce
---

On agent-fastapi the user wants: **"before writing any code just explain it to me and its purpose,"** and **"use simple and basic coding style which is easy to understand."**

**Why:** he's learning/reviewing the system as it's built and wants to follow every change; he favors readability over cleverness.

**How to apply:** for each code task, first explain *what* will change and *why* (plain language), then implement. Prefer straightforward, explicit code — simple functions, clear names, minimal abstraction/metaprogramming, obvious control flow — over compact or "clever" constructs. In plan mode this maps to: write the explanation in the plan + message, then ExitPlanMode for the go-ahead, then code. Pairs with [[user-runs-migrations]] (give him the alembic commands; he runs them) and [[prod-rigor-edge-cases]].
