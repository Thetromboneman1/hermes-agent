#!/usr/bin/env python3
"""Filter numbered diff candidates without mistaking text I/O for encoded commands.

The workflow retains its broad candidate regex. Only an unambiguous Python call
whose encoding markers belong exclusively to UTF-8 stdin encoding or captured
stdout/stderr decoding is removed. Unknown syntax and other markers stay critical.
"""

from __future__ import annotations

import ast
import re
import sys

_NUMBERED_ADDITION = re.compile(r"^\d+:\+(.*)$")
_ENCODING_MARKER = re.compile(r"base64|decode|encode|\\x|chr\(", re.IGNORECASE)


def _is_subprocess_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in {"Popen", "call", "run"}
    )


def _utf8_method(value: ast.AST, method: str) -> ast.Attribute | None:
    if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and value.func.attr == method and not value.keywords):
        return None
    if len(value.args) > 2 or any(not isinstance(arg, ast.Constant) or not isinstance(arg.value, str)
                                  for arg in value.args):
        return None
    if value.args and value.args[0].value not in {"utf-8", "utf8", "utf_8"}:
        return None
    return value.func


def _command_argument(call: ast.Call) -> ast.AST | None:
    if any(isinstance(node, ast.Starred) for node in ast.walk(call)):
        return None
    names = [keyword.arg for keyword in call.keywords]
    if None in names or len(set(names)) != len(names):
        return None
    named_args = [keyword.value for keyword in call.keywords if keyword.arg == "args"]
    if (call.args and named_args) or not (call.args or named_args):
        return None
    return call.args[0] if call.args else named_args[0]


def _text_io_attributes(tree: ast.Module, call: ast.Call) -> list[ast.Attribute] | None:
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
        return None
    result = tree.body[0].value
    direct_result = result is call or (
        isinstance(result, ast.Attribute) and result.value is call
        and result.attr in {"returncode", "stdout", "stderr"}
    )
    attributes = []
    if not direct_result:
        output_decode = _utf8_method(result, "decode")
        receiver = output_decode.value if output_decode is not None else None
        if not (isinstance(receiver, ast.Attribute) and receiver.attr in {"stdout", "stderr"}
                and receiver.value is call):
            return None
        attributes.append(output_decode)
    for keyword in call.keywords:
        if keyword.arg == "input" and (attribute := _utf8_method(keyword.value, "encode")) is not None:
            attributes.append(attribute)
    return attributes


def is_critical_candidate(line: str) -> bool:
    """Keep a candidate unless every encoding marker is proven to be text I/O."""
    match = _NUMBERED_ADDITION.fullmatch(line.rstrip("\r\n"))
    if match is None:
        return True
    source = match[1].lstrip()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return True
    calls = [node for node in ast.walk(tree) if _is_subprocess_call(node)]
    if len(calls) != 1:
        return True
    call = calls[0]
    command = _command_argument(call)
    if command is None or _ENCODING_MARKER.search(ast.get_source_segment(source, command) or ""):
        return True
    text_io_attributes = _text_io_attributes(tree, call)
    if text_io_attributes is None:
        return True

    # AST columns are UTF-8 byte offsets. Mask only the method name, preserving
    # markers in receivers, arguments, comments, and other same-line expressions.
    masked = bytearray(source.encode("utf-8"))
    for attribute in text_io_attributes:
        end = attribute.end_col_offset
        start = end - len(attribute.attr)
        masked[start:end] = b" " * (end - start)
    return _ENCODING_MARKER.search(masked.decode("utf-8")) is not None


def main() -> None:
    for line in sys.stdin:
        if line.strip() and is_critical_candidate(line):
            sys.stdout.write(line)


if __name__ == "__main__":
    main()
