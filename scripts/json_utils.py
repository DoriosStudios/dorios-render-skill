#!/usr/bin/env python3
"""Small JSONC reader for Minecraft asset files that contain comments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_json_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            output.extend("  ")
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            output.extend("  ")
            index += 2
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    output.extend("  ")
                    index += 2
                    break
                output.append("\n" if text[index] == "\n" else " ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def load_jsonc(path: Path) -> dict[str, Any]:
    return json.loads(strip_json_comments(path.read_text(encoding="utf-8-sig")))
