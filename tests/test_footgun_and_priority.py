"""Tests for the `set -e` footgun guard and Project priority_cap parsing."""
import pytest

from qzcli.core.create import _SET_E_RE
from qzcli.domain.models import Project


@pytest.mark.parametrize("cmd", [
    "set -e",
    "set -eu",
    "set -euxo pipefail",
    "set -ex",
    "echo hi; set -e; do_stuff",
    "bash -lc 'set -e; foo'",
    "  set -e",
    "(set -e; bar)",
    "{set -e; baz}",
    "true && set -eu && go",
])
def test_set_e_detected(cmd):
    assert _SET_E_RE.search(cmd) is not None, f"expected match: {cmd!r}"


@pytest.mark.parametrize("cmd", [
    "echo hi",
    "set -x",                # -x without -e is fine
    "set -u",                # -u without -e is fine
    "set -uxo pipefail",     # no -e in the flag list
    "preset -e",             # not a `set` command — preceded by a letter
])
def test_set_e_not_falsely_detected(cmd):
    assert _SET_E_RE.search(cmd) is None, f"unexpected match: {cmd!r}"


def test_set_e_quoted_literal_is_a_documented_limitation():
    # The guard can't parse shell quoting; a literal `set -e` inside a string
    # ALSO fires. Acceptable: the user can pass --allow-set-e to override.
    assert _SET_E_RE.search("echo 'set -e in a string'; ok") is not None


def test_project_priority_cap_parsed():
    p = Project.from_api({"id": "p-1", "name": "X", "en_name": "x",
                          "priority_name": "6", "space_list": []})
    assert p.priority_cap == 6
    assert p.to_dict()["priority_cap"] == 6


def test_project_priority_cap_none_when_missing_or_bad():
    assert Project.from_api({"id": "p", "name": "X", "en_name": "x"}).priority_cap is None
    assert Project.from_api({"id": "p", "name": "X", "en_name": "x",
                             "priority_name": ""}).priority_cap is None
    assert Project.from_api({"id": "p", "name": "X", "en_name": "x",
                             "priority_name": "abc"}).priority_cap is None
