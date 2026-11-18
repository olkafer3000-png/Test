"""Convert JDT schemas to JSON Schema Draft 2020-12."""

import json
import math
from typing import Any, Dict, List, Tuple

from .parser import Constraint, Parser, PropertyDef, Schema


JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _false_schema() -> Dict[str, Any]:
    return {"not": {}}


def _is_false_schema(schema: Dict[str, Any]) -> bool:
    return schema == _false_schema()


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def _matches_json_type(value: Any, value_type: str) -> bool:
    if value_type == "null":
        return value is None
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "integer":
        return (
            isinstance(value, int) and not isinstance(value, bool)
        ) or (
            isinstance(value, float) and not isinstance(value, bool) and value.is_integer()
        )
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "array":
        return isinstance(value, list)
    if value_type == "object":
        return isinstance(value, dict)
    return False


def _same_json_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _combine_any_of(schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
    flattened = []
    for schema in schemas:
        if not schema:
            return {}
        if _is_false_schema(schema):
            continue
        if set(schema) == {"anyOf"}:
            flattened.extend(schema["anyOf"])
        else:
            flattened.append(schema)

    if not flattened:
        return _false_schema()
    if len(flattened) == 1:
        return flattened[0]

    enum_values = []
    enum_compatible = True
    for schema in flattened:
        if "const" in schema and set(schema).issubset({"type", "const"}):
            enum_values.append(schema["const"])
        elif "enum" in schema and set(schema).issubset({"type", "enum"}):
            enum_values.extend(schema["enum"])
        else:
            enum_compatible = False
            break
    if enum_compatible:
        result: Dict[str, Any] = {"enum": enum_values}
        value_types = {_json_type(value) for value in enum_values}
        if len(value_types) == 1:
            result["type"] = value_types.pop()
        return result

    return {"anyOf": flattened}


def _combine_all_of(schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
    flattened = []
    for schema in schemas:
        if _is_false_schema(schema):
            return _false_schema()
        if not schema:
            continue
        if set(schema) == {"allOf"}:
            flattened.extend(schema["allOf"])
        else:
            flattened.append(schema)

    if not flattened:
        return {}
    if len(flattened) == 1:
        return flattened[0]

    types = {schema["type"] for schema in flattened if "type" in schema}
    if types.issubset({"number", "integer"}):
        value_type = "integer" if "integer" in types else "number"
    elif len(types) <= 1:
        value_type = next(iter(types), None)
    else:
        return _false_schema()

    if value_type not in (None, "null"):
        flattened = [
            schema
            for schema in flattened
            if schema.get("not") != {"type": "null"}
        ]

    result: Dict[str, Any] = {}
    for schema in flattened:
        for key, value in schema.items():
            if key == "type":
                result["type"] = value_type
            elif key in ("minimum", "exclusiveMinimum", "minLength", "minItems"):
                result[key] = max(result.get(key, value), value)
            elif key in ("maximum", "exclusiveMaximum", "maxLength", "maxItems"):
                result[key] = min(result.get(key, value), value)
            elif key in result:
                if key == "const":
                    if not _same_json_value(result[key], value):
                        return _false_schema()
                    continue
                if key == "enum":
                    result[key] = [
                        item
                        for item in result[key]
                        if any(_same_json_value(item, candidate) for candidate in value)
                    ]
                    if not result[key]:
                        return _false_schema()
                    continue
                if result[key] != value:
                    return {"allOf": flattened}
            else:
                result[key] = value
    if value_type is not None and "const" in result:
        if not _matches_json_type(result["const"], value_type):
            return _false_schema()
    if value_type is not None and "enum" in result:
        result["enum"] = [
            item for item in result["enum"] if _matches_json_type(item, value_type)
        ]
        if not result["enum"]:
            return _false_schema()
    return result


def _combine(keyword: str, schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
    if keyword == "allOf":
        return _combine_all_of(schemas)
    if keyword == "anyOf":
        return _combine_any_of(schemas)
    if not schemas:
        return {}
    if len(schemas) == 1:
        return schemas[0]
    return {keyword: schemas}


def _constraint_type_hint(constraint: Constraint) -> Any:
    if constraint.type in ("string", "number", "boolean", "null", "array"):
        return constraint.type
    if constraint.type == "literal":
        return _json_type(constraint.value)
    if constraint.type == "true" or constraint.type == "false":
        return "boolean"
    if constraint.type == "type":
        return "object"
    if constraint.type == "and":
        hints = {_constraint_type_hint(child) for child in constraint.children}
        hints.discard(None)
        if len(hints) == 1:
            return hints.pop()
    if constraint.type == "or":
        hints = [_constraint_type_hint(child) for child in constraint.children]
        if hints and all(hint is not None for hint in hints) and len(set(hints)) == 1:
            return hints[0]
    return None


def _flatten_conjunction(constraint: Constraint) -> List[Constraint]:
    if constraint.type != "and":
        return [constraint]
    flattened = []
    for child in constraint.children:
        flattened.extend(_flatten_conjunction(child))
    return flattened


def _split_presence(constraint: Constraint, value_type: Any = None) -> Tuple[Dict[str, Any], bool]:
    """Separate required from conjunctions, where JDT uses it as an occurrence rule."""
    if constraint.type == "required":
        return {}, True
    if constraint.type == "optional":
        return {}, False
    if constraint.type == "and":
        value_type = value_type or _constraint_type_hint(constraint)
        schemas = []
        is_required = False
        children = _flatten_conjunction(constraint)
        numeric_lengths = [
            child
            for child in children
            if value_type == "number" and child.type in ("longer", "shorter")
        ]
        if numeric_lengths:
            schemas.append(_numeric_digit_schema(numeric_lengths))
        for child in children:
            if child in numeric_lengths:
                continue
            schema, child_required = _split_presence(child, value_type)
            if schema:
                schemas.append(schema)
            is_required = is_required or child_required
        return _combine("allOf", schemas), is_required
    return _constraint_schema(constraint, value_type), False


def _numeric_digit_schema(constraints: List[Constraint]) -> Dict[str, Any]:
    minimum_digits = 1
    maximum_digits = None
    for constraint in constraints:
        if constraint.type == "longer":
            minimum_digits = max(minimum_digits, math.floor(constraint.value) + 1)
        else:
            limit_digits = math.ceil(constraint.value) - 1
            maximum_digits = (
                limit_digits
                if maximum_digits is None
                else min(maximum_digits, limit_digits)
            )

    if maximum_digits is not None and (
        maximum_digits < 1 or minimum_digits > maximum_digits
    ):
        return _false_schema()

    if minimum_digits == 1:
        integer_schema: Dict[str, Any] = {"type": "integer"}
        if maximum_digits is not None:
            threshold = 10**maximum_digits
            integer_schema["exclusiveMinimum"] = -threshold
            integer_schema["exclusiveMaximum"] = threshold
        return integer_schema

    minimum_value = 10 ** (minimum_digits - 1)
    integer_schema = {
        "type": "integer",
        "not": {
            "minimum": -(minimum_value - 1),
            "maximum": minimum_value - 1,
        },
    }
    if maximum_digits is not None:
        threshold = 10**maximum_digits
        integer_schema["exclusiveMinimum"] = -threshold
        integer_schema["exclusiveMaximum"] = threshold
    return integer_schema


def _length_constraint_schema(
    kind: str, limit: Any, value_type: Any = None
) -> Dict[str, Any]:
    if value_type == "number":
        return _numeric_digit_schema([Constraint(kind, value=limit)])
    if value_type == "string":
        if kind == "longer":
            return {"type": "string", "minLength": max(0, math.floor(limit) + 1)}
        maximum_length = math.ceil(limit) - 1
        return (
            {"type": "string", "maxLength": maximum_length}
            if maximum_length >= 0
            else _false_schema()
        )
    if value_type == "array":
        if kind == "longer":
            return {"type": "array", "minItems": max(0, math.floor(limit) + 1)}
        maximum_length = math.ceil(limit) - 1
        return (
            {"type": "array", "maxItems": maximum_length}
            if maximum_length >= 0
            else _false_schema()
        )
    if value_type in ("boolean", "null", "object"):
        return _false_schema()

    schemas: List[Dict[str, Any]] = []
    if kind == "longer":
        minimum_length = max(0, math.floor(limit) + 1)
        schemas.extend(
            [
                {"type": "string", "minLength": minimum_length},
                {"type": "array", "minItems": minimum_length},
            ]
        )

        schemas.append(_numeric_digit_schema([Constraint(kind, value=limit)]))
    else:
        maximum_length = math.ceil(limit) - 1
        if maximum_length >= 0:
            schemas.extend(
                [
                    {"type": "string", "maxLength": maximum_length},
                    {"type": "array", "maxItems": maximum_length},
                ]
            )

        schemas.append(_numeric_digit_schema([Constraint(kind, value=limit)]))

    return _combine("anyOf", schemas) if schemas else {"not": {}}


def _constraint_schema(
    constraint: Constraint, value_type: Any = None
) -> Dict[str, Any]:
    kind = constraint.type

    if kind in ("string", "number", "boolean"):
        return {"type": kind}
    if kind == "null":
        return {"type": "null"}
    if kind == "true":
        return {"const": True}
    if kind == "false":
        return {"const": False}
    if kind == "literal":
        return {"const": constraint.value}
    if kind == "required":
        return {"not": {"type": "null"}}
    if kind == "optional":
        return {}
    if kind == "and":
        value_type = value_type or _constraint_type_hint(constraint)
        children = _flatten_conjunction(constraint)
        numeric_lengths = [
            child
            for child in children
            if value_type == "number" and child.type in ("longer", "shorter")
        ]
        schemas = [
            _numeric_digit_schema(numeric_lengths)
        ] if numeric_lengths else []
        schemas.extend(
            _constraint_schema(child, value_type)
            for child in children
            if child not in numeric_lengths
        )
        return _combine("allOf", schemas)
    if kind == "or":
        value_type = value_type or _constraint_type_hint(constraint)
        return _combine(
            "anyOf",
            [_constraint_schema(child, value_type) for child in constraint.children],
        )
    if kind == "not":
        return {"not": _constraint_schema(constraint.children[0], value_type)}
    if kind == "array":
        result: Dict[str, Any] = {"type": "array"}
        if constraint.value is not None:
            result["items"] = _constraint_schema(constraint.value)
        return result
    if kind == "minimum":
        return {"minimum": constraint.value}
    if kind == "maximum":
        return {"maximum": constraint.value}
    if kind == "larger":
        return {"exclusiveMinimum": constraint.value}
    if kind == "smaller":
        return {"exclusiveMaximum": constraint.value}
    if kind == "longer":
        return _length_constraint_schema(kind, constraint.value, value_type)
    if kind == "shorter":
        return _length_constraint_schema(kind, constraint.value, value_type)
    if kind == "match":
        return {"type": "string", "pattern": "^(?:" + constraint.value + ")$"}
    if kind == "type":
        escaped_name = str(constraint.value).replace("~", "~0").replace("/", "~1")
        return {"$ref": "#/$defs/" + escaped_name}

    raise ValueError("Unsupported JDT constraint: {}".format(kind))


def _object_schema(
    properties: Dict[str, PropertyDef], inherited_closed: bool = False
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"type": "object"}
    json_properties: Dict[str, Any] = {}
    required: List[str] = []
    if "$closed" in properties:
        closed = True
    elif "$open" in properties:
        closed = False
    else:
        closed = inherited_closed

    for name, prop in properties.items():
        if name.startswith("$"):
            continue
        if prop.is_container:
            child_schema = _object_schema(prop.children, closed)
            child_required = False
        elif prop.constraint is not None:
            child_schema, child_required = _split_presence(prop.constraint)
        else:
            child_schema, child_required = {}, False

        json_properties[name] = child_schema
        if child_required:
            required.append(name)

    if json_properties:
        result["properties"] = json_properties
    if required:
        result["required"] = required
    if closed:
        result["additionalProperties"] = False
    return result


def _validate_custom_references(schema: Schema) -> None:
    def visit_constraint(constraint: Constraint) -> None:
        if constraint.type == "type" and constraint.value not in schema.custom_types:
            raise ValueError("Undefined JDT data type: {}".format(constraint.value))
        if constraint.type == "array" and constraint.value is not None:
            visit_constraint(constraint.value)
        for child in constraint.children:
            visit_constraint(child)

    def visit_properties(properties: Dict[str, PropertyDef]) -> None:
        for prop in properties.values():
            if prop.constraint is not None:
                visit_constraint(prop.constraint)
            visit_properties(prop.children)

    visit_properties(schema.properties)
    for type_def in schema.custom_types.values():
        visit_properties(type_def.children)


def convert_jdt_to_json_schema(schema_text: str) -> Dict[str, Any]:
    """Parse JDT source and return its equivalent JSON Schema as a dictionary."""
    schema: Schema = Parser(schema_text).parse()
    _validate_custom_references(schema)
    result: Dict[str, Any] = {
        "$schema": JSON_SCHEMA_DRAFT,
        "type": schema.root_type,
    }

    if schema.root_type == "array":
        return result

    root = _object_schema(schema.properties, schema.closed)
    result.update(root)

    root_properties = result.setdefault("properties", {})
    for keyword, uri in (
        ("$schema", schema.schema_uri),
        ("$version", schema.version_uri),
        ("$owner", schema.owner_uri),
    ):
        if uri is not None:
            root_properties[keyword] = {"const": uri}

    if schema.custom_types:
        result["$defs"] = {
            name: _object_schema(type_def.children, schema.closed)
            for name, type_def in schema.custom_types.items()
        }

    return result


def dumps_json_schema(schema_text: str, indent: int = 2) -> str:
    """Convert JDT source and serialize the resulting JSON Schema."""
    return json.dumps(convert_jdt_to_json_schema(schema_text), indent=indent, ensure_ascii=False)


def main() -> None:
    """Convert a JDT schema file (or stdin) and write JSON Schema to stdout."""
    import argparse
    import sys

    argument_parser = argparse.ArgumentParser(description="Convert a JDT schema to JSON Schema.")
    argument_parser.add_argument("file", nargs="?", help="JDT schema file; reads stdin if omitted")
    argument_parser.add_argument("--indent", type=int, default=2, help="JSON indentation (default: 2)")
    args = argument_parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as source_file:
            source = source_file.read()
    else:
        source = sys.stdin.read()
    print(dumps_json_schema(source, indent=args.indent))


__all__ = ["convert_jdt_to_json_schema", "dumps_json_schema", "JSON_SCHEMA_DRAFT"]
