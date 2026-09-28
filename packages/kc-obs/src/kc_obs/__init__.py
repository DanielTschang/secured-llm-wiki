"""Structured logging that can only carry IDs and numbers (invariant 7).

Values must be an ID type (SpaceId, PageId, AttachmentId), a number or a bool.
Exceptions (only in the `error` field) are logged by type name only, never by message
or traceback, because messages and tracebacks can contain content.
"""

import json
import logging
import re
from enum import Enum

from kc_ids import AttachmentId, PageId
from kc_labels import SpaceId

__all__ = ["IdLogger", "get_logger"]

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ID_TYPES = (SpaceId, PageId, AttachmentId)

type Value = SpaceId | PageId | AttachmentId | int | float | bool | BaseException | Enum


def _check_name(name: str) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("log names must be lowercase identifiers")
    return name


def _render(key: str, value: object) -> str | int | float | bool:
    if isinstance(value, _ID_TYPES):
        return str(value)
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, BaseException) and key == "error":
        return type(value).__name__
    if isinstance(value, Enum) and isinstance(value.value, str):
        return _check_name(value.value)
    # Do not describe the value: it may be content.
    raise TypeError("log values must be IDs or numbers")


class IdLogger:
    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(f"kc.{_check_name(name)}")

    def _emit(self, level: int, event: str, fields: dict[str, Value]) -> None:
        record: dict[str, str | int | float | bool] = {"event": _check_name(event)}
        for key, value in fields.items():
            record[_check_name(key)] = _render(key, value)
        self._log.log(level, json.dumps(record, ensure_ascii=True))

    def info(self, event: str, **fields: Value) -> None:
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Value) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Value) -> None:
        self._emit(logging.ERROR, event, fields)


def get_logger(name: str) -> IdLogger:
    return IdLogger(name)
