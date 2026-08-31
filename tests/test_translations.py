"""Check that shipped translation structures stay aligned."""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "synology_surveillance_station"


def key_shape(value, prefix=""):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            result.add(path)
            result.update(key_shape(child, path))
    return result


class TranslationTests(unittest.TestCase):
    def test_translation_keys_match_strings(self) -> None:
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        expected = key_shape(strings)
        for language in ("en", "cs"):
            translated = json.loads(
                (COMPONENT / "translations" / f"{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(expected, key_shape(translated), language)

    def test_metadata_is_valid_json(self) -> None:
        for filename in ("manifest.json", "strings.json", "icons.json"):
            self.assertIsInstance(
                json.loads((COMPONENT / filename).read_text(encoding="utf-8")), dict
            )
        self.assertIsInstance(
            json.loads((ROOT / "hacs.json").read_text(encoding="utf-8")), dict
        )

    def test_local_brand_icons_follow_home_assistant_specification(self) -> None:
        expected = {
            "icon.png": (256, 256),
            "icon@2x.png": (512, 512),
            "dark_icon.png": (256, 256),
            "dark_icon@2x.png": (512, 512),
        }
        for filename, dimensions in expected.items():
            data = (COMPONENT / "brand" / filename).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), dimensions)
        for filename in (
            "logo.png",
            "logo@2x.png",
            "dark_logo.png",
            "dark_logo@2x.png",
        ):
            self.assertFalse((COMPONENT / "brand" / filename).exists())

    def test_webhook_support_is_not_shipped(self) -> None:
        self.assertFalse((COMPONENT / "webhook.py").exists())
        setup_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        flow_source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
        strings_source = (COMPONENT / "strings.json").read_text(encoding="utf-8")
        self.assertNotIn("homeassistant.components import webhook", setup_source)
        self.assertNotIn("async_register", setup_source)
        self.assertNotIn("webhook", flow_source.casefold())
        self.assertNotIn("webhook", strings_source.casefold())

    def test_image_entity_uses_home_assistant_image_proxy(self) -> None:
        image_source = (COMPONENT / "image.py").read_text(encoding="utf-8")
        icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))
        verified_icon = "mdi:image-outline"
        self.assertIn(f'_attr_icon = "{verified_icon}"', image_source)
        self.assertEqual(
            icons["entity"]["image"]["last_event"]["default"], verified_icon
        )
        self.assertNotIn("mdi:image-clock", image_source)
        self.assertNotIn("def entity_picture", image_source)
        self.assertNotIn("data:image/svg+xml", image_source)
        self.assertIn("return self._image", image_source)
        self.assertNotIn("if event.snapshot is None:\n            return", image_source)

    def test_runtime_reliability_hooks_are_shipped(self) -> None:
        setup_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        api_source = (COMPONENT / "api.py").read_text(encoding="utf-8")
        self.assertIn("entry.async_start_reauth(hass)", setup_source)
        self.assertIn("async_remove_entry", setup_source)
        self.assertIn("async_remove_config_entry_device", setup_source)
        self.assertIn("last_event_index", setup_source)
        self.assertIn("timeout=_REQUEST_TIMEOUT", api_source)
        self.assertIn("_MAX_SNAPSHOT_BYTES", api_source)

    def test_default_entity_set_keeps_diagnostics_out_of_new_installs(self) -> None:
        sensor_source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
        event_source = (COMPONENT / "event.py").read_text(encoding="utf-8")
        strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
        self.assertIn("SurveillanceEventsTodaySensor", sensor_source)
        self.assertGreaterEqual(
            sensor_source.count("_attr_entity_registry_enabled_default = False"), 2
        )
        self.assertIn('"events_today"', json.dumps(strings))
        self.assertIn("_unrecorded_attributes", event_source)
        unrecorded = event_source.split("_unrecorded_attributes", 1)[1].split(")", 1)[0]
        self.assertNotIn('"event_type",\n', unrecorded)


if __name__ == "__main__":
    unittest.main()
