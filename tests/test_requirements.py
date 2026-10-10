import hashlib
import json
import unittest
from pathlib import Path

from ai_test_agent.requirements import M1InputError, normalize_source, read_json


def source_document(**overrides):
    source = {
        "source_id": "req-α",
        "source_version": "v1",
        "source_ref": "fixture/requirements.json",
        "permission": "user-approved",
        "model_access": False,
        "sections": [
            {
                "section_id": "s1",
                "title": " Title with spaces ",
                "text": "  Unicode 内容\n  ",
                "locator": "p1",
            },
        ],
    }
    source.update(overrides)
    return source


class RequirementNormalizationTests(unittest.TestCase):
    def test_normalize_preserves_text_and_adds_deterministic_hashes(self):
        source = source_document()
        normalized = normalize_source(source)
        self.assertEqual(normalized["sections"][0]["text"], source["sections"][0]["text"])
        self.assertEqual(
            normalized["sections"][0]["section_sha256"],
            hashlib.sha256(source["sections"][0]["text"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(normalized, normalize_source(source))
        self.assertEqual(normalized["data_class"], "project-document")
        self.assertIn("content_sha256", normalized)

    def test_content_digest_is_canonical_and_contains_data_class(self):
        normalized = normalize_source(source_document())
        digest_payload = dict(normalized)
        digest_payload.pop("content_sha256")
        expected = hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(normalized["content_sha256"], expected)

    def test_hash_tampering_is_rejected(self):
        source = source_document()
        source["sections"][0]["section_sha256"] = "0" * 64
        with self.assertRaises(M1InputError):
            normalize_source(source)
        source = source_document()
        source["content_sha256"] = "0" * 64
        with self.assertRaises(M1InputError):
            normalize_source(source)
        source = source_document()
        source["sections"][0]["section_sha256"] = None
        with self.assertRaises(M1InputError):
            normalize_source(source)

    def test_duplicate_sections_and_unicode_whitespace_are_rejected(self):
        source = source_document(sections=[source_document()["sections"][0].copy()] * 2)
        with self.assertRaises(M1InputError):
            normalize_source(source)
        for field in ("source_id", "source_version", "source_ref"):
            bad = source_document(**{field: "\u2003\u00a0"})
            with self.assertRaises(M1InputError):
                normalize_source(bad)
        for field in ("section_id", "title", "text", "locator"):
            section = source_document()["sections"][0].copy()
            section[field] = "\u2003\u00a0"
            with self.assertRaises(M1InputError):
                normalize_source(source_document(sections=[section]))

    def test_permission_is_closed_and_boolean_is_strict(self):
        with self.assertRaises(M1InputError):
            normalize_source(source_document(permission="admin-approved"))
        with self.assertRaises(M1InputError):
            normalize_source(source_document(model_access=1))
        normalized = normalize_source(source_document(permission="unverified"))
        self.assertEqual(normalized["permission"], "unverified")
        self.assertFalse(normalized["model_access"])

    def test_optional_data_class_is_closed_and_hashed(self):
        normalized = normalize_source(source_document(data_class="synthetic"))
        self.assertEqual(normalized["data_class"], "synthetic")
        with self.assertRaises(M1InputError):
            normalize_source(source_document(data_class="secret"))


class StrictJsonReadTests(unittest.TestCase):
    def test_read_json_accepts_any_json_value(self):
        path = Path(".local") / "requirements-test-array.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, {\"x\": true}]", encoding="utf-8")
        self.assertEqual(read_json(path), [1, {"x": True}])

    def test_read_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        path = Path(".local") / "requirements-test-invalid.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"x": 1, "x": 2}', encoding="utf-8")
        with self.assertRaises(M1InputError):
            read_json(path)
        path.write_text("{\"x\": NaN}", encoding="utf-8")
        with self.assertRaises(M1InputError):
            read_json(path)
        path.write_text("{\"x\": 1e400}", encoding="utf-8")
        with self.assertRaises(M1InputError):
            read_json(path)


if __name__ == "__main__":
    unittest.main()
