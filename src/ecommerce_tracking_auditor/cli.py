from __future__ import annotations

import argparse
import re
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .auditor import EcommerceAudit, normalize_homepage


def pause_before_close(interactive: bool) -> None:
    if interactive:
        try:
            input("Press Enter to close...")
        except EOFError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Audit browser-side ecommerce tracking through checkout initiation without placing an order.")
    result.add_argument("homepage", nargs="?", help="Ecommerce homepage URL")
    result.add_argument("--product", default="", help="Optional product URL when automatic discovery is unreliable")
    result.add_argument("--output", type=Path, help="Output directory")
    result.add_argument("--headed", action="store_true", help="Show the browser while the audit runs")
    result.add_argument("--timeout", type=int, default=45, help="Navigation timeout in seconds")
    result.add_argument("--no-open", action="store_true", help="Do not open the HTML report after the audit")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    homepage = args.homepage
    interactive = not homepage
    if not homepage:
        print("Ecommerce Tracking Auditor")
        try:
            homepage = input("Paste the ecommerce homepage URL: ").strip()
        except EOFError as exc:
            print(f"Error: {exc}")
            pause_before_close(interactive)
            return 1
    try:
        homepage = normalize_homepage(homepage)
    except ValueError as exc:
        print(f"Error: {exc}")
        pause_before_close(interactive)
        return 1
    host = re.sub(r"[^A-Za-z0-9.-]+", "_", urlparse(homepage).hostname or "store")
    output = args.output or Path("output") / f"{host}_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        checks = EcommerceAudit(homepage, output, args.product, args.headed, max(args.timeout, 5) * 1000).run()
    except Exception as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            print("Chromium is not installed. Run: python -m playwright install chromium")
        else:
            print(f"Audit failed: {message}")
        pause_before_close(interactive)
        return 1
    report_path = output.resolve() / "report.html"
    print(f"\nAudit complete. Result folder: {output.resolve()}")
    for name in ("report.html", "summary.csv", "tracking_requests.csv", "data_layer.csv", "journey.json"):
        print(f"  {name}")
    missing_requests = sum(row["status"] in {"request_missing", "data_layer_only"} for row in checks)
    not_tested = sum(row["status"] == "not_tested" for row in checks)
    print(f"  provider requests not verified: {missing_requests}")
    print(f"  not-tested checks: {not_tested}")
    print(f"  Opening report: {report_path}")
    if not args.no_open:
        webbrowser.open(report_path.as_uri())
    pause_before_close(interactive)
    return 0
