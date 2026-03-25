"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .reflection_agent import REFLECTION_AGENT_CONFIG
from .report_reviewer_agent import REPORT_REVIEWER_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "REFLECTION_AGENT_CONFIG",
    "REPORT_REVIEWER_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "reflection": REFLECTION_AGENT_CONFIG,
    "report_reviewer": REPORT_REVIEWER_CONFIG,
}
