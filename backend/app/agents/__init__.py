from app.agents.base_agent import StageAgent
from app.agents.copilot_agent import CopilotAgent
from app.agents.llm import ClaudeAgent
from app.agents.stub import StubAgent

PROVIDERS = {"copilot": CopilotAgent, "claude": ClaudeAgent}


def get_agent(stage: str, config: dict, provider: str) -> StageAgent:
    """The agent for a stage: the stub, or an AI agent run by the provider chosen in Settings."""
    if config.get("mode") != "llm":
        return StubAgent(stage)
    return PROVIDERS.get(provider, CopilotAgent)(stage)
