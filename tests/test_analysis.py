import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecommerce_tracking_auditor.analysis import evaluate, render_report
from ecommerce_tracking_auditor.auditor import classified_provider, normal_chrome_user_agent, parse_request_params, redact


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
        self.assertEqual(keyed[("checkout", "ga4")], "request_missing")
        self.assertEqual(keyed[("cart", "meta")], "request_missing")

    def test_event_on_wrong_stage_does_not_pass(self):
        steps = [{"stage": stage, "reached": True} for stage in ("homepage", "product", "cart", "checkout")]
        requests = [{"stage": "homepage", "provider": "ga4", "event": "view_item"}]
        checks = evaluate(steps, requests)
        keyed = {(row["stage"], row["provider"]): row["status"] for row in checks}
        self.assertEqual(keyed[("product", "ga4")], "request_missing")

    def test_absent_provider_is_not_observed(self):
        steps = [{"stage": stage, "reached": True} for stage in ("homepage", "product", "cart", "checkout")]
        checks = evaluate(steps, [])
        self.assertTrue(all(row["status"] == "not_observed" for row in checks))

    def test_gtm_and_data_layer_do_not_claim_provider_is_missing(self):
        steps = [
            {"stage": "homepage", "reached": True, "data_layer_pushes": []},
            {"stage": "product", "reached": True, "data_layer_pushes": [{"value": {"event": "view_item"}}]},
            {"stage": "cart", "reached": True, "data_layer_pushes": [{"value": {"event": "add_to_cart"}}]},
            {"stage": "checkout", "reached": True, "data_layer_pushes": [{"value": {"event": "begin_checkout"}}]},
        ]
        checks = evaluate(steps, [{"stage": "homepage", "provider": "gtm", "event": ""}])
        self.assertTrue(all(row["status"] == "data_layer_only" for row in checks))

    def test_provider_classification_and_redaction(self):
        self.assertEqual(classified_provider("https://www.google-analytics.com/g/collect?en=view_item"), "ga4")
        self.assertEqual(classified_provider("https://www.facebook.com/tr/?ev=ViewContent"), "meta")
        self.assertEqual(classified_provider("https://connect.facebook.net/en_US/fbevents.js"), "meta")
        self.assertEqual(redact({"em": ["a@example.com"], "en": ["view_item"]})["em"], ["[REDACTED]"])

    def test_audit_user_agent_does_not_identify_as_headless(self):
        user_agent = normal_chrome_user_agent("143.0.0.0")
        self.assertIn("Chrome/143.0.0.0", user_agent)
        self.assertNotIn("HeadlessChrome", user_agent)

    def test_meta_multipart_event_fields_are_parsed(self):
        body = (
            "------WebKitFormBoundary123\r\n"
            'Content-Disposition: form-data; name="id"\r\n\r\n'
            "1023841666963509\r\n"
            "------WebKitFormBoundary123\r\n"
            'Content-Disposition: form-data; name="ev"\r\n\r\n'
            "ViewContent\r\n"
            "------WebKitFormBoundary123\r\n"
            'Content-Disposition: form-data; name="cd[value]"\r\n\r\n'
            "19.99\r\n"
            "------WebKitFormBoundary123--\r\n"
        )
        params = parse_request_params("https://www.facebook.com/tr/", body)
        self.assertEqual(params["id"], ["1023841666963509"])
        self.assertEqual(params["ev"], ["ViewContent"])
        self.assertEqual(params["cd[value]"], ["19.99"])

    def test_static_report_explains_scope(self):
        steps = [{"stage": "product", "reached": False, "url": "", "note": "No product", "screenshot": ""}]
        checks = evaluate(steps, [])
        report = render_report("https://shop.test", steps, [], checks)
        self.assertIn("Not tested", report)
        self.assertIn("does not submit an order", report)


if __name__ == "__main__":
    unittest.main()
