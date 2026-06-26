"""Unit tests for the `nb exec` pure helpers (no network).

The terminal protocol itself is live-verified; here we pin the parsing that
isolates stdout/exit-code from the PTY echo + banner, and the gateway URL regex.
"""
import base64
import re

from qzcli.client import endpoints
from qzcli.cli import _build_exec_command
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


def test_extractor_isolates_stdout_and_exit_code():
    nonce = "QZX185c38ac1"
    end_re = re.compile(rf"{nonce}EXIT(-?\d+)END")
    captured = (
        "welcome banner line\n"
        f"[root:host]$ echo {nonce}START; echo HELLO; uname -sm; echo {nonce}EXIT$?END\n"
        f"{nonce}START\n"
        "HELLO\n"
        "Linux x86_64\n"
        f"{nonce}EXIT0END\n"
        "[root:host]$ "
    )
    e = notebook._Extractor(f"{nonce}START", end_re)
    e.feed(captured)
    assert e.done
    assert e.exit_code == 0
    assert e.stdout == "HELLO\nLinux x86_64"


def test_extractor_handles_chunked_feed_and_streams_each_line():
    nonce = "QZX42"
    end_re = re.compile(rf"{nonce}EXIT(-?\d+)END")
    streamed: list[str] = []
    e = notebook._Extractor(f"{nonce}START", end_re, on_line=streamed.append)
    # arbitrarily-split chunks: partial lines, multi-line, etc.
    for chunk in [
        f"[root:host]$ echo {nonce}START; cmd; echo {nonce}EXIT$?END\n",
        f"{nonce}START\nlin",  # split mid-line
        "e1\nline2\n",
        f"line3\n{nonce}EXIT7END\ntrailing prompt",
    ]:
        e.feed(chunk)
    assert e.done
    assert e.exit_code == 7
    # streamed lines arrive in order, no markers, no PTY-echo line:
    assert streamed == ["line1", "line2", "line3"]
    assert e.stdout == "line1\nline2\nline3"


def test_extractor_empty_when_no_start_marker():
    end_re = re.compile(r"QZXxEXIT(-?\d+)END")
    e = notebook._Extractor("QZXxSTART", end_re)
    e.feed("just banner\nno markers\n")
    assert not e.done
    assert e.exit_code is None
    assert e.stdout == ""


# --- command reconstruction (issue #1: the quoting mangle) ----------------

def test_build_exec_single_arg_passthrough():
    # One arg = a pre-formed shell string; pipelines/redirs survive verbatim.
    assert _build_exec_command(["ls | wc -l && echo done"]) == "ls | wc -l && echo done"


def test_build_exec_strips_leading_dashdash():
    assert _build_exec_command(["--", "ls | wc -l"]) == "ls | wc -l"


def test_build_exec_multi_arg_preserves_grouping():
    # The case that was broken: `bash -lc 'script'` must NOT collapse to
    # `bash -lc script` (which drops the script body).
    parts = ["bash", "-lc", "for i in 1 2 3; do echo $i; done"]
    cmd = _build_exec_command(parts)
    assert cmd == "bash -lc 'for i in 1 2 3; do echo $i; done'"
    # The third arg stays a single token to the remote shell.
    import shlex
    assert shlex.split(cmd) == parts


def test_build_exec_multi_arg_quotes_spaces():
    assert _build_exec_command(["echo", "hello world"]) == "echo 'hello world'"


# --- nb exec --detach wrapper ---------------------------------------------

def test_detach_wrapper_embeds_command_and_emits_handle():
    user_cmd = "for i in 1 2 3; do echo $i; done"
    b64 = base64.b64encode(user_cmd.encode()).decode()
    w = notebook._detach_wrapper("nb12-abc", b64, "$HOME/.qzcli/runs")
    # command is carried base64-encoded (survives any quoting), decoded to cmd.sh
    assert b64 in w
    assert 'base64 -d > "$RUN_DIR/cmd.sh"' in w
    # detaches under setsid, away from the terminal's process group
    assert "setsid bash -c" in w
    assert "</dev/null >/dev/null 2>&1 &" in w
    # records exit code and prints a machine-readable handle line
    assert 'echo $? > "$0/exit_code"' in w
    assert "QZDETACH " in w
    assert "nb12-abc" in w


def test_detach_handle_regex_parses_json():
    line = (
        'some banner\n'
        'QZDETACH {"run_id":"r1","pid":4242,"run_dir":"/root/.qzcli/runs/r1",'
        '"log":"/root/.qzcli/runs/r1/log","exit_code_file":"/root/.qzcli/runs/r1/exit_code"}\n'
        'trailing'
    )
    m = notebook._DETACH_RE.search(line)
    assert m is not None
    import json
    h = json.loads(m.group(1))
    assert h["pid"] == 4242 and h["run_id"] == "r1"


def test_new_run_id_is_stable_shape():
    rid = notebook._new_run_id("fef12c1a-6614-4cb7-8a89")
    head, _, tail = rid.partition("-")
    assert head == "fef12c1a"  # hex prefix from the notebook id
    assert tail and all(c in "0123456789abcdef" for c in tail)
