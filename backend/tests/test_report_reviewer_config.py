"""Tests for report_reviewer subagent configuration (Phase 4)."""

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def reviewer_config():
    backend_dir = Path(__file__).parent.parent
    config_path = backend_dir / "packages/harness/deerflow/subagents/config.py"
    agent_path = backend_dir / "packages/harness/deerflow/subagents/builtins/report_reviewer_agent.py"

    _load_module("deerflow.subagents.config", config_path)
    mod = _load_module("report_reviewer_agent_test_module", agent_path)
    return mod.REPORT_REVIEWER_CONFIG


class TestReportReviewerRegistration:
    def test_registered_in_builtin_subagents(self):
        from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
        assert "report_reviewer" in BUILTIN_SUBAGENTS

    def test_config_object_is_accessible(self, reviewer_config):
        assert reviewer_config is not None
        assert reviewer_config.name == "report_reviewer"


class TestReportReviewerConfig:
    def test_tools_are_exactly_three(self, reviewer_config):
        assert reviewer_config.tools == ["read_file", "write_file", "evidence_retrieve"]

    def test_disallowed_tools_includes_required_entries(self, reviewer_config):
        required = [
            "task",
            "ask_clarification",
            "present_files",
            "web_search",
            "web_fetch",
            "compact_context",
            "outline_update",
            "evidence_store",
            "check_query_duplicate",
            "report_validate",
        ]
        for tool in required:
            assert tool in reviewer_config.disallowed_tools, (
                f"'{tool}' missing from disallowed_tools"
            )

    def test_max_turns(self, reviewer_config):
        assert reviewer_config.max_turns == 20

    def test_timeout_seconds(self, reviewer_config):
        assert reviewer_config.timeout_seconds == 180

    def test_model_inherit(self, reviewer_config):
        assert reviewer_config.model == "inherit"


class TestReportReviewerSystemPrompt:
    def test_contains_skip_condition_for_missing_sources_annotation(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        assert "[sources: X, Y]" in prompt or "no [sources:" in prompt or "sources: X, Y" in prompt

    def test_contains_three_priority_tasks(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        assert "Primary" in prompt
        assert "Secondary" in prompt
        assert "Tertiary" in prompt

    def test_enrich_is_highest_priority(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        primary_pos = prompt.find("Primary")
        secondary_pos = prompt.find("Secondary")
        tertiary_pos = prompt.find("Tertiary")
        assert primary_pos < secondary_pos < tertiary_pos

    def test_contains_workflow_section(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        assert "WORKFLOW" in prompt

    def test_contains_rules_section(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        assert "RULES" in prompt

    def test_no_external_knowledge(self, reviewer_config):
        prompt = reviewer_config.system_prompt
        assert "evidence bank" in prompt.lower()
        assert "do not add facts from your own knowledge" in prompt.lower()


class TestTaskToolSubagentWiring:
    """All BUILTIN_SUBAGENTS must be accepted by task_tool's Literal type."""

    def test_literal_includes_all_builtin_subagents(self):
        import typing

        from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
        from deerflow.tools.builtins.task_tool import task_tool

        hints = typing.get_type_hints(task_tool.func, include_extras=True)
        literal_type = hints["subagent_type"]
        allowed = set(typing.get_args(literal_type))

        for name in BUILTIN_SUBAGENTS:
            assert name in allowed, (
                f"Subagent '{name}' registered in BUILTIN_SUBAGENTS "
                f"but missing from task_tool Literal: {sorted(allowed)}"
            )
