---
name: review-code
description: Review recent code changes for quality, bugs, security, and consistency with project conventions
user_invocable: true
---

Review recent code changes in the stockTrading project for quality and correctness.

## Steps

1. `cd /Users/cadenceproinc/projects/stockTrading`
2. Run `git diff` for unstaged changes and `git diff --cached` for staged changes
3. If no uncommitted changes, run `git log --oneline -5` and review the last commit with `git show HEAD`
4. For each changed file, check:
   - **Type safety**: Python type hints present and correct, TypeScript types not using `any`
   - **Error handling**: Exceptions caught at appropriate boundaries (API layer, not deep in services)
   - **Security**: No hardcoded secrets, no SQL injection, no XSS, no command injection
   - **Consistency**: Follows patterns in the relevant CLAUDE.md (naming, imports, structure)
   - **Missing tests**: Does the change need new tests? Note what's untested
   - **Documentation**: Does any CLAUDE.md need updating for structural changes?
   - **IST timezone**: All market times use IST, stored as TIMESTAMPTZ
   - **Port/URL**: Backend on 8080, frontend on 3000, WS on ws://localhost:8080/ws
5. Report findings as a checklist with severity (critical / warning / info)
6. Auto-fix critical issues (security, crashes). Leave warnings for user review
7. Do NOT commit — leave changes for the user to review

## Conventions Reference

- Backend: Python 3.11, FastAPI, async/await, Pydantic v2, SQLAlchemy 2.0 async
- Frontend: Next.js 15, TypeScript strict, Tailwind CSS v4, Zustand
- All prices in INR, formatted with Indian number system
- Paper trading mode by default
