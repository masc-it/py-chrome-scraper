"""Shared CLI output helpers: JSON logging."""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(payload: dict[str, Any], stream: Any = sys.stdout) -> None:
    """Write a JSON document (pretty, unicode-safe) + trailing newline."""
    json.dump(payload, stream, indent=2, ensure_ascii=False)
    stream.write("\n")


def emit_ok(payload: dict[str, Any], stream: Any = sys.stdout) -> None:
    """Emit a success envelope (``ok: true``) merged with ``payload``."""
    emit({"ok": True, **payload}, stream=stream)


def emit_error(message: str, *, stream: Any = sys.stderr, **extra: Any) -> None:
    """Emit a failure envelope (``ok: false``, ``error: message``) to stderr."""
    emit({"ok": False, "error": message, **extra}, stream=stream)
