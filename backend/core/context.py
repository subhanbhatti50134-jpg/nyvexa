"""Request-scoped context (user id, memory block) for tools and prompts."""

from __future__ import annotations

from contextvars import ContextVar

_user_id: ContextVar[str | None] = ContextVar("nyvexa_user_id", default=None)
_memory_block: ContextVar[str | None] = ContextVar("nyvexa_memory_block", default=None)


def set_user_id(user_id: str | None) -> None:
    _user_id.set(user_id)


def get_user_id() -> str | None:
    return _user_id.get()


def set_memory_block(block: str | None) -> None:
    _memory_block.set(block or None)


def get_memory_block() -> str | None:
    return _memory_block.get()
