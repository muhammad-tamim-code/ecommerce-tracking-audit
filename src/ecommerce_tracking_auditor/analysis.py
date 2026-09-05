from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_EVENTS = {
    "product": {"ga4": "view_item", "meta": "ViewContent"},
    "cart": {"ga4": "add_to_cart", "meta": "AddToCart"},
    "checkout": {"ga4": "begin_checkout", "meta": "InitiateCheckout"},
}

PROVIDER_NAMES = {
    "ga4": "Google Analytics 4",
    "meta": "Meta Pixel",
    "google_ads": "Google Ads",
    "tiktok": "TikTok",
    "microsoft_ads": "Microsoft Ads",
    "linkedin": "LinkedIn Insight Tag",
    "pinterest": "Pinterest Tag",
    "snapchat": "Snap Pixel",
    "clarity": "Microsoft Clarity",
    "gtm": "Google Tag Manager",
    "google_tag": "Google tag",
}


def evaluate(steps: list[dict[str, Any]], requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reached = {step["stage"]: bool(step.get("reached")) for step in steps}
    installed = {request.get("provider") for request in requests if request.get("provider")}
    rows: list[dict[str, Any]] = []
    for stage, providers in EXPECTED_EVENTS.items():
        for provider, expected in providers.items():
            observed = [
                request for request in requests
                if request.get("stage") == stage
                and request.get("provider") == provider
                and request.get("event") == expected
            ]
            if not reached.get(stage):
                status = "not_tested"
                note = f"The {stage} stage was not reached."
            elif provider not in installed:
                status = "not_configured"
                note = f"No {PROVIDER_NAMES[provider]} requests were observed during the journey."
            elif observed:
                status = "passed"
                note = f"Observed {len(observed)} matching request(s) during the {stage} stage."
            else:
                status = "failed"
                note = f"The stage was reached, but {expected} was not observed."
            rows.append({
                "stage": stage,
                "provider": provider,
                "provider_name": PROVIDER_NAMES[provider],
                "expected_event": expected,
                "status": status,
                "observed_count": len(observed),
                "note": note,
            })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def status_label(value: str) -> str:
    return {
        "passed": "Passed",
        "failed": "Failed",
        "not_tested": "Not tested",
        "not_configured": "Not configured",
    }.get(value, value.replace("_", " ").title())


def render_report(homepage: str, steps: list[dict[str, Any]], requests: list[dict[str, Any]], checks: list[dict[str, Any]]) -> str:
    failures = sum(row["status"] == "failed" for row in checks)
    incomplete = any(row["status"] == "not_tested" for row in checks)
    if incomplete:
        conclusion = "The journey was incomplete. Unreached stages are marked Not tested."
    elif failures:
        conclusion = f"The completed journey produced {failures} missing expected-event finding(s)."
    else:
        conclusion = "The completed journey produced no missing expected-event findings in the supported checks."

    providers = Counter(request.get("provider") for request in requests if request.get("provider"))
    step_rows = "".join(
        "<tr>"
        f"<td>{html.escape(step['stage'].title())}</td>"
        f"<td>{'Reached' if step.get('reached') else 'Not reached'}</td>"
        f"<td>{html.escape(step.get('url', ''))}</td>"
        f"<td>{html.escape(step.get('note', ''))}</td>"
        "</tr>"
        for step in steps
    )
    check_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['stage'].title())}</td>"
        f"<td>{html.escape(row['provider_name'])}</td>"
        f"<td><code>{html.escape(row['expected_event'])}</code></td>"
        f"<td><span class='status {row['status']}'>{status_label(row['status'])}</span></td>"
        f"<td>{html.escape(row['note'])}</td>"
        "</tr>"
        for row in checks
    )
    provider_rows = "".join(
        f"<tr><td>{html.escape(PROVIDER_NAMES.get(provider, provider))}</td><td>{count}</td></tr>"
        for provider, count in sorted(providers.items())
    ) or "<tr><td colspan='2'>No recognized tracking requests observed</td></tr>"
    screenshot_items = "".join(
        f"<li><a href='{html.escape(step['screenshot'])}'>{html.escape(step['stage'].title())} screenshot</a></li>"
        for step in steps if step.get("screenshot")
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ecommerce tracking audit</title>
<style>
body{{font-family:Arial,sans-serif;color:#171717;background:#f6f7f9;margin:0;line-height:1.5}}
main{{max-width:1100px;margin:36px auto;background:#fff;padding:36px;box-shadow:0 8px 28px #0001}}
h1{{font-size:28px;margin:0 0 6px}} h2{{margin-top:30px;font-size:20px}} .lead{{font-size:17px}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{border:1px solid #d9d9d9;padding:9px;text-align:left;vertical-align:top}} th{{background:#24364b;color:#fff}}
code{{font-size:13px}} .status{{font-weight:700;white-space:nowrap}} .passed{{color:#147a3d}} .failed{{color:#b42318}} .not_tested,.not_configured{{color:#765500}}
.meta{{color:#555;overflow-wrap:anywhere}} a{{color:#164f8f}} footer{{margin-top:32px;color:#555;font-size:13px}}
</style></head><body><main>
<h1>Ecommerce tracking audit</h1><p class="meta">Homepage: {html.escape(homepage)}</p>
<p class="lead">{html.escape(conclusion)}</p>
<h2>Journey coverage</h2><table><thead><tr><th>Stage</th><th>Result</th><th>URL</th><th>Note</th></tr></thead><tbody>{step_rows}</tbody></table>
<h2>Expected event checks</h2><table><thead><tr><th>Stage</th><th>Provider</th><th>Expected event</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{check_rows}</tbody></table>
<h2>Observed providers</h2><table><thead><tr><th>Provider</th><th>Recognized requests</th></tr></thead><tbody>{provider_rows}</tbody></table>
<h2>Evidence files</h2><ul>{screenshot_items}<li><a href="summary.csv">Expected-event checks CSV</a></li><li><a href="tracking_requests.csv">Recognized tracking requests CSV</a></li><li><a href="data_layer.csv">Captured dataLayer pushes CSV</a></li><li><a href="journey.json">Complete structured evidence JSON</a></li></ul>
<footer>This automated report covers browser-side signals through checkout initiation. It does not submit an order, validate purchase events, or prove that server-side events reached an advertising platform.</footer>
</main></body></html>"""


def save_outputs(output_dir: Path, homepage: str, steps: list[dict[str, Any]], requests: list[dict[str, Any]], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = evaluate(steps, requests)
    write_csv(output_dir / "summary.csv", checks)
    request_fields = ["timestamp_ms", "stage", "provider", "event", "destination_id", "method", "host", "path", "url"]
    write_csv(output_dir / "tracking_requests.csv", requests, request_fields)
    data_layer_rows = [
        {
            "stage": step.get("stage", ""),
            "page_url": step.get("url", ""),
            "captured_at_ms": push.get("capturedAt", ""),
            "value_json": json.dumps(push.get("value"), ensure_ascii=False),
        }
        for step in steps
        for push in step.get("data_layer_pushes", [])
    ]
    write_csv(output_dir / "data_layer.csv", data_layer_rows, ["stage", "page_url", "captured_at_ms", "value_json"])
    evidence = {"homepage": homepage, "steps": steps, "tracking_requests": requests, "actions": actions, "checks": checks}
    (output_dir / "journey.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "report.html").write_text(render_report(homepage, steps, requests, checks), encoding="utf-8")
    return checks
