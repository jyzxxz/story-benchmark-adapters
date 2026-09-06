from __future__ import annotations

import re


class JsonStringFieldStream:
    """Incrementally decode one JSON string field from streamed JSON text."""

    def __init__(self, field_name: str, *, search_limit: int = 65536) -> None:
        self._pattern = re.compile(rf'"{re.escape(field_name)}"\s*:\s*"')
        self._search_limit = search_limit
        self._search_buffer = ""
        self._started = False
        self._finished = False
        self._escaped = False
        self._unicode_digits: list[str] | None = None

    @property
    def finished(self) -> bool:
        return self._finished

    def feed(self, chunk: str) -> str:
        if not chunk or self._finished:
            return ""
        if not self._started:
            self._search_buffer += chunk
            match = self._pattern.search(self._search_buffer)
            if not match:
                if len(self._search_buffer) > self._search_limit:
                    raise ValueError("streamed JSON content field not found")
                return ""
            chunk = self._search_buffer[match.end() :]
            self._search_buffer = ""
            self._started = True

        output: list[str] = []
        escape_map = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        for char in chunk:
            if self._unicode_digits is not None:
                if char.lower() not in "0123456789abcdef":
                    raise ValueError("invalid unicode escape in streamed JSON")
                self._unicode_digits.append(char)
                if len(self._unicode_digits) == 4:
                    output.append(chr(int("".join(self._unicode_digits), 16)))
                    self._unicode_digits = None
                continue
            if self._escaped:
                self._escaped = False
                if char == "u":
                    self._unicode_digits = []
                elif char in escape_map:
                    output.append(escape_map[char])
                else:
                    raise ValueError("invalid escape in streamed JSON")
                continue
            if char == "\\":
                self._escaped = True
            elif char == '"':
                self._finished = True
                break
            else:
                output.append(char)
        return "".join(output)

