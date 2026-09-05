from __future__ import annotations

import argparse
import re
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .auditor import EcommerceAudit, normalize_homepage


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Audit browser-side ecommerce tracking through checkout initiation without placing an order.")
    result.add_argument("homepage", nargs="?", help="Ecommerce homepage URL")
    result.add_argument("--product", default="", help="Optional product URL when automatic discovery is unreliable")
    result.add_argument("--output", type=Path, help="Output directory")
    result.add_argument("--headed", action="store_true", help="Show the browser while the audit runs")
    result.add_argument("--timeout", type=int, default=45, help="Navigation timeout in seconds")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    homepage = args.homepage
    interactive = not homepage
    if not homepage:
        print("Ecommerce Tracking Auditor")
        homepage = input("Paste the ecommerce homepage URL: ").strip()
    try:
        homepage = normalize_homepage(homepage)
    except ValueError as exc:
        print(f"Error: {exc}")
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
        return 1
    print(f"\nAudit complete: {output.resolve()}")
    for name in ("report.html", "summary.csv", "tracking_requests.csv", "data_layer.csv", "journey.json"):
        print(f"  {name}")
    failed = sum(row["status"] == "failed" for row in checks)
    not_tested = sum(row["status"] == "not_tested" for row in checks)
    print(f"  failed checks: {failed}")
    print(f"  not-tested checks: {not_tested}")
    if interactive:
        webbrowser.open((output.resolve() / "report.html").as_uri())
        input("Press Enter to close...")
    return 0
