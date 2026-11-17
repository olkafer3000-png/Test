"""Convert JDT schemas to JSON Schema Draft 2020-12."""

import json
from typing import Any, Dict, List, Tuple

from .parser import Constraint, Parser, PropertyDef, Schema


JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _combine(keyword: str, schemas: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not schemas:
        return {}
    if keyword == "allOf":
        flattened = []
        for schema in schemas:
            if set(schema) == {"allOf"}:
                flattened.extend(schema["allOf"])
            else:
                flattened.append(schema)
        schemas = flattened
    if len(schemas) == 1:
        return schemas[0]
    return {keyword: schemas}


def _split_presence(constraint: Constraint) -> Tuple[Dict[str, Any], bool]:
    """Separate required from conjunctions, where JDT uses it as an occurrence rule."""
    if constraint.type == "required":
        return {}, True
    if constraint.type == "optional":
        return {}, False
    if constraint.type == "and":
        schemas = []
        is_required = False
        for child in constraint.children:
            schema, child_required = _split_presence(child)
            if schema:
                schemas.append(schema)
            is_required = is_required or child_required
        return _combine("allOf", schemas), is_required
    return _constraint_schema(constraint), False


def _constraint_schema(constraint: Constraint) -> Dict[str, Any]:
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
        return _combine("allOf", [_constraint_schema(child) for child in constraint.children])
    if kind == "or":
        return _combine("anyOf", [_constraint_schema(child) for child in constraint.children])
    if kind == "not":
        return {"not": _constraint_schema(constraint.children[0])}
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
        return {"minLength": constraint.value, "minItems": constraint.value}
    if kind == "shorter":
        return {"maxLength": constraint.value, "maxItems": constraint.value}
    if kind == "match":
        return {"pattern": "^(?:" + constraint.value + ")$"}
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
