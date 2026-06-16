"""Unit tests for the `nb exec` pure helpers (no network).

The terminal protocol itself is live-verified; here we pin the parsing that
isolates stdout/exit-code from the PTY echo + banner, and the gateway URL regex.
"""
import re

from qzcli.client import endpoints
from qzcli.core import notebook


def test_jupyter_base_re_parses_base_and_token():
    final = (
        "https://nat2-notebook-inspire.sii.edu.cn/ws-abc/project-def/user-ghi/"
        "jupyter/185c38ac-a8ba-492d-906a-9fd8804abbfe/"
        "bc527fb8-258c-42b3-a7fb-ef808e7000dc/lab?token=bc527fb8-258c-42b3-a7fb-ef808e7000dc"
    )
    m = endpoints._JUPYTER_BASE_RE.match(final)
    assert m is not None
    base, token = m.group(1), m.group(2)
    assert token == "bc527fb8-258c-42b3-a7fb-ef808e7000dc"
    assert base.endswith("/jupyter/185c38ac-a8ba-492d-906a-9fd8804abbfe/" + token)
    assert "?" not in base and "/lab" not in base


def test_strip_ansi_removes_csi_and_osc():
    s = "\x1b[0m\x1b[32mhello\x1b[0m\x1b]0;title\x07world\r\n"
    assert notebook._strip_ansi(s) == "helloworld\n"


def test_extract_between_isolates_stdout():
    nonce = "QZX185c38ac1"
    end_re = re.compile(rf"{nonce}EXIT(-?\d+)END")
    # what a real PTY capture looks like: banner, prompt+echo (one line, has
    # nonce), then the two output markers wrapping the command's stdout.
    captured = (
        "welcome banner line\n"
        f"[root:host]$ echo {nonce}START; echo HELLO; uname -sm; echo {nonce}EXIT$?END\n"
        f"{nonce}START\n"
        "HELLO\n"
        "Linux x86_64\n"
        f"{nonce}EXIT0END\n"
        "[root:host]$ "
    )
    out = notebook._extract_between(captured, f"{nonce}START", end_re)
    assert out == "HELLO\nLinux x86_64"


def test_extract_between_empty_when_no_start_marker():
    end_re = re.compile(r"QZXxEXIT(-?\d+)END")
    assert notebook._extract_between("just banner\nno markers\n", "QZXxSTART", end_re) == ""
