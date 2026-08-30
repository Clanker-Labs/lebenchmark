"""A typo in the suite costs hours of GPU time before it shows up as a model
that 'failed everything'. Validation runs before a run starts; these pin it."""

from __future__ import annotations

import pytest

from lebenchmark.tasks import TaskError, load
from lebenchmark.toolbelt import TOOLS_BY_NAME


def write(tmp_path, body: str):
    (tmp_path / "s.yaml").write_text(body)
    return tmp_path


class TestShippedSuite:
    def test_loads(self):
        assert len(load("tasks")) >= 12

    def test_every_expected_tool_is_on_the_belt(self):
        for task in load("tasks"):
            if task.kind == "tool":
                assert task.expect_tool in TOOLS_BY_NAME

    def test_every_task_has_several_paraphrases(self):
        # One phrasing plus a deterministic backend means repetitions measure
        # one sentence N times.
        for task in load("tasks"):
            assert len(task.prompts) >= 3, task.id

    def test_it_contains_abstention_tasks(self):
        kinds = {t.kind for t in load("tasks")}
        assert "abstain" in kinds and "tool" in kinds

    def test_paraphrases_cycle(self):
        task = next(t for t in load("tasks") if t.id == "restart_app")
        first = task.prompt_for(0)
        assert task.prompt_for(len(task.prompts)) == first
        assert task.prompt_for(1) != first


class TestValidation:
    def test_rejects_a_tool_that_does_not_exist(self, tmp_path):
        write(tmp_path, "- {id: x, kind: tool, expect_tool: nope, prompts: [a]}")
        with pytest.raises(TaskError, match="not on the belt"):
            load(tmp_path)

    def test_rejects_an_argument_the_tool_has_not_got(self, tmp_path):
        write(tmp_path, "- {id: x, kind: tool, expect_tool: brain_today, "
                        "prompts: [a], expect_args: {nope: {equals: 1}}}")
        with pytest.raises(TaskError, match="has no"):
            load(tmp_path)

    def test_rejects_an_equals_outside_the_enum(self, tmp_path):
        write(tmp_path, "- {id: x, kind: tool, expect_tool: ecosystem_app, prompts: [a], "
                        "expect_args: {action: {equals: reboot}}}")
        with pytest.raises(TaskError, match="not in the enum"):
            load(tmp_path)

    def test_rejects_an_abstain_task_that_expects_a_tool(self, tmp_path):
        write(tmp_path, "- {id: x, kind: abstain, expect_tool: brain_today, prompts: [a]}")
        with pytest.raises(TaskError, match="must not expect"):
            load(tmp_path)

    def test_rejects_duplicate_ids(self, tmp_path):
        write(tmp_path, "- {id: x, kind: abstain, prompts: [a]}\n"
                        "- {id: x, kind: abstain, prompts: [b]}")
        with pytest.raises(TaskError, match="duplicate task id"):
            load(tmp_path)

    def test_rejects_duplicate_prompts(self, tmp_path):
        write(tmp_path, "- {id: x, kind: abstain, prompts: [a, a]}")
        with pytest.raises(TaskError, match="duplicate prompts"):
            load(tmp_path)

    def test_rejects_an_unknown_rule(self, tmp_path):
        write(tmp_path, "- {id: x, kind: tool, expect_tool: chat_search, prompts: [a], "
                        "expect_args: {query: {matches: foo}}}")
        with pytest.raises(TaskError, match="unknown rule"):
            load(tmp_path)
