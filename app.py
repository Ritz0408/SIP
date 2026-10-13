"""
Best SIP Finder - local web app.

Rules (same as the manual process):
  1. Category choose karo: Large Cap / Mid Cap / Small Cap
  2. Sirf Direct + Growth funds lo (AMFI official category list se)
  3. Har fund ke liye: 5Y CAGR, Exit Load, Expense Ratio, Min SIP note karo
     (5 saal se naye funds ka 3Y CAGR, woh bhi na ho to 1Y CAGR)
  4. 3 rankings: CAGR (zyada = acha), Exit Load (kam = acha), Exp Ratio (kam = acha)
     Tie ho to same rank (1, 1, 3)
  5. Teeno ranks ka sum = Total; Total sabse kam = best fund

Data sources:
  - AMFI NAVAll.txt   -> fund list + category (official)
  - api.mfapi.in      -> NAV history -> 5Y CAGR calculate
  - Groww (unofficial) -> exit load, expense ratio, min SIP
  - Kuvera mirror      -> fallback for expense ratio / min SIP

Run:  python app.py   then open http://localhost:8000
Static build (GitHub Pages):  python app.py --build site
Only Python standard library is used (no pip install needed).
"""

import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8000
CACHE_SECONDS = 6 * 60 * 60
HERE = Path(__file__).parent
IST = timezone(timedelta(hours=5, minutes=30))

CATEGORIES = {
    "large": {"amfi": "Equity Scheme - Large Cap Fund", "groww": "Large Cap", "label": "Large Cap"},
    "mid": {"amfi": "Equity Scheme - Mid Cap Fund", "groww": "Mid Cap", "label": "Mid Cap"},
    "small": {"amfi": "Equity Scheme - Small Cap Fund", "groww": "Small Cap", "label": "Small Cap"},
}

AMFI_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
MFAPI_URL = "https://api.mfapi.in/mf/{code}"
GROWW_LIST_URL = (
    "https://groww.in/v1/api/search/v1/derived/scheme?available_for_investment=true"
    "&doc_type=scheme&page={page}&plan_type=Direct&q=&size=100&sub_category={sub}"
)
KUVERA_URL = "https://mf.captnemo.in/kuvera/{isin}"

_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- helpers

def fetch(url, as_json=True, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BestSIPFinder)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
    return json.loads(body) if as_json else body


def cached(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- AMFI fund list

STALE_DAYS = 30  # NAV older than this = scheme merged/closed, skip it


def amfi_funds(category_header):
    """
    Direct + Growth schemes from the given AMFI category section.
    Returns (funds, unclear): `unclear` rows have no plan/option text at all (e.g. Motilal Oswal
    Midcap Fund ;;) and must be confirmed as Direct Growth from another source.
    """
    text = cached("amfi", lambda: fetch(AMFI_URL, as_json=False, timeout=60))
    funds, unclear, in_section = [], [], False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Open Ended Schemes") or line.startswith("Close Ended Schemes") \
                or line.startswith("Interval Fund Schemes"):
            in_section = category_header in line and line.startswith("Open Ended")
            continue
        if not in_section or ";" not in line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if not parts[0].isdigit():
            continue
        # New format: code;isin1;isin2;name;plan;option;nav;date (plan/option are sometimes blank
        # and the plan is written inside the name instead), old format: code;isin1;isin2;name;nav;date
        name = parts[3]
        try:
            nav_date = datetime.strptime(parts[-1], "%d-%b-%Y").date()
            if (date.today() - nav_date).days > STALE_DAYS:
                continue
        except ValueError:
            pass
        # A real Growth option has no reinvestment ISIN; a second ISIN means it is an IDCW
        # scheme (even if AMFI labels it "Growth", e.g. Sundaram Small Cap 119588)
        if parts[2] not in ("", "-"):
            continue
        desc = " ".join(parts[3:6] if len(parts) >= 8 else parts[3:4]).lower()
        if re.search(r"idcw|dividend|bonus|income distribution|payout|reinvest", desc):
            continue
        row = {"code": parts[0], "isin": parts[1], "name": name}
        if "direct" in desc and "growth" in desc:
            funds.append(row)
        elif "direct" not in desc and "regular" not in desc:
            unclear.append(row)
    return funds, unclear


def confirm_direct_growth(row, groww):
    """For unclear AMFI rows: Groww's list is Direct Growth only; else ask Kuvera by ISIN."""
    if row["code"] in groww:
        return True
    try:
        f = fetch(KUVERA_URL.format(isin=row["isin"]), timeout=20)[0]
        return f.get("direct") == "Y" and "growth" in (f.get("name") or "").lower()
    except Exception:
        return False


# ---------------------------------------------------------------- Groww / Kuvera details

def groww_details(sub_category):
    out, page = {}, 0
    while page < 10:
        url = GROWW_LIST_URL.format(page=page, sub=urllib.parse.quote(sub_category))
        data = fetch(url)
        items = data.get("content") or []
        for it in items:
            out[str(it.get("scheme_code"))] = {
                "exit_load_text": (it.get("exit_load") or "").strip(),
                "expense_ratio": _num(it.get("expense_ratio")),
                "min_sip": _num(it.get("min_sip_investment")),
                "min_lumpsum": _num(it.get("min_investment_amount")),
            }
        if len(items) < 100:
            break
        page += 1
    return out


def kuvera_details(isin):
    try:
        f = fetch(KUVERA_URL.format(isin=isin), timeout=20)[0]
        return {
            "expense_ratio": _num(f.get("expense_ratio")),
            "min_sip": _num(f.get("sip_min")),
            "min_lumpsum": _num(f.get("lump_min")),
            "exit_load_text": (f.get("exit_load") or "").strip(),
        }
    except Exception:
        return {}


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- exit load parsing

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12}


def parse_exit_load(text):
    """
    Returns (load_pct, days, free_pct) or None if unknown.
    Lower load, shorter period and larger free portion = better.
    """
    if not text:
        return None
    t = text.lower().replace("\n", " ")
    if t.strip(" .") in ("nil", "none", "0", "0%", "no exit load") or t.startswith("nil"):
        return (0.0, 0, 100.0)

    # free units, e.g. "units in excess of 10%", "investments beyond 10%", "more than 25%"
    free_pct = 0.0
    m = re.search(r"(?:excess of|beyond|more than|above)\s*(\d+(?:\.\d+)?)\s*%", t)
    if m:
        free_pct = float(m.group(1))
        t_rest = t[:m.start()] + t[m.end():]
    else:
        t_rest = t

    pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", t_rest)]
    load_pct = max(pcts) if pcts else None

    days = None
    for num, unit in re.findall(r"(\d+|one|two|three|six|twelve)\s*(day|month|year|yr)", t_rest):
        if unit == "yr":
            unit = "year"
        n = int(num) if num.isdigit() else _WORD_NUM[num]
        d = n * (1 if unit == "day" else 30 if unit == "month" else 365)
        days = max(days or 0, d)
    if days is not None and days in (360, 364, 366):
        days = 365

    if load_pct is None:
        return None
    if load_pct == 0:
        return (0.0, 0, 100.0)
    return (load_pct, days if days is not None else 9999, free_pct)


def exit_load_label(key):
    if key is None:
        return ""
    pct, days, free = key
    if pct == 0:
        return "Nil"
    s = f"{pct:g}%"
    if days and days != 9999:
        s += f" < {days}d"
    if free and free < 100:
        s += f" ({free:g}% free)"
    return s


# ---------------------------------------------------------------- 5Y CAGR

CAGR_PERIODS = (5, 3, 1)  # 5Y preferred; newer funds fall back to 3Y, then 1Y


def best_cagr(code):
    """5Y CAGR, or 3Y / 1Y for funds that are not old enough. Under 1 year -> no CAGR."""
    data = fetch(MFAPI_URL.format(code=code), timeout=30).get("data") or []
    if not data:
        return None, "NAV data nahi mila"
    pts = []
    for d in data:
        try:
            pts.append((datetime.strptime(d["date"], "%d-%m-%Y").date(), float(d["nav"])))
        except (KeyError, ValueError):
            pass
    pts.sort()
    end_d, end_nav = pts[-1]
    for period in CAGR_PERIODS:
        try:
            target = end_d.replace(year=end_d.year - period)
        except ValueError:  # 29 Feb
            target = end_d.replace(year=end_d.year - period, day=28)
        if pts[0][0] > target:
            continue
        start_d, start_nav = max((p for p in pts if p[0] <= target), key=lambda p: p[0])
        if start_nav <= 0:
            return None, "NAV galat"
        years = (end_d - start_d).days / 365.25
        cagr = ((end_nav / start_nav) ** (1 / years) - 1) * 100
        return {"cagr": round(cagr, 2), "period": period, "nav": end_nav,
                "nav_date": end_d.isoformat(), "from_date": start_d.isoformat()}, None
    return None, f"1 saal bhi pura nahi (start {pts[0][0]:%d-%b-%Y})"


# ---------------------------------------------------------------- ranking

def rank_min(values, higher_is_better):
    """Competition ranking: ties share the same rank, next rank skips (1, 1, 3)."""
    order = sorted(values, reverse=higher_is_better)
    return [order.index(v) + 1 for v in values]


def build(cat_key):
    cat = CATEGORIES[cat_key]
    funds, unclear = amfi_funds(cat["amfi"])
    try:
        groww = groww_details(cat["groww"])
    except Exception:
        groww = {}
    if unclear:
        with ThreadPoolExecutor(max_workers=8) as ex:
            oks = list(ex.map(lambda r: confirm_direct_growth(r, groww), unclear))
        funds += [r for r, ok in zip(unclear, oks) if ok]

    def enrich(f):
        info = dict(groww.get(f["code"], {}))
        if info.get("expense_ratio") is None or info.get("min_sip") is None or not info.get("exit_load_text"):
            k = kuvera_details(f["isin"])
            for field, val in k.items():
                if info.get(field) in (None, "") and val not in (None, ""):
                    info[field] = val
        try:
            c, why = best_cagr(f["code"])
        except Exception as e:
            c, why = None, f"NAV fetch error: {e.__class__.__name__}"
        el_key = parse_exit_load(info.get("exit_load_text"))
        row = {
            "code": f["code"], "isin": f["isin"], "name": f["name"],
            "cagr": c["cagr"] if c else None,
            "cagr_period": c["period"] if c else None,
            "nav": c["nav"] if c else None,
            "nav_date": c["nav_date"] if c else None,
            "cagr_from": c["from_date"] if c else None,
            "exit_load_text": info.get("exit_load_text") or "",
            "exit_load_key": list(el_key) if el_key else None,
            "exit_load_short": exit_load_label(el_key),
            "expense_ratio": info.get("expense_ratio"),
            "min_sip": info.get("min_sip"),
            "min_lumpsum": info.get("min_lumpsum"),
        }
        reasons = []
        if why:
            reasons.append(why)
        if row["expense_ratio"] is None:
            reasons.append("Expense ratio nahi mila")
        if el_key is None:
            reasons.append("Exit load samajh nahi aaya")
        row["skip_reason"] = "; ".join(reasons)
        return row

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(enrich, funds))

    ranked = [r for r in rows if not r["skip_reason"]]
    skipped = [r for r in rows if r["skip_reason"]]

    if ranked:
        r_cagr = rank_min([r["cagr"] for r in ranked], higher_is_better=True)
        r_el = rank_min([tuple(r["exit_load_key"][:2]) + (-r["exit_load_key"][2],) for r in ranked],
                        higher_is_better=False)
        r_er = rank_min([r["expense_ratio"] for r in ranked], higher_is_better=False)
        for r, a, b, c in zip(ranked, r_cagr, r_el, r_er):
            r["rank_cagr"], r["rank_exit_load"], r["rank_expense"] = a, b, c
            r["total"] = a + b + c
        # Total kam = best. Total tie ho to CAGR zyada wala upar.
        ranked.sort(key=lambda r: (r["total"], -r["cagr"]))
        finals = rank_min([r["total"] for r in ranked], higher_is_better=False)
        for r, fr in zip(ranked, finals):
            r["final_rank"] = fr

    skipped.sort(key=lambda r: r["name"])
    return {
        "category": cat["label"],
        "generated_at": datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST"),
        "total_funds": len(rows),
        "ranked": ranked,
        "skipped": skipped,
    }


# ---------------------------------------------------------------- web server

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/data/(\w+)\.json", u.path)
        if m:
            cat = m.group(1)
            if cat not in CATEGORIES:
                return self._send(400, json.dumps({"error": "cat must be large, mid or small"}),
                                  "application/json")
            if q.get("refresh") == ["1"]:
                clear_cache()
            try:
                result = cached("cat:" + cat, lambda: build(cat))
                return self._send(200, json.dumps(result), "application/json")
            except Exception as e:
                return self._send(502, json.dumps({"error": f"Data fetch fail hua: {e}"}),
                                  "application/json")
        self._send(404, "Not found", "text/plain")


def build_static(out_dir):
    """Write index.html + data/<cat>.json for GitHub Pages (run daily by GitHub Actions)."""
    out = Path(out_dir)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_bytes((HERE / "index.html").read_bytes())
    for cat in CATEGORIES:
        result = build(cat)
        (out / "data" / f"{cat}.json").write_text(json.dumps(result), encoding="utf-8")
        print(f"{cat}: {result['total_funds']} funds, {len(result['ranked'])} ranked")
        if not result["ranked"]:
            raise SystemExit(f"{cat}: koi fund rank nahi hua, data source check karo")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--build":
        build_static(sys.argv[2])
    else:
        print(f"Best SIP Finder chal raha hai -> http://localhost:{PORT}  (band karne ke liye Ctrl+C)")
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
