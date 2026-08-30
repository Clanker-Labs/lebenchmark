"""The classifier is the instrument. If it drifts, every number in every past
run drifts with it, so its behaviour is pinned here rather than eyeballed."""

from __future__ import annotations

import pytest

from lebenchmark.grade import Outcome, classify, detect_tool_syntax, grade
from lebenchmark.tasks import Task


def _call(name: str, arguments: str) -> dict:
    return {"id": "c1", "type": "function",
            "function": {"name": name, "arguments": arguments}}


RESTART = Task(
    id="restart_app", kind="tool", prompts=["Restart moude."],
    expect_tool="ecosystem_app",
    expect_args={"app": {"equals": "moude"}, "action": {"equals": "restart"}},
)
ABSTAIN = Task(id="explain", kind="abstain", prompts=["What is idempotent?"])


class TestDetectToolSyntax:
    def test_the_documented_failure_shape(self):
        # Verbatim from docs/AGENTS.md — this is the shape that started it.
        content = 'Sure. <tools>{"name": "ecosystem_app", "arguments": {"app": "moude"}}</tools>'
        assert detect_tool_syntax(content) == "tools_tag"

    @pytest.mark.parametrize(
        "content,shape",
        [
            ('<tool_call>{"name": "brain_today", "arguments": {}}</tool_call>', "tool_call_tag"),
            ("<|tool_call|>", "special_token"),
            ("<function=ecosystem_status>", "function_tag"),
            ('{"name": "chat_list", "arguments": {}}', "json_name_args"),
        ],
    )
    def test_other_known_shapes(self, content, shape):
        assert detect_tool_syntax(content) == shape

    @pytest.mark.parametrize(
        "content",
        [
            "I will use ecosystem_app to restart it.",
            "The action field accepts start, stop, restart or logs.",
            "You have a tool called brain_today.",
            "",
            "moude is running normally.",
        ],
    )
    def test_prose_that_only_mentions_tools_is_not_a_call(self, content):
        # A false positive here inflates the headline rate, so this is the test
        # that matters most.
        assert detect_tool_syntax(content) is None


class TestClassify:
    def test_structured_call_wins_over_content(self):
        outcome, _ = classify(True, "I'll restart it.", [_call("ecosystem_app", "{}")])
        assert outcome is Outcome.TOOL_CALL

    def test_empty_is_distinct_from_refusal(self):
        assert classify(True, "", [])[0] is Outcome.EMPTY
        assert classify(True, "   ", [])[0] is Outcome.EMPTY
        assert classify(True, "No.", [])[0] is Outcome.PROSE_PLAIN

    def test_transport_failure_is_not_model_behaviour(self):
        assert classify(False, "", [])[0] is Outcome.ERROR


class TestGradeToolTask:
    def test_a_correct_call(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", '{"app":"moude","action":"restart"}')])
        assert g.correct and g.right_tool and g.args_schema_ok and g.args_match

    def test_argument_order_does_not_matter(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", '{"action":"restart","app":"moude"}')])
        assert g.correct

    def test_wrong_enum_member_fails(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", '{"app":"moude","action":"reboot"}')])
        assert not g.correct and not g.args_schema_ok

    def test_missing_required_argument_fails(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", '{"app":"moude"}')])
        assert not g.correct and not g.args_schema_ok

    def test_wrong_tool_fails(self):
        g = grade(RESTART, True, "", [_call("ecosystem_status", "{}")])
        assert not g.correct and not g.right_tool and g.emitted_call

    def test_unparseable_arguments_fail_without_crashing(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", "{app: moude,")])
        assert not g.correct and g.called_args is None

    def test_app_outside_the_registry_is_flagged(self):
        g = grade(RESTART, True, "", [_call("ecosystem_app", '{"app":"moudle","action":"restart"}')])
        assert g.hallucinated_app == "moudle"
        assert not g.correct  # expect_args pins the app, so this also fails outright

    def test_prose_serialisation_is_not_a_success(self):
        content = '<tools>{"name": "ecosystem_app", "arguments": {"app":"moude","action":"restart"}}</tools>'
        g = grade(RESTART, True, content, [])
        assert g.outcome is Outcome.PROSE_TOOL_SYNTAX
        assert not g.correct and not g.emitted_call


class TestGradeAbstainTask:
    def test_plain_answer_is_correct(self):
        assert grade(ABSTAIN, True, "It means running it twice is safe.", []).correct

    def test_calling_a_tool_is_wrong(self):
        g = grade(ABSTAIN, True, "", [_call("ecosystem_status", "{}")])
        assert not g.correct and g.called_tool == "ecosystem_status"

    def test_prose_serialisation_is_also_wrong(self):
        g = grade(ABSTAIN, True, '{"name": "brain_today", "arguments": {}}', [])
        assert not g.correct

    def test_empty_is_not_an_abstention(self):
        # Saying nothing is a failure, not restraint.
        assert not grade(ABSTAIN, True, "", []).correct
