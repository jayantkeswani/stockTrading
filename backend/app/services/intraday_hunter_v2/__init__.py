"""Intraday Hunter v2 — a parallel PAPER strategy + learning-loop data capture.

Runs beside v1 (`services/intraday_hunter/`, untouched) on its own run rows
(`intraday_hunter_runs.variant='v2'`), its own YOLO profile `IH-v2`, and its own strategy name
`intraday_hunter_v2`. Differences vs v1: teacher plan + stop-level facts + graded lessons in
Call 1; a fast text-only stop-hunting Call 2 from 09:16 every minute to 09:25; his basket
shape (BN ATM+1 OTM, NIFTY ATM, SENSEX ATM); BASKET-level 1:1 exits; a per-minute learning
dataset (ih_minute_log) with every candidate arm; nightly grading + ledger + lessons.

Design spec: docs/ai/intraday-hunter-v2.md
"""
