"""Request-scoped context (user id) for tools that need it."""

from __future__ import annotations

from contextvars import ContextVar

_user_id: ContextVar[str | None] = ContextVar("nyvexa_user_id", default=None)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> str | None:
    return _user_id.get()
