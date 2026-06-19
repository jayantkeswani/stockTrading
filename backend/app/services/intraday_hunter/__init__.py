"""Intraday Hunter agent — a discretionary, human-like index-options trade suggester.

Reasons like the @IntradayHunter trader (SL-hunting / trapped-trader framework),
forms a pre-open thesis (Call 1) and an at-open ENTER/WAIT/SKIP decision (Call 2),
and surfaces a full trade plan on the /intraday-hunter UI page for manual execution.

Design spec: docs/ai/intraday-hunter-agent.md
"""
