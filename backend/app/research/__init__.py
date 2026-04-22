"""AI-powered stock research agent system.

Multi-agent orchestration for comprehensive stock analysis:
- Orchestrator coordinates 6 specialized sub-agents in parallel
- Each agent fetches data programmatically, then uses LLM for interpretation
- Synthesis agent combines all findings into actionable report
- Real-time progress via WebSocket events
"""
