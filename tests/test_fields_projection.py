"""Tests for the global --fields projection (token-economy helper)."""
from qzcli.cli import _project_fields


def test_projects_top_level_list():
    data = [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]
    assert _project_fields(data, "a,c") == [{"a": 1, "c": 3}, {"a": 4, "c": 6}]


def test_projects_list_inside_dict():
    data = {"total": 2, "jobs": [{"id": "j1", "status": "r", "x": 9}, {"id": "j2", "status": "s", "x": 0}]}
    out = _project_fields(data, "id,status")
    assert out["total"] == 2
    assert out["jobs"] == [{"id": "j1", "status": "r"}, {"id": "j2", "status": "s"}]


def test_missing_key_is_none_and_whitespace_tolerated():
    assert _project_fields([{"a": 1}], " a , z ") == [{"a": 1, "z": None}]


def test_scalars_and_non_dict_rows_pass_through():
    assert _project_fields({"n": 5, "items": [1, 2, 3]}, "a") == {"n": 5, "items": [1, 2, 3]}
    assert _project_fields("hello", "a") == "hello"


def test_empty_fields_returns_data_unchanged():
    data = [{"a": 1, "b": 2}]
    assert _project_fields(data, "  ") == data
