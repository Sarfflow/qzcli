"""Error model.

Every failure path raises :class:`QzError`. It carries enough structure that
the CLI can emit a stable JSON envelope an agent can act on:

    {"ok": false, "error": {"code", "message", "hint", "candidates"}}

Principle #1 (极致执行反馈): an error must say *what* is wrong and *what to do
next* — never a bare code, never a silent failure. ``hint`` is the next action
to take; ``candidates`` lists the currently-legal choices when the failure is a
bad/missing selection.
"""

from __future__ import annotations

from typing import Any, Optional


class QzError(Exception):
    """A failure that maps directly to the JSON error envelope.

    Args:
        message: human/agent-readable description of what went wrong.
        code: short machine-stable token (e.g. ``"auth_required"``,
            ``"invalid_option"``, ``"http_error"``). Defaults to ``"error"``.
        hint: the concrete next step (e.g. "run: qzcli login").
        candidates: the currently-legal choices, when the error is a bad or
            missing selection. Each item is whatever the relevant ``options``
            command would return, so the agent can pick one and retry.
        http_status: the HTTP status code, when the failure came from a request.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        hint: Optional[str] = None,
        candidates: Optional[list[Any]] = None,
        http_status: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint
        self.candidates = candidates
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.candidates is not None:
            err["candidates"] = self.candidates
        if self.http_status is not None:
            err["http_status"] = self.http_status
        return err
