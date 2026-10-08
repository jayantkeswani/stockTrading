"""Intraday Hunter v2 — teacher ingestion (the real trader's evening plan + live trade).

Modules: clock (taskbar-clock OCR decoding), youtube (yt-dlp/ffmpeg wrappers), plan
(evening prediction video -> structured plan), live (live-trading video -> entry/exit/P&L),
ingest (DB-backed scheduled jobs + upsert for Mac pushes).
"""
