"""Workflow engine tests."""

import pytest

from workflows.condition_evaluator import evaluate_condition
from workflows.parser import WorkflowParser


class TestWorkflowParser:
    def test_parse_valid(self):
        parser = WorkflowParser()
        defn = {"name": "test", "steps": [{"id": "s1", "type": "agent"}]}
        result = parser.parse(defn)
        assert result["name"] == "test"

    def test_circular_dependency(self):
        parser = WorkflowParser()
        defn = {
            "name": "test",
            "steps": [
                {"id": "a", "type": "agent", "depends_on": ["b"]},
                {"id": "b", "type": "agent", "depends_on": ["a"]},
            ],
        }
        with pytest.raises(ValueError, match="E3006"):
            parser.parse(defn)

    def test_invalid_step_type(self):
        parser = WorkflowParser()
        defn = {"name": "test", "steps": [{"id": "s1", "type": "invalid_type"}]}
        with pytest.raises(ValueError):
            parser.parse(defn)


class TestConditionEvaluator:
    def test_greater_than(self):
        assert evaluate_condition("total > 500000", {"total": 600000})

    def test_less_than(self):
        assert not evaluate_condition("total > 500000", {"total": 400000})

    def test_equality(self):
        assert evaluate_condition("status == mismatch", {"status": "mismatch"})

    def test_or_condition(self):
        assert evaluate_condition(
            "total > 500000 OR status == mismatch", {"total": 100, "status": "mismatch"}
        )

    def test_and_condition(self):
        assert not evaluate_condition(
            "total > 500000 AND status == mismatch", {"total": 100, "status": "mismatch"}
        )
