from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .analysis import save_outputs


SENSITIVE_KEYS = {"email", "em", "phone", "ph", "first_name", "last_name", "address", "street", "postal_code", "card", "cvc", "cvv", "user_data"}
DATA_LAYER_HOOK = r"""
(() => {
  const copy = value => { try { return JSON.parse(JSON.stringify(value)); } catch (_) { return '[unserializable]'; } };
  window.__trackingAuditPushes = [];
  const layer = window.dataLayer = window.dataLayer || [];
  const original = layer.push.bind(layer);
  layer.push = function(...items) {
    for (const item of items) {
      const captured = {capturedAt: Date.now(), value: copy(item)};
      window.__trackingAuditPushes.push(captured);
      try { window.__trackingAuditRecord(captured); } catch (_) {}
    }
    return original(...items);
  };
})();
"""


def first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key)
    return values[0] if values else ""


def sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(normalized == part or normalized.endswith(f"[{part}]") or normalized.endswith(f".{part}") for part in SENSITIVE_KEYS)


def redact(params: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: (["[REDACTED]"] if sensitive_key(key) and any(values) else values) for key, values in params.items()}


def parse_request_params(url: str, post_data: str = "") -> dict[str, list[str]]:
    """Read query, URL encoded, and multipart form values from a request."""
    params = parse_qs(urlparse(url).query, keep_blank_values=True)
    if not post_data:
        return params
    if "Content-Disposition: form-data" in post_data:
        for key, value in re.findall(
            r'Content-Disposition:\s*form-data;\s*name="([^"]+)"[^\r\n]*\r?\n\r?\n(.*?)(?=\r?\n--)',
            post_data,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            params.setdefault(key, []).append(value)
    elif "=" in post_data:
        for key, values in parse_qs(post_data, keep_blank_values=True).items():
            params.setdefault(key, []).extend(values)
    return params


def classified_provider(url: str) -> str:
    parsed = urlparse(url)
    host, path = parsed.netloc.lower(), parsed.path.lower()
    if "googletagmanager.com" in host and path.endswith("/gtm.js"):
        return "gtm"
    if "googletagmanager.com" in host and path.endswith("/gtag/js"):
        return "google_tag"
    if "google-analytics.com" in host or path.endswith("/g/collect"):
        return "ga4"
    if "connect.facebook.net" in host:
        return "meta"
    if "facebook.com" in host and (path.rstrip("/").endswith("/tr") or path.rstrip("/").endswith("/events")):
        return "meta"
    if "googleadservices.com" in host or "googleads.g.doubleclick.net" in host or "/pagead/conversion" in path:
        return "google_ads"
    if "analytics.tiktok.com" in host or "business-api.tiktok.com" in host:
        return "tiktok"
    if "bat.bing.com" in host:
        return "microsoft_ads"
    if "linkedin.com" in host and ("/collect" in path or "/insight" in path):
        return "linkedin"
    if "pinterest.com" in host and ("/v3" in path or "/ct" in path):
        return "pinterest"
    if "snapchat.com" in host and ("pixel" in path or "/p" in path):
        return "snapchat"
    if "clarity.ms" in host:
        return "clarity"
    return ""


def event_name(provider: str, params: dict[str, list[str]], path: str) -> str:
    if provider == "ga4":
        return first(params, "en")
    if provider == "meta":
        if first(params, "ev"):
            return first(params, "ev")
        if path.endswith("fbevents.js") or "/signals/config/" in path:
            return "base_code"
        return first(params, "event") or first(params, "event_name")
    if provider == "google_ads" and "conversion" in path:
        return "conversion"
    return first(params, "event") or first(params, "event_name")


def destination_id(provider: str, params: dict[str, list[str]]) -> str:
    if provider == "ga4":
        return first(params, "tid")
    if provider == "meta":
        return first(params, "id")
    return first(params, "send_to") or first(params, "tid") or first(params, "id")


def normalize_homepage(value: str) -> str:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Paste only the website URL, for example: https://example.com")
    if not urlparse(value).scheme:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Paste only the website URL, for example: https://example.com")
    return urlunparse(parsed._replace(path=parsed.path or "/", query="", fragment="")).rstrip("/")


def same_site(url: str, homepage: str) -> bool:
    left = (urlparse(url).hostname or "").lower().removeprefix("www.")
    right = (urlparse(homepage).hostname or "").lower().removeprefix("www.")
    return left == right


def normal_chrome_user_agent(browser_version: str) -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{browser_version} Safari/537.36"
    )


class EcommerceAudit:
    def __init__(self, homepage: str, output_dir: Path, product_url: str = "", headed: bool = False, timeout_ms: int = 45000):
        self.homepage = normalize_homepage(homepage)
        self.output_dir = output_dir
        self.product_url = product_url
        self.headed = headed
        self.timeout_ms = timeout_ms
        self.current_stage = "startup"
        self.requests: list[dict[str, Any]] = []
        self.request_records: dict[Any, dict[str, Any]] = {}
        self.data_layer_pushes: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []

    def run(self) -> list[dict[str, Any]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = None
            launch_errors = []
            for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
                try:
                    browser = playwright.chromium.launch(headless=not self.headed, **options)
                    break
                except Exception as exc:
                    launch_errors.append(str(exc))
            if browser is None:
                raise RuntimeError(
                    "No supported browser could be launched. Install Chromium with "
                    "'python -m playwright install chromium', or install Google Chrome or Microsoft Edge. "
                    + " | ".join(launch_errors)
                )
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    locale="en-GB",
                    user_agent=normal_chrome_user_agent(browser.version),
                )
                context.expose_binding("__trackingAuditRecord", self._record_data_layer_push)
                context.add_init_script(DATA_LAYER_HOOK)
                page = context.new_page()
                page.on("request", self._record_request)
                page.on("response", self._record_response)
                page.on("requestfailed", self._record_request_failure)
                self._run_journey(page)
            finally:
                browser.close()
        return save_outputs(self.output_dir, self.homepage, self.steps, self.requests, self.actions)

    def _record_data_layer_push(self, source: dict[str, Any], captured: dict[str, Any]) -> None:
        page = source.get("page")
        self.data_layer_pushes.append({
            "stage": self.current_stage,
            "page_url": page.url if page else "",
            "capturedAt": captured.get("capturedAt", ""),
            "value": captured.get("value"),
        })

    def _record_request(self, request) -> None:
        provider = classified_provider(request.url)
        if not provider:
            return
        try:
            post_data = request.post_data or ""
        except Exception:
            post_data = ""
        raw_params = parse_request_params(request.url, post_data)
        params = redact(raw_params)
        parsed = urlparse(request.url)
        safe_query = redact(parse_qs(parsed.query, keep_blank_values=True))
        safe_pairs = [(key, value) for key, values in safe_query.items() for value in values]
        safe_url = urlunparse(parsed._replace(query=urlencode(safe_pairs, doseq=True)))
        destination = destination_id(provider, params)
        if provider == "meta" and not destination:
            config_match = re.search(r"/signals/config/(\d+)", parsed.path)
            destination = config_match.group(1) if config_match else ""
        record = {
            "timestamp_ms": int(time.time() * 1000),
            "stage": self.current_stage,
            "provider": provider,
            "event": event_name(provider, params, parsed.path),
            "destination_id": destination,
            "method": request.method,
            "host": parsed.netloc,
            "path": parsed.path,
            "url": safe_url,
            "params": params,
            "response_status": "",
            "failure": "",
        }
        self.requests.append(record)
        self.request_records[request] = record

    def _record_response(self, response) -> None:
        record = self.request_records.get(response.request)
        if record is not None:
            record["response_status"] = response.status

    def _record_request_failure(self, request) -> None:
        record = self.request_records.get(request)
        if record is not None:
            try:
                record["failure"] = request.failure or "Request failed"
            except Exception:
                record["failure"] = "Request failed"

    def _goto(self, page: Page, url: str) -> bool:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1800)
            return response is None or response.status < 400
        except Exception as exc:
            self.actions.append({"action": "navigation_failed", "url": url, "error": str(exc)})
            return False

    def _goto_stage(self, page: Page, url: str, stage: str) -> bool:
        if not self._goto(page, url):
            return False
        if self._looks_like_stage(page, stage):
            return True
        self.actions.append({"action": "stage_validation_failed", "stage": stage, "requested_url": url, "final_url": page.url})
        return False

    def _looks_like_stage(self, page: Page, stage: str) -> bool:
        path = urlparse(page.url).path.lower()
        patterns = {
            "cart": r"/(cart|basket)(?:/|$)",
            "checkout": r"/(checkout|checkouts)(?:/|$)",
        }
        if re.search(patterns[stage], path):
            return True
        selectors = {
            "cart": ".woocommerce-cart-form, form.cart, [data-cart], a[href*='/checkout']",
            "checkout": "form.checkout, form[name='checkout'], input[name*='billing'], input[name*='shipping'], [data-checkout]",
        }
        try:
            return page.locator(selectors[stage]).count() > 0
        except Exception:
            return False

    def _snapshot(self, page: Page, stage: str, reached: bool, note: str = "") -> None:
        filename = f"{len(self.steps)+1:02d}_{stage}.png" if reached else ""
        if reached:
            try:
                page.screenshot(path=str(self.output_dir / filename), full_page=True)
            except Exception as exc:
                note = (note + f" Screenshot failed: {exc}").strip()
        pushes = [
            {"capturedAt": item["capturedAt"], "value": item["value"]}
            for item in self.data_layer_pushes
            if item["stage"] == stage
        ] if reached else []
        self.steps.append({"stage": stage, "reached": reached, "url": page.url if reached else "", "title": page.title() if reached else "", "screenshot": filename, "data_layer_pushes": pushes, "note": note})

    def _run_journey(self, page: Page) -> None:
        self.current_stage = "homepage"
        home_reached = self._goto(page, self.homepage)
        self._snapshot(page, "homepage", home_reached)
        if not home_reached:
            self._mark_unreached(page, "Homepage could not be loaded.")
            return
        self._accept_consent(page)
        product = self._discover_product(page)
        if not product:
            self._snapshot(page, "product", False, "No product page could be discovered. Supply --product if needed.")
            self._snapshot(page, "cart", False, "Product stage was not reached.")
            self._snapshot(page, "checkout", False, "Product stage was not reached.")
            return
        self.current_stage = "product"
        product_reached = self._goto(page, product)
        self._snapshot(page, "product", product_reached)
        if not product_reached:
            self._snapshot(page, "cart", False, "Product page could not be loaded.")
            self._snapshot(page, "checkout", False, "Product page could not be loaded.")
            return
        self.current_stage = "cart"
        added = self._add_to_cart(page)
        cart = self._discover_cart(page) if added else ""
        cart_reached = bool(cart and self._goto_stage(page, cart, "cart"))
        self._snapshot(page, "cart", cart_reached, "" if cart_reached else "Add to cart or cart discovery did not complete.")
        if not cart_reached:
            self._snapshot(page, "checkout", False, "Cart stage was not reached.")
            return
        checkout = self._discover_checkout(page)
        self.current_stage = "checkout"
        checkout_reached = bool(checkout and self._goto_stage(page, checkout, "checkout"))
        self._snapshot(page, "checkout", checkout_reached, "Audit stopped before order submission." if checkout_reached else "Checkout page could not be discovered or loaded.")

    def _mark_unreached(self, page: Page, note: str) -> None:
        for stage in ("product", "cart", "checkout"):
            self._snapshot(page, stage, False, note)

    def _accept_consent(self, page: Page) -> None:
        for label in ("Accept all", "Accept All", "Allow all", "I agree", "Accept cookies"):
            try:
                button = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
                if button.count() and button.is_visible(timeout=300):
                    button.click(timeout=1500)
                    page.wait_for_timeout(500)
                    self.actions.append({"action": "consent_clicked", "label": label})
                    return
            except Exception:
                continue

    def _discover_product(self, page: Page) -> str:
        if self.product_url:
            candidate = urljoin(self.homepage + "/", self.product_url)
            return candidate if same_site(candidate, self.homepage) else ""
        selectors = [
            "a.woocommerce-LoopProduct-link",
            "a[href*='/product/']",
            "a[href*='/products/']",
            "[itemtype*='Product'] a[href]",
            "a[href*='/shop/']",
        ]
        for selector in selectors:
            try:
                links = page.locator(selector)
                for index in range(min(links.count(), 30)):
                    href = links.nth(index).get_attribute("href") or ""
                    candidate = urljoin(page.url, href)
                    if candidate and same_site(candidate, self.homepage) and candidate.rstrip("/") != self.homepage:
                        self.actions.append({"action": "product_discovered", "url": candidate, "source": "homepage"})
                        return candidate
            except Exception:
                continue
        for sitemap_path in ("/wp-sitemap-posts-product-1.xml", "/product-sitemap.xml"):
            try:
                response = page.context.request.get(self.homepage + sitemap_path, timeout=8000)
                if not response.ok:
                    continue
                root = ET.fromstring(response.text())
                for node in root.iter():
                    if node.tag.endswith("loc") and node.text:
                        candidate = node.text.strip()
                        if same_site(candidate, self.homepage):
                            self.actions.append({"action": "product_discovered", "url": candidate, "source": "sitemap"})
                            return candidate
            except Exception:
                continue
        return ""

    def _choose_options(self, page: Page) -> None:
        for index in range(page.locator("form select").count()):
            select = page.locator("form select").nth(index)
            try:
                options = select.locator("option")
                for option_index in range(options.count()):
                    option = options.nth(option_index)
                    value = option.get_attribute("value") or ""
                    if value and not option.is_disabled():
                        select.select_option(value=value)
                        break
            except Exception:
                continue
        page.wait_for_timeout(500)

    def _add_to_cart(self, page: Page) -> bool:
        self._choose_options(page)
        selectors = [
            "form.cart button.single_add_to_cart_button",
            "form.cart button[type='submit']",
            "button.single_add_to_cart_button",
            "button:has-text('Add to cart')",
            "button:has-text('Add to basket')",
            "button:has-text('Buy now')",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible(timeout=400):
                    locator.click(timeout=3000)
                    page.wait_for_timeout(2200)
                    self.actions.append({"action": "add_to_cart_clicked", "selector": selector})
                    return True
            except Exception:
                continue
        return False

    def _link_matching(self, page: Page, pattern: str) -> str:
        try:
            links = page.locator(f"a[href*='{pattern}']")
            for index in range(min(links.count(), 20)):
                href = links.nth(index).get_attribute("href") or ""
                candidate = urljoin(page.url, href)
                if same_site(candidate, self.homepage):
                    return candidate
        except Exception:
            pass
        return ""

    def _discover_cart(self, page: Page) -> str:
        if re.search(r"/(cart|basket)(?:/|\?|$)", page.url, re.I):
            return page.url
        return self._link_matching(page, "/cart") or self._link_matching(page, "/basket") or self.homepage + "/cart/"

    def _discover_checkout(self, page: Page) -> str:
        return self._link_matching(page, "/checkout") or self._link_matching(page, "/checkouts/") or self.homepage + "/checkout/"
