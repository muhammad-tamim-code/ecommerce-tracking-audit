import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecommerce_tracking_auditor.analysis import evaluate, render_report
from ecommerce_tracking_auditor.auditor import classified_provider, redact


class AnalysisTests(unittest.TestCase):
    def test_unreached_steps_are_not_tested(self):
        steps = [
            {"stage": "homepage", "reached": True},
            {"stage": "product", "reached": False},
            {"stage": "cart", "reached": False},
            {"stage": "checkout", "reached": False},
        ]
        checks = evaluate(steps, [{"provider": "ga4", "event": "page_view"}])
        self.assertTrue(all(row["status"] == "not_tested" for row in checks))

    def test_reached_event_passes_and_missing_event_fails(self):
        steps = [{"stage": stage, "reached": True} for stage in ("homepage", "product", "cart", "checkout")]
        requests = [
            {"stage": "product", "provider": "ga4", "event": "view_item"},
            {"stage": "cart", "provider": "ga4", "event": "add_to_cart"},
            {"stage": "product", "provider": "meta", "event": "ViewContent"},
        ]
        checks = evaluate(steps, requests)
        keyed = {(row["stage"], row["provider"]): row["status"] for row in checks}
        self.assertEqual(keyed[("product", "ga4")], "passed")
        self.assertEqual(keyed[("checkout", "ga4")], "failed")
        self.assertEqual(keyed[("cart", "meta")], "failed")

    def test_event_on_wrong_stage_does_not_pass(self):
        steps = [{"stage": stage, "reached": True} for stage in ("homepage", "product", "cart", "checkout")]
        requests = [{"stage": "homepage", "provider": "ga4", "event": "view_item"}]
        checks = evaluate(steps, requests)
        keyed = {(row["stage"], row["provider"]): row["status"] for row in checks}
        self.assertEqual(keyed[("product", "ga4")], "failed")

    def test_absent_provider_is_not_configured(self):
        steps = [{"stage": stage, "reached": True} for stage in ("homepage", "product", "cart", "checkout")]
        checks = evaluate(steps, [])
        self.assertTrue(all(row["status"] == "not_configured" for row in checks))

    def test_provider_classification_and_redaction(self):
        self.assertEqual(classified_provider("https://www.google-analytics.com/g/collect?en=view_item"), "ga4")
        self.assertEqual(classified_provider("https://www.facebook.com/tr/?ev=ViewContent"), "meta")
        self.assertEqual(redact({"em": ["a@example.com"], "en": ["view_item"]})["em"], ["[REDACTED]"])

    def test_static_report_explains_scope(self):
        steps = [{"stage": "product", "reached": False, "url": "", "note": "No product", "screenshot": ""}]
        checks = evaluate(steps, [])
        report = render_report("https://shop.test", steps, [], checks)
        self.assertIn("Not tested", report)
        self.assertIn("does not submit an order", report)


if __name__ == "__main__":
    unittest.main()
