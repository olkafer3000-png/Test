import json
import unittest

from jdt.json_schema import convert_jdt_to_json_schema, dumps_json_schema


class JsonSchemaConverterTests(unittest.TestCase):
    def test_converts_nested_properties_and_constraints(self):
        result = convert_jdt_to_json_schema(
            """is closed
user:
    name is string, required, longer 0
    age is number, minimum 0, maximum 120
    zip is number, not null
    hobbies is array(string)
"""
        )

        user = result["properties"]["user"]
        self.assertEqual(user["type"], "object")
        self.assertEqual(user["required"], ["name"])
        self.assertEqual(user["additionalProperties"], False)
        self.assertEqual(
            user["properties"]["name"],
            {"type": "string", "minLength": 1},
        )
        self.assertEqual(
            user["properties"]["age"],
            {"type": "number", "minimum": 0, "maximum": 120},
        )
        self.assertEqual(user["properties"]["zip"], {"type": "number"})
        self.assertEqual(user["properties"]["hobbies"], {
            "type": "array",
            "items": {"type": "string"},
        })
        self.assertFalse(result.get("additionalProperties", True))

    def test_converts_custom_types_and_root_metadata(self):
        result = convert_jdt_to_json_schema(
            """is schema "https://example.test/schema"
is version "https://example.test/v1"
define Address:
    city is string, required
person:
    address is Address, required
"""
        )

        self.assertEqual(
            result["properties"]["$schema"],
            {"const": "https://example.test/schema"},
        )
        self.assertEqual(
            result["properties"]["$version"],
            {"const": "https://example.test/v1"},
        )
        self.assertEqual(result["properties"]["person"]["required"], ["address"])
        self.assertEqual(
            result["properties"]["person"]["properties"]["address"],
            {"$ref": "#/$defs/Address"},
        )
        self.assertEqual(result["$defs"]["Address"]["required"], ["city"])

    def test_converts_logic_literals_patterns_and_numeric_bounds(self):
        result = convert_jdt_to_json_schema(
            """state is ("active" or "inactive")
count is number, larger 0, smaller 10
code is string, match(""" + '"""[A-Z]+"""' + """)
"""
        )

        self.assertEqual(
            result["properties"]["state"],
            {"type": "string", "enum": ["active", "inactive"]},
        )
        self.assertEqual(
            result["properties"]["count"],
            {
                "type": "number",
                "exclusiveMinimum": 0,
                "exclusiveMaximum": 10,
            },
        )
        self.assertEqual(
            result["properties"]["code"],
            {"type": "string", "pattern": "^(?:[A-Z]+)$"},
        )

    def test_converts_length_constraints_for_integer_digit_counts(self):
        result = convert_jdt_to_json_schema(
            """long_value is number, longer 13
short_value is number, shorter 13
"""
        )

        self.assertEqual(
            result["properties"]["long_value"],
            {
                "type": "integer",
                "not": {
                    "minimum": -(10**13 - 1),
                    "maximum": 10**13 - 1,
                },
            },
        )
        self.assertEqual(
            result["properties"]["short_value"],
            {
                "type": "integer",
                "exclusiveMinimum": -(10**12),
                "exclusiveMaximum": 10**12,
            },
        )

    def test_simplifies_multiple_integer_digit_limits_without_rounding(self):
        result = convert_jdt_to_json_schema(
            "number is number, longer 13, shorter 19"
        )

        self.assertEqual(
            result["properties"]["number"],
            {
                "type": "integer",
                "not": {
                    "minimum": -(10**13 - 1),
                    "maximum": 10**13 - 1,
                },
                "exclusiveMinimum": -(10**18),
                "exclusiveMaximum": 10**18,
            },
        )

    def test_collapses_literal_alternatives_and_removes_empty_branch(self):
        result = convert_jdt_to_json_schema(
            'color is string, "blue" or "green" or "brown", optional'
        )

        self.assertEqual(
            result["properties"]["color"],
            {"type": "string", "enum": ["blue", "green", "brown"]},
        )

    def test_rejects_references_to_undefined_types(self):
        with self.assertRaisesRegex(ValueError, "Undefined JDT data type: Missing"):
            convert_jdt_to_json_schema("value is Missing")

    def test_serializes_as_valid_json_and_handles_array_roots(self):
        encoded = dumps_json_schema("is type array")
        self.assertEqual(json.loads(encoded)["type"], "array")
        self.assertEqual(
            convert_jdt_to_json_schema("is type array")["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )


if __name__ == "__main__":
    unittest.main()
