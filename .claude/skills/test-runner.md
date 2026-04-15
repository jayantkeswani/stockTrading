---
name: test-runner
description: Run the full backend test suite, diagnose failures, fix them, and re-run until green
user_invocable: true
---

Run the backend test suite for the stockTrading project and ensure all tests pass.

## Steps

1. `cd /Users/cadenceproinc/projects/stockTrading/backend`
2. Run: `source .venv/bin/activate && python -m pytest tests/ -v --tb=short`
3. Report: total passed, failed, errors, skipped
4. If any tests fail:
   a. Read the failing test file AND the source file it tests
   b. Determine if the bug is in the test or the source code
   c. Fix the root cause (prefer fixing test expectations if the source code behavior is intentionally changed)
   d. Re-run tests to confirm the fix
   e. Repeat until all tests pass
5. Report final status with pass/fail counts

## Important

- Never skip or delete tests to make the suite pass
- If a test is genuinely obsolete (tests removed code), delete it and note why
- Run the full suite at the end, not just the fixed tests
- If tests require infrastructure (PostgreSQL, Redis), note which tests are skipped and why
