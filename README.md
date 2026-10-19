# PriceMatch AI — live deployment starter

This package turns the PriceMatch AI prototype into a small web app with a backend.

## What it does
- Accepts two public product-page URLs.
- Attempts to extract product name, model, price, and specifications from structured product data (JSON-LD), metadata, specification tables, and definition lists.
- Marks eligible if both model numbers match after normalization, or if the number of matching detected specifications reaches the configured threshold (10 by default).
- Shows the extracted details and per-spec comparison.

## Important limitations
- This is a starter deployment, not a guarantee that every retailer can be scraped.
- Some stores block automated requests or render product data with JavaScript. Those pages may need a retailer-approved API or a store-specific integration.
- A missing spec is not a mismatch in the source data; it is reported as “Not found” and does not count as a match.
- Eligibility is based only on the configured rule. Real retailer policies may also check seller, stock, shipping, product condition, promotions, location, and exclusions.
- No API key is required by this starter because extraction uses structured page data and HTML parsing; it does not use a hosted AI model.
- Use only URLs and retailer data you are permitted to access. Respect retailer terms and rate limits.

## Deploy free with Render
1. Extract this ZIP.
2. Create a GitHub repository and upload all files in this folder.
3. Sign in to Render and choose **New → Web Service**.
4. Connect your GitHub repository. Render should detect `render.yaml`; otherwise select Python and use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. Deploy. Render will provide a public `https://...onrender.com` URL.
6. Open the public URL and test with two retailer product pages. The first request on a free instance may take a little time after idle.

## Local test (optional)
Python 3.10+ recommended:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```
Then open http://127.0.0.1:8000.

## API
`POST /api/compare`
```json
{
  "url_a": "https://example.com/product-a",
  "url_b": "https://example.com/product-b",
  "threshold": 10
}
```
