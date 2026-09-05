# Ecommerce Tracking Audit

This tool checks ecommerce tracking with a real browser.

Paste a store homepage URL. The tool tries to open a product, add it to the cart and reach the checkout page. It stops before placing an order.

The tool checks browser requests for GA4 and Meta events. It also saves dataLayer pushes and screenshots.

## What you get

- `report.html` is the main report. Open it in a browser.
- `summary.csv` shows which tracking checks passed or failed.
- `tracking_requests.csv` has the tracking requests found during the test.
- `data_layer.csv` has the dataLayer pushes found on each page.
- `journey.json` has all test data in JSON format.
- PNG files show the pages reached by the tool.

The report is made from a fixed HTML template. It does not use AI.

## Install and run

You need Python 3.11 or newer.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
ecommerce-tracking-auditor
```

The tool will ask for the store homepage URL.

You can also give the URL in the command:

```powershell
ecommerce-tracking-auditor https://example-store.com
```

Show the browser while the test runs:

```powershell
ecommerce-tracking-auditor https://example-store.com --headed
```

Give a product URL when the tool cannot find one:

```powershell
ecommerce-tracking-auditor https://example-store.com --product https://example-store.com/product/example/
```

## Result types

- `passed` means the event was found at the right step.
- `request_missing` means the provider was found, but its event request was not seen.
- `data_layer_only` means GTM and the dataLayer event were found, but the provider request was not seen.
- `not_observed` means the tool did not find enough tracking evidence.
- `not_tested` means the tool could not reach that step.

## Make a Windows EXE

```powershell
.\build_exe.ps1
```

The EXE will be inside the `dist` folder. It can use Playwright Chromium, Google Chrome or Microsoft Edge. You can also run the `build-windows` workflow on GitHub and download the EXE from the workflow result.

## Test the code

```powershell
py -3 -m unittest discover -s tests -v
```

## Limits

- The tool checks browser tracking only.
- It does not set up GA4, GTM or Meta Pixel.
- It does not test server-side tracking.
- It does not place an order or test the purchase event.
- Some custom stores may need more selectors.
- Cookie consent can change what the tool sees.

Only test a store when you own it or have permission.
