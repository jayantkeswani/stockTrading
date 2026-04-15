---
name: simplify
description: Review changed code for reuse, quality, and efficiency, then fix any issues found
user_invocable: true
---

Review recently changed code in the stockTrading project for simplification opportunities.

## Steps

1. Identify changed files via `git diff` or `git log --oneline -5`
2. For each changed file:
   - **Duplication**: Logic repeated across files that could be extracted to a shared utility
   - **Over-engineering**: Unnecessary abstractions, unused parameters, premature generalization
   - **Performance**: N+1 queries, unnecessary re-renders, missing async where needed
   - **Dead code**: Unused imports, unreachable branches, commented-out code
   - **Complexity**: Functions over 50 lines that could be split, deeply nested conditionals
3. Apply simplifications directly — keep changes minimal and targeted
4. Run `cd backend && source .venv/bin/activate && python -m pytest tests/ -v --tb=short` to ensure nothing breaks
5. Report what was simplified and why

## Rules

- Don't refactor working code that wasn't changed — scope to recent changes only
- Don't add new abstractions unless removing clear duplication
- Three similar lines > one premature abstraction
- If in doubt, leave it alone
