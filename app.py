import json, re, socket, ipaddress
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="PriceMatch AI", version="1.0.0")
HEADERS = {
    "User-Agent": "PriceMatchAI/1.0 (+product comparison; contact site owner for access)",
    "Accept": "text/html,application/xhtml+xml"
}
TIMEOUT = 12
MAX_BYTES = 3_000_000

class CompareRequest(BaseModel):
    url_a: HttpUrl
    url_b: HttpUrl
    threshold: int = 10

def validate_public_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only public http/https URLs are supported.")
    host = parsed.hostname.lower()
    if host in ("localhost",) or host.endswith(".local"):
        raise ValueError("Local URLs are not allowed.")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        for entry in addresses:
            ip = ipaddress.ip_address(entry[4][0])
            if not ip.is_global:
                raise ValueError("This URL does not point to a public host.")
    except socket.gaierror:
        raise ValueError("Could not resolve the website hostname.")

def text_of(value):
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, list):
        return ", ".join(filter(None, (text_of(x) for x in value)))
    if isinstance(value, dict):
        return text_of(value.get("name") or value.get("value") or value.get("@value") or "")
    return ""

def add_spec(specs, key, value):
    key = re.sub(r"\s+", " ", str(key or "").strip()).lower()
    value = text_of(value)
    if key and value and len(key) < 100 and len(value) < 300:
        aliases = {
            "model no": "model number", "model #": "model number", "mpn": "model number",
            "manufacturer part number": "model number", "brand": "brand",
            "product width": "width", "product height": "height", "product depth": "depth",
            "colour": "color", "fuel": "fuel type", "oven capacity": "capacity",
            "number of burners": "burners", "burner count": "burners",
        }
        key = aliases.get(key, key)
        specs[key] = value

def product_from_jsonld(data):
    if isinstance(data, list):
        for item in data:
            found = product_from_jsonld(item)
            if found.get("name") or found.get("model") or found.get("specs"):
                return found
        return {}
    if not isinstance(data, dict):
        return {}
    if "@graph" in data:
        found = product_from_jsonld(data["@graph"])
        if found:
            return found
    typ = data.get("@type", "")
    is_product = typ == "Product" or (isinstance(typ, list) and "Product" in typ)
    if not is_product:
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = product_from_jsonld(value)
                if found.get("name") or found.get("model") or found.get("specs"):
                    return found
        return {}
    out = {
        "name": text_of(data.get("name")),
        "model": text_of(data.get("model") or data.get("mpn") or data.get("sku")),
        "price": "",
        "specs": {}
    }
    offers = data.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        price = text_of(offers.get("price") or offers.get("lowPrice"))
        currency = text_of(offers.get("priceCurrency"))
        out["price"] = (currency + " " + price).strip()
    brand = text_of(data.get("brand"))
    if brand:
        add_spec(out["specs"], "brand", brand)
    for key in ("description", "category", "color", "material", "size", "weight", "width", "height", "depth"):
        if data.get(key):
            add_spec(out["specs"], key, data[key])
    additional = data.get("additionalProperty", [])
    if isinstance(additional, dict):
        additional = [additional]
    if isinstance(additional, list):
        for prop in additional:
            if isinstance(prop, dict):
                add_spec(out["specs"], prop.get("name"), prop.get("value"))
    return out

def scrape_product(url: str):
    validate_public_url(str(url))
    try:
        with requests.get(str(url), headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            validate_public_url(response.url)
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Product page is larger than the allowed limit.")
                chunks.append(chunk)
            html = b"".join(chunks)
    except ValueError:
        raise
    except requests.RequestException as exc:
        raise ValueError("Could not access this product page. It may block automated requests or require JavaScript.") from exc

    soup = BeautifulSoup(html, "html.parser")
    result = {"name": "", "model": "", "price": "", "specs": {}, "url": str(url)}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            candidate = product_from_jsonld(json.loads(script.string or script.get_text()))
            if candidate and (candidate.get("name") or candidate.get("model") or candidate.get("specs")):
                result.update({k: v for k, v in candidate.items() if v})
                break
        except (ValueError, TypeError):
            continue

    def meta(*selectors):
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                value = node.get("content") or node.get_text(" ", strip=True)
                if value:
                    return value.strip()
        return ""

    result["name"] = result["name"] or meta('meta[property="og:title"]', 'meta[name="twitter:title"]', "h1")
    result["model"] = result["model"] or meta('meta[itemprop="model"]', 'meta[name="model"]', 'meta[itemprop="mpn"]')
    result["price"] = result["price"] or meta('meta[property="product:price:amount"]', 'meta[itemprop="price"]', 'meta[name="price"]')
    if not result["specs"]:
        # Read common specification tables and definition lists.
        for row in soup.select("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) >= 2:
                add_spec(result["specs"], cells[0].get_text(" ", strip=True), cells[1].get_text(" ", strip=True))
        for dl in soup.select("dl"):
            terms = dl.find_all("dt")
            for term in terms:
                desc = term.find_next_sibling("dd")
                if desc:
                    add_spec(result["specs"], term.get_text(" ", strip=True), desc.get_text(" ", strip=True))
    if not result["name"]:
        result["name"] = soup.title.get_text(" ", strip=True) if soup.title else urlparse(str(url)).netloc
    if not result["model"]:
        # Search visible text for common model-number labels.
        text = soup.get_text(" ", strip=True)
        match = re.search(r"(?:model(?: number| no\.?| #)?|mpn)\s*[:#]?\s*([A-Z0-9][A-Z0-9._-]{3,})", text, re.I)
        if match:
            result["model"] = match.group(1)
    if not result["specs"]:
        result["warning"] = "No structured specifications were detected. This retailer may render details with JavaScript or use a non-standard layout."
    return result

def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())

def norm_value(s):
    s = str(s or "").lower().strip()
    s = re.sub(r"\b(inches|inch|in\.)\b", "in", s)
    s = re.sub(r"\b(feet|foot|ft\.)\b", "ft", s)
    s = re.sub(r"\b(cubic feet|cu\.\s*ft\.?|cu ft|cuft)\b", "cuft", s)
    s = re.sub(r"\b(stainless[- ]steel)\b", "stainless", s)
    s = re.sub(r"\b(yes|true)\b", "yes", s)
    s = re.sub(r"\b(no|false)\b", "no", s)
    return re.sub(r"[^a-z0-9.]", "", s)

def compare_products(a, b, threshold):
    model_a, model_b = a.get("model", ""), b.get("model", "")
    exact = bool(model_a and model_b and norm(model_a) == norm(model_b))
    sa, sb = a.get("specs", {}), b.get("specs", {})
    keys = sorted(set(sa) | set(sb))
    checks = []
    for key in keys:
        va, vb = sa.get(key, ""), sb.get(key, "")
        checks.append({"key": key, "a": va or "Not found", "b": vb or "Not found",
                       "match": bool(va and vb and norm_value(va) == norm_value(vb))})
    matched = sum(1 for x in checks if x["match"])
    eligible = exact or matched >= threshold
    if exact:
        reason = "Model numbers match after normalization."
    elif eligible:
        reason = f"{matched} matching specifications meet the threshold of {threshold}."
    else:
        reason = f"Only {matched} matching specifications were detected; {threshold} are required."
    return {"eligible": eligible, "exact_model_match": exact, "matched_specifications": matched,
            "threshold": threshold, "reason": reason, "checks": checks, "product_a": a, "product_b": b}

@app.get("/", response_class=HTMLResponse)
def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/compare")
def compare(req: CompareRequest):
    threshold = max(1, min(100, req.threshold))
    try:
        a = scrape_product(str(req.url_a))
        b = scrape_product(str(req.url_b))
        return compare_products(a, b, threshold)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
