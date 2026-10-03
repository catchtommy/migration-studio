from app.agents.base_agent import StageAgent
from app.agents.llm import ClaudeAgent
from app.agents.stub import StubAgent


def get_agent(stage: str, config: dict) -> StageAgent:
    """The agent for a stage, as configured on the Agents page: the configurable Claude agent or the stub."""
    return ClaudeAgent(stage) if config.get("mode") == "llm" else StubAgent(stage)
