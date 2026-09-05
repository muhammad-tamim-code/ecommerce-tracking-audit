import csv
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecommerce_tracking_auditor.auditor import EcommerceAudit


def tracking_script(event):
    return f"<script>dataLayer.push({{event:'{event}'}}); fetch('/g/collect?en={event}&tid=G-LOCAL-TEST');</script>"


class StoreHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        pages = {
            "/": "<h1>Store</h1><a href='/product/test/'>Test product</a>",
            "/product/test/": tracking_script("view_item") + "<h1>Test product</h1><button class='single_add_to_cart_button' onclick=\"fetch('/g/collect?en=add_to_cart&tid=G-LOCAL-TEST');setTimeout(()=>location.href='/cart/',150)\">Add to cart</button>",
            "/cart/": "<h1>Cart</h1><div data-cart>Test product</div><a href='/checkout/'>Proceed to checkout</a>",
            "/checkout/": tracking_script("begin_checkout") + "<h1>Checkout</h1><form data-checkout><input name='billing_name'></form>",
        }
        if self.path.startswith("/g/collect"):
            self.send_response(204)
            self.end_headers()
            return
        body = ("<!doctype html><html><head><title>Local store</title></head><body>" + pages.get(self.path, "not found") + "</body></html>").encode()
        status = 200 if self.path in pages else 404
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class AuditorIntegrationTests(unittest.TestCase):
    def test_completes_journey_and_writes_static_evidence(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StoreHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "audit"
                homepage = f"http://127.0.0.1:{server.server_port}"
                try:
                    checks = EcommerceAudit(homepage, output, timeout_ms=15000).run()
                except Exception as exc:
                    if "Executable doesn't exist" in str(exc):
                        self.skipTest("Playwright Chromium is not installed")
                    raise
                ga4 = {row["stage"]: row["status"] for row in checks if row["provider"] == "ga4"}
                self.assertEqual(ga4, {"product": "passed", "cart": "passed", "checkout": "passed"})
                for name in ("report.html", "summary.csv", "tracking_requests.csv", "data_layer.csv", "journey.json"):
                    self.assertTrue((output / name).exists(), name)
                with (output / "tracking_requests.csv").open(encoding="utf-8-sig") as handle:
                    requests = list(csv.DictReader(handle))
                self.assertEqual({row["event"] for row in requests}, {"view_item", "add_to_cart", "begin_checkout"})
                with (output / "data_layer.csv").open(encoding="utf-8-sig") as handle:
                    data_layer = list(csv.DictReader(handle))
                self.assertEqual({row["stage"] for row in data_layer}, {"product", "checkout"})
                self.assertTrue(any("view_item" in row["value_json"] for row in data_layer))
                report = (output / "report.html").read_text(encoding="utf-8")
                self.assertIn("The completed journey had 3 check(s) without enough tracking evidence", report)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
