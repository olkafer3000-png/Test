import unittest

from app import app


class JsonSchemaConverterRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_validator_is_default_and_converter_is_selectable(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<option value="validator" selected>Validator</option>', html)
        self.assertIn('id="validatorView"', html)
        self.assertIn('id="converterView" aria-label="Converter" hidden', html)
        self.assertIn('id="convertBtn"', html)

    def test_converts_jdt_source_to_json_schema(self):
        response = self.client.post(
            "/convert",
            json={"schema": "name is string, required"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result["success"])
        self.assertEqual(result["schema"]["properties"]["name"], {"type": "string"})
        self.assertEqual(result["schema"]["required"], ["name"])

    def test_reports_invalid_or_missing_source(self):
        for payload in ({}, {"schema": "value is Missing"}):
            with self.subTest(payload=payload):
                response = self.client.post("/convert", json=payload)

                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["success"])
                self.assertTrue(response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
