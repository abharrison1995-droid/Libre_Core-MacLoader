"""Extract literal attributes from named ASL scopes."""

from collections.abc import Iterator
import re


_TOKEN = re.compile(
    r'(?P<scope>Device|Scope|Processor|Method)\s*\(\s*'
    r'(?P<name>[\\^A-Za-z0-9_.]+)[^)]*\)\s*\{'
    r'|Name\s*\(\s*(?P<attribute>_ADR|_HID)\s*,\s*'
    r'(?:(?P<integer>0x[0-9A-Fa-f]+|\d+|One|Zero)'
    r'|(?:EisaId\s*\(\s*"(?P<eisa>[A-Za-z0-9]+)"\s*\)|"(?P<hid>[A-Za-z0-9]+)"))\s*\)'
    r'|/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|[{}]',
    re.S,
)
_Attributes = list[tuple[str, int | str]]


def dsl_names(dsl: str) -> Iterator[tuple[str, str, _Attributes]]:
    """Yield absolute paths, object kinds and direct literal Name attributes."""
    stack: list[tuple[str, str, _Attributes]] = []
    in_method = 0
    for token in _TOKEN.finditer(dsl):
        kind = token["scope"]
        parent = stack[-1][0] if stack else ""
        if kind:
            name = token["name"]
            if name.startswith("\\"):
                path = name[1:]
            else:
                while name.startswith("^"):
                    parent = parent.rpartition(".")[0]
                    name = name[1:]
                path = ".".join(part for part in (parent, name) if part)
            stack.append((path, kind, []))
            in_method += kind == "Method"
        elif token["attribute"] and stack and stack[-1][1] and not in_method:
            integer = token["integer"]
            value: str | int
            if integer is None:
                value = token["eisa"] or token["hid"]
            elif integer in ("One", "Zero"):
                value = int(integer == "One")
            else:
                value = int(integer, 16 if integer.startswith("0x") else 10)
            stack[-1][2].append((token["attribute"], value))
        elif token[0] == "{":
            stack.append((parent, "", []))
        elif token[0] == "}" and stack:
            path, kind, attributes = stack.pop()
            if kind == "Method":
                in_method -= 1
            elif kind and not in_method:
                yield f"\\{path}", kind, attributes
