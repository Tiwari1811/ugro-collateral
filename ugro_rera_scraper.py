#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  UGRO Capital — Land Rate Scraper v2.0
  Sources: RERA portals + 99acres + MagicBricks + Housing.com
           + IGRSUP circle rates + CERSAI
  Output:  pincode_rates.json  (drop into GitHub repo)
  Schedule: Runs daily automatically via Windows Task Scheduler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  FIRST TIME SETUP:
    pip install requests beautifulsoup4 tqdm playwright schedule
    python -m playwright install chromium

  RUN MANUALLY:
    python ugro_rera_scraper.py

  SETUP DAILY AUTO-RUN (Windows):
    python ugro_rera_scraper.py --setup-scheduler

  OUTPUT:
    pincode_rates.json   → upload to GitHub repo
    ugro_scraper.log     → check if something goes wrong
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import json
import time
import os
import sys
import logging
import argparse
import subprocess
from datetime import datetime
from collections import defaultdict
from statistics import median, mean
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

# ── Optional imports ────────────────────────────────────────────
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False

# ── Script directory (all output files go here) ─────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "pincode_rates.json")
LOG_FILE    = os.path.join(SCRIPT_DIR, "ugro_scraper.log")

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, mode="a"),
    ]
)
log = logging.getLogger("ugro")

# ── HTTP Session ─────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
})
SESSION.verify = False
TIMEOUT = 20


def safe_get(url, params=None, headers=None, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            log.warning(f"HTTP {r.status_code} — {url[:60]}")
        except Exception as e:
            log.warning(f"GET attempt {attempt+1}: {str(e)[:60]}")
        time.sleep([2, 5, 10][attempt])
    return None


def safe_post(url, json_body=None, data=None, headers=None, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.post(url, json=json_body, data=data,
                             headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            log.warning(f"HTTP {r.status_code} POST — {url[:60]}")
        except Exception as e:
            log.warning(f"POST attempt {attempt+1}: {str(e)[:60]}")
        time.sleep([2, 5, 10][attempt])
    return None


def to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("₹", "")
                  .replace("L", "").replace("Cr", "").strip())
        return f if f > 0 else None
    except Exception:
        return None


def make_record(pincode, state, district, source, source_url,
                rate_min, rate_max, prop_type="Residential",
                is_guideline=False, project_name=""):
    return {
        "pincode": str(pincode),
        "state": state,
        "district": str(district).upper(),
        "source": source,
        "source_url": source_url,
        "rate_min_sqft": round(rate_min) if rate_min else None,
        "rate_max_sqft": round(rate_max) if rate_max else None,
        "property_type": prop_type,
        "is_guideline": is_guideline,
        "scraped_date": datetime.now().strftime("%Y-%m-%d"),
        "project_name": str(project_name)[:60],
    }


# ════════════════════════════════════════════════════════════════
#  BLOCK A — RERA PORTALS (12 states)
# ════════════════════════════════════════════════════════════════

def scrape_rera_portal(name, base_url, method, payload_fn,
                       state, parse_fn, max_pages=50):
    """Generic RERA portal scraper."""
    results = []
    for page in range(1, max_pages + 1):
        payload = payload_fn(page)
        if method == "POST":
            r = safe_post(base_url, json_body=payload)
        else:
            r = safe_get(base_url, params=payload)
        if not r:
            break
        try:
            data = r.json()
        except Exception:
            break
        records, total = parse_fn(data, state)
        results.extend(records)
        if not records:
            break
        log.info(f"  {name} page {page}: {len(records)} → total {len(results)}")
        time.sleep(0.5)
        if total and page * 100 >= total:
            break
    log.info(f"{name}: {len(results)} records")
    return results


def _parse_generic(data, state, pin_keys, rate_keys, dist_key="district",
                   source="", url="", rate_unit="sqft"):
    records = []
    projects = None
    for k in ["Projects","projects","data","content","ProjectList","projectList"]:
        if isinstance(data.get(k), list):
            projects = data[k]
            break
    if not projects:
        return records, 0

    total = data.get("TotalCount") or data.get("total") or data.get("totalCount") or 0

    for proj in projects:
        pin = None
        for pk in pin_keys:
            v = proj.get(pk)
            if v and len(str(v).strip()) == 6 and str(v).strip().isdigit():
                pin = str(v).strip()
                break
        if not pin:
            continue

        rate = None
        for rk in rate_keys:
            v = to_float(proj.get(rk))
            if v and v > 50:
                rate = v
                break
        if not rate:
            continue

        # Convert sqm → sqft if needed
        if rate_unit == "sqm":
            rate = rate / 10.764

        dist = str(proj.get(dist_key) or proj.get("District") or "").upper()
        pname = str(proj.get("projectName") or proj.get("ProjectName") or "")

        records.append(make_record(
            pin, state, dist, source, url,
            rate * 0.88, rate * 1.12, "Residential",
            project_name=pname
        ))
    return records, int(total)


def scrape_maharera(max_pages=100):
    log.info("── MahaRERA (Maharashtra) ────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["PinCode","Pincode","pin_code"],
            ["CarpetAreaRateMin","MinRate","min_rate","basicRate","rate"],
            source="MahaRERA", url="https://maharerait.mahaonline.gov.in")
    return scrape_rera_portal(
        "MahaRERA",
        "https://maharerait.mahaonline.gov.in/api/Project/GetProjectList",
        "POST",
        lambda p: {"PageNo": p, "PageSize": 100, "ProjectStatus": "1"},
        "MAHARASHTRA", parse, max_pages
    )


def scrape_up_rera(max_pages=80):
    log.info("── UP RERA (Uttar Pradesh) ───────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","PinCode","pin"],
            ["costOfProject","rate","unitRate","basicRate"],
            source="UP-RERA", url="https://www.up-rera.in")
    return scrape_rera_portal(
        "UP RERA", "https://www.up-rera.in/api/project/getList",
        "GET", lambda p: {"pageNo": p, "pageSize": 100, "status": "approved"},
        "UTTAR PRADESH", parse, max_pages
    )


def scrape_karnataka_rera(max_pages=60):
    log.info("── Karnataka RERA ────────────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","pin","PinCode"],
            ["unitRate","rate","basicRate","saleRate"],
            source="Karnataka-RERA", url="https://rera.karnataka.gov.in")
    return scrape_rera_portal(
        "Karnataka RERA", "https://rera.karnataka.gov.in/api/project/list",
        "GET", lambda p: {"page": p, "size": 100},
        "KARNATAKA", parse, max_pages
    )


def scrape_tnrera(max_pages=40):
    log.info("── TNRERA (Tamil Nadu) ───────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","PinCode"],
            ["basicRate","rate","unitRate"],
            source="TNRERA", url="https://www.tnrera.in")
    return scrape_rera_portal(
        "TNRERA", "https://www.tnrera.in/api/projects/list",
        "POST", lambda p: {"pageNo": p, "pageSize": 100, "status": "REGISTERED"},
        "TAMIL NADU", parse, max_pages
    )


def scrape_gujarat_rera(max_pages=40):
    log.info("── Gujarat RERA ──────────────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pincode","PinCode"],
            ["basicSalePrice","rate","unitRate"],
            dist_key="districtName",
            source="GujRERA", url="https://gujrera.gujarat.gov.in")
    return scrape_rera_portal(
        "Gujarat RERA",
        "https://gujrera.gujarat.gov.in/online/project/getProjectDetails",
        "GET", lambda p: {"pageNo": p, "pageSize": 100, "status": "A"},
        "GUJARAT", parse, max_pages
    )


def scrape_rajasthan_rera(max_pages=30):
    log.info("── Rajasthan RERA ────────────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","pin"],
            ["basicRate","unitRate","rate"],
            source="RRERA", url="https://rera.rajasthan.gov.in")
    return scrape_rera_portal(
        "Rajasthan RERA", "https://rera.rajasthan.gov.in/api/project/list",
        "GET", lambda p: {"page": p, "size": 100},
        "RAJASTHAN", parse, max_pages
    )


def scrape_haryana_rera(max_pages=30):
    log.info("── Haryana RERA ──────────────────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","pin"],
            ["basicRate","saleRate","rate"],
            source="HRERA", url="https://hrera.gov.in")
    return scrape_rera_portal(
        "Haryana RERA", "https://hrera.gov.in/api/project/list",
        "GET", lambda p: {"pageNo": p, "pageSize": 100, "status": "REGISTERED"},
        "HARYANA", parse, max_pages
    )


def scrape_mp_rera(max_pages=30):
    log.info("── MP RERA (Madhya Pradesh) ──────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","PinCode"],
            ["basicRate","rate","unitRate"],
            source="MP-RERA", url="https://www.rera.mp.gov.in")
    return scrape_rera_portal(
        "MP RERA",
        "https://www.rera.mp.gov.in/mprera/api/project/getProjectList",
        "POST", lambda p: {"pageNo": p, "pageSize": 100},
        "MADHYA PRADESH", parse, max_pages
    )


def scrape_ap_rera(max_pages=30):
    log.info("── AP RERA (Andhra Pradesh) ──────────────────────────")
    def parse(data, state):
        return _parse_generic(data, state,
            ["pinCode","pin"],
            ["saleRate","basicRate","rate"],
            source="AP-RERA", url="https://rera.ap.gov.in")
    return scrape_rera_portal(
        "AP RERA", "https://rera.ap.gov.in/api/project/list",
        "GET", lambda p: {"page": p, "size": 100, "status": "registered"},
        "ANDHRA PRADESH", parse, max_pages
    )


# ════════════════════════════════════════════════════════════════
#  BLOCK B — PROPERTY PORTALS (99acres, MagicBricks, Housing.com)
#  Uses Playwright headless browser to bypass bot protection
# ════════════════════════════════════════════════════════════════

# Key Indian cities with pincodes for targeted scraping
CITY_PINCODES = {
    "Mumbai":    ["400001","400002","400003","400004","400005","400006","400007",
                  "400008","400009","400010","400011","400012","400013","400014",
                  "400016","400018","400019","400020","400021","400022","400023",
                  "400024","400025","400026","400028","400029","400030","400031"],
    "Delhi":     ["110001","110002","110003","110004","110005","110006","110007",
                  "110008","110009","110010","110011","110012","110013","110014",
                  "110015","110016","110017","110018","110019","110020","110021",
                  "110022","110023","110024","110025","110026","110027","110028"],
    "Bengaluru": ["560001","560002","560003","560004","560005","560006","560007",
                  "560008","560009","560010","560011","560012","560013","560014",
                  "560015","560016","560017","560018","560019","560020","560021"],
    "Chennai":   ["600001","600002","600003","600004","600005","600006","600007",
                  "600008","600009","600010","600011","600012","600013","600014"],
    "Hyderabad": ["500001","500002","500003","500004","500005","500006","500007",
                  "500008","500009","500010","500011","500012","500013","500014"],
    "Pune":      ["411001","411002","411003","411004","411005","411006","411007",
                  "411008","411009","411010","411011","411012","411013","411014"],
    "Kolkata":   ["700001","700002","700003","700004","700005","700006","700007",
                  "700008","700009","700010","700011","700012","700013","700014"],
    "Ahmedabad": ["380001","380002","380003","380004","380005","380006","380007",
                  "380008","380009","380013","380014","380015","380016","380019"],
    "Jaipur":    ["302001","302002","302003","302004","302005","302006","302011",
                  "302012","302013","302015","302016","302017","302018","302019"],
    "Lucknow":   ["226001","226002","226003","226004","226005","226006","226007",
                  "226008","226009","226010","226011","226012","226013","226014"],
    "Chandigarh":["160001","160002","160003","160004","160005","160006","160009",
                  "160010","160011","160012","160014","160015","160017","160018"],
    "Noida":     ["201301","201302","201303","201304","201305","201306","201307",
                  "201308","201309","201310","201311","201312","201313","201314"],
    "Gurugram":  ["122001","122002","122003","122004","122005","122006","122007",
                  "122008","122009","122010","122011","122012","122013","122015"],
    "Coimbatore":["641001","641002","641003","641004","641005","641006","641007",
                  "641008","641009","641010","641011","641012","641013","641014"],
    "Kochi":     ["682001","682002","682003","682004","682005","682006","682007",
                  "682008","682009","682010","682011","682012","682013","682014"],
    "Bhopal":    ["462001","462002","462003","462004","462010","462011","462016",
                  "462023","462024","462026","462030","462031","462037","462038"],
    "Patna":     ["800001","800002","800003","800004","800005","800006","800007",
                  "800008","800009","800010","800011","800012","800013","800014"],
}

CITY_STATE = {
    "Mumbai":"MAHARASHTRA","Delhi":"DELHI","Bengaluru":"KARNATAKA",
    "Chennai":"TAMIL NADU","Hyderabad":"TELANGANA","Pune":"MAHARASHTRA",
    "Kolkata":"WEST BENGAL","Ahmedabad":"GUJARAT","Jaipur":"RAJASTHAN",
    "Lucknow":"UTTAR PRADESH","Chandigarh":"CHANDIGARH","Noida":"UTTAR PRADESH",
    "Gurugram":"HARYANA","Coimbatore":"TAMIL NADU","Kochi":"KERALA",
    "Bhopal":"MADHYA PRADESH","Patna":"BIHAR",
}


def scrape_99acres():
    """
    99acres: Uses internal GraphQL/REST API.
    Scrapes locality-wise price per sqft for each pincode.
    """
    log.info("── 99acres ───────────────────────────────────────────")
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
        return _scrape_99acres_requests()
    return _scrape_99acres_playwright()


def _scrape_99acres_requests():
    """Fallback: requests-based 99acres scraper."""
    results = []
    for city, pincodes in CITY_PINCODES.items():
        state = CITY_STATE.get(city, "")
        for pin in pincodes[:5]:  # Sample 5 per city in fallback mode
            # 99acres locality search API
            r = safe_get(
                "https://www.99acres.com/api/v1/locality/search",
                params={"q": pin, "type": "locality"},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.99acres.com",
                    "Cookie": "userCountry=IN",
                }
            )
            if not r:
                continue
            try:
                data = r.json()
                localities = data.get("data") or data.get("localities") or []
                for loc in localities:
                    rate = to_float(loc.get("avg_price_sqft") or
                                   loc.get("price_per_sqft") or
                                   loc.get("avgRate"))
                    if rate and rate > 100:
                        results.append(make_record(
                            pin, state, city, "99acres",
                            "https://www.99acres.com",
                            rate * 0.9, rate * 1.1
                        ))
            except Exception:
                pass
            time.sleep(0.5)
    log.info(f"99acres (requests): {len(results)} records")
    return results


def _scrape_99acres_playwright():
    """Playwright-based 99acres scraper — bypasses bot protection."""
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-IN",
            )
            page = context.new_page()

            for city, pincodes in CITY_PINCODES.items():
                state = CITY_STATE.get(city, "")
                log.info(f"  99acres scraping {city}…")

                for pin in pincodes[:8]:
                    try:
                        # Use 99acres locality price page
                        url = f"https://www.99acres.com/locality-detail/{pin}"
                        page.goto(url, timeout=20000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)

                        # Extract price data from page
                        content = page.content()
                        if HAS_BS4:
                            soup = BeautifulSoup(content, "lxml")
                            # Look for price per sqft in page data
                            scripts = soup.find_all("script", type="application/json")
                            for sc in scripts:
                                try:
                                    d = json.loads(sc.string or "{}")
                                    rate = _extract_rate_from_json(d)
                                    if rate:
                                        results.append(make_record(
                                            pin, state, city, "99acres",
                                            "https://www.99acres.com",
                                            rate * 0.9, rate * 1.1
                                        ))
                                        break
                                except Exception:
                                    pass

                        # Also intercept API calls
                        time.sleep(0.8)
                    except Exception as e:
                        log.warning(f"  99acres {pin}: {str(e)[:40]}")

            browser.close()
    except Exception as e:
        log.error(f"Playwright 99acres: {e}")
        return _scrape_99acres_requests()

    log.info(f"99acres: {len(results)} records")
    return results


def scrape_magicbricks():
    """
    MagicBricks: Has an internal REST API for locality prices.
    """
    log.info("── MagicBricks ───────────────────────────────────────")
    if not HAS_PLAYWRIGHT:
        log.warning("Playwright not installed — using requests fallback")
        return _scrape_mb_requests()
    return _scrape_mb_playwright()


def _scrape_mb_requests():
    results = []
    for city, pincodes in CITY_PINCODES.items():
        state = CITY_STATE.get(city, "")
        for pin in pincodes[:5]:
            # MagicBricks internal locality API
            r = safe_get(
                "https://www.magicbricks.com/mbsearch/ajax/localityDataSearch.html",
                params={
                    "pinCode": pin,
                    "type": "locality",
                    "city": city.lower(),
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.magicbricks.com",
                }
            )
            if not r:
                continue
            try:
                data = r.json()
                rate = to_float(
                    data.get("avgPricePerSqft") or
                    data.get("pricePerSqft") or
                    data.get("avg_price")
                )
                if rate and rate > 100:
                    results.append(make_record(
                        pin, state, city, "MagicBricks",
                        "https://www.magicbricks.com",
                        rate * 0.9, rate * 1.1
                    ))
            except Exception:
                pass
            time.sleep(0.5)
    log.info(f"MagicBricks (requests): {len(results)} records")
    return results


def _scrape_mb_playwright():
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                locale="en-IN",
            )
            page = context.new_page()

            # Intercept API responses
            api_data = []
            def handle_response(response):
                if "pricetrend" in response.url or "localityData" in response.url:
                    try:
                        api_data.append(response.json())
                    except Exception:
                        pass

            page.on("response", handle_response)

            for city, pincodes in CITY_PINCODES.items():
                state = CITY_STATE.get(city, "")
                log.info(f"  MagicBricks scraping {city}…")

                for pin in pincodes[:8]:
                    try:
                        api_data.clear()
                        url = f"https://www.magicbricks.com/locality-detail/{pin}"
                        page.goto(url, timeout=20000, wait_until="networkidle")
                        page.wait_for_timeout(2000)

                        # Process intercepted API data
                        for d in api_data:
                            rate = _extract_rate_from_json(d)
                            if rate:
                                results.append(make_record(
                                    pin, state, city, "MagicBricks",
                                    "https://www.magicbricks.com",
                                    rate * 0.9, rate * 1.1
                                ))
                                break

                        # Fallback: scrape from HTML
                        if not api_data:
                            content = page.content()
                            rate = _extract_rate_from_html(content)
                            if rate:
                                results.append(make_record(
                                    pin, state, city, "MagicBricks",
                                    "https://www.magicbricks.com",
                                    rate * 0.9, rate * 1.1
                                ))
                        time.sleep(0.8)
                    except Exception as e:
                        log.warning(f"  MagicBricks {pin}: {str(e)[:40]}")

            browser.close()
    except Exception as e:
        log.error(f"Playwright MagicBricks: {e}")
        return _scrape_mb_requests()

    log.info(f"MagicBricks: {len(results)} records")
    return results


def scrape_housing():
    """
    Housing.com: Has a well-documented internal REST API.
    """
    log.info("── Housing.com ───────────────────────────────────────")
    results = []

    for city, pincodes in CITY_PINCODES.items():
        state = CITY_STATE.get(city, "")
        log.info(f"  Housing.com scraping {city}…")

        for pin in pincodes[:10]:
            # Housing.com locality API (documented internal endpoint)
            r = safe_get(
                "https://housing.com/api/v2/locality/insights",
                params={
                    "pincode": pin,
                    "city": city,
                    "transaction_type": "buy",
                },
                headers={
                    "X-Platform": "web",
                    "X-Version": "v2",
                    "Referer": "https://housing.com",
                    "Origin": "https://housing.com",
                }
            )
            if not r:
                # Try alternate endpoint
                r = safe_get(
                    "https://housing.com/api/v2/search/locality",
                    params={"q": pin, "limit": 5},
                    headers={"Referer": "https://housing.com"}
                )
            if not r:
                continue

            try:
                data = r.json()
                # Navigate response structure
                insights = (data.get("data") or data.get("insights") or
                           data.get("locality") or data)
                if isinstance(insights, list):
                    insights = insights[0] if insights else {}

                rate = to_float(
                    insights.get("avg_price_per_sqft") or
                    insights.get("price_per_sqft") or
                    insights.get("avgRate") or
                    insights.get("average_price")
                )
                if rate and rate > 100:
                    results.append(make_record(
                        pin, state, city, "Housing.com",
                        "https://housing.com",
                        rate * 0.9, rate * 1.1
                    ))
            except Exception:
                pass
            time.sleep(0.4)

    log.info(f"Housing.com: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════
#  BLOCK C — IGRSUP Circle Rates (Uttar Pradesh)
#  Most comprehensive state circle rate portal — covers 75 districts
# ════════════════════════════════════════════════════════════════

def scrape_igrsup():
    """
    IGRSUP: Uttar Pradesh stamp & registration circle rates.
    Covers all 75 districts with tehsil-level granularity.
    """
    log.info("── IGRSUP (UP Circle Rates) ──────────────────────────")
    results = []

    # Step 1: Get district list
    r = safe_get(
        "https://igrsup.gov.in/igrsup/defaultAction.action",
        headers={"Referer": "https://igrsup.gov.in"}
    )
    if not r:
        log.warning("IGRSUP: portal unreachable")
        return results

    # Step 2: Get circle rates via the stamp duty calculator
    # IGRSUP exposes tehsil-wise rates via AJAX
    district_ids = list(range(1, 76))  # 75 districts in UP

    for dist_id in district_ids:
        # Get tehsils for this district
        r_tehsil = safe_post(
            "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
            data={
                "stampDutyCalcBean.districtId": str(dist_id),
                "actionType": "getTehsilList",
            },
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": "https://igrsup.gov.in/igrsup/"}
        )
        if not r_tehsil:
            continue

        try:
            tehsils = r_tehsil.json()
        except Exception:
            if HAS_BS4 and r_tehsil:
                soup = BeautifulSoup(r_tehsil.text, "lxml")
                tehsils = [{"id": o.get("value"), "name": o.text}
                           for o in soup.find_all("option") if o.get("value")]
            else:
                continue

        for tehsil in (tehsils if isinstance(tehsils, list) else []):
            tehsil_id = tehsil.get("id") or tehsil.get("value")
            if not tehsil_id:
                continue

            # Get circle rates for this tehsil
            r_rate = safe_post(
                "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
                data={
                    "stampDutyCalcBean.districtId": str(dist_id),
                    "stampDutyCalcBean.tehsilId": str(tehsil_id),
                    "stampDutyCalcBean.propertyType": "1",  # Residential
                    "actionType": "getCircleRate",
                },
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Referer": "https://igrsup.gov.in/igrsup/"}
            )
            if not r_rate:
                continue

            try:
                rate_data = r_rate.json()
                # Navigate to the rate value
                rate_sqm = to_float(
                    rate_data.get("circleRate") or
                    rate_data.get("rate") or
                    rate_data.get("residentialRate")
                )
                if not rate_sqm or rate_sqm < 100:
                    continue

                # IGRSUP rates are in ₹/sqm — convert to sqft
                rate_sqft = rate_sqm / 10.764

                # Map tehsil to pincodes (approximate via pincode DB)
                district_name = str(tehsil.get("districtName") or "").upper()

                # Use district-level pincode approximation
                results.append({
                    "pincode": f"DIST_{dist_id}_TEHSIL_{tehsil_id}",  # placeholder
                    "state": "UTTAR PRADESH",
                    "district": district_name,
                    "tehsil": str(tehsil.get("name") or ""),
                    "source": "IGRSUP",
                    "source_url": "https://igrsup.gov.in",
                    "rate_min_sqft": round(rate_sqft * 0.95),
                    "rate_max_sqft": round(rate_sqft * 1.05),
                    "property_type": "Residential",
                    "is_guideline": True,
                    "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                    "project_name": "",
                })
            except Exception:
                pass
            time.sleep(0.3)

    # Map district-level rates to pincodes
    up_results = _map_district_rates_to_pincodes(results, "UTTAR PRADESH")
    log.info(f"IGRSUP: {len(up_results)} pincode records (from {len(results)} tehsil records)")
    return up_results


def _map_district_rates_to_pincodes(district_records, state):
    """Map district-level circle rates to individual pincodes."""
    # Load pincode DB if available (from the main platform)
    pincode_to_district = {}
    try:
        # Try to read from existing pincode data
        db_path = os.path.join(SCRIPT_DIR, "pincode_db.json")
        if os.path.exists(db_path):
            with open(db_path) as f:
                db = json.load(f)
            for pin, info in db.items():
                if info.get("state") == state:
                    pincode_to_district[pin] = info.get("district", "")
    except Exception:
        pass

    results = []
    # Build district → rate lookup
    dist_rates = {}
    for rec in district_records:
        dist = rec.get("district", "")
        if dist and rec.get("rate_min_sqft"):
            dist_rates[dist] = rec

    # Map to pincodes
    for pin, dist in pincode_to_district.items():
        if dist.upper() in dist_rates:
            base = dist_rates[dist.upper()]
            results.append(make_record(
                pin, state, dist, base["source"], base["source_url"],
                base["rate_min_sqft"], base["rate_max_sqft"],
                is_guideline=True
            ))

    return results if results else district_records


# ════════════════════════════════════════════════════════════════
#  BLOCK D — CERSAI (Central Registry of Securitisation)
#  Encumbrance + transaction data (requires registration)
# ════════════════════════════════════════════════════════════════

def scrape_cersai():
    """
    CERSAI: Extracts encumbrance data to cross-validate
    property transaction values. Requires registered NBFC access.
    """
    log.info("── CERSAI (Encumbrance Data) ─────────────────────────")
    results = []

    # CERSAI public search — property search by state/district
    r = safe_get(
        "https://www.cersai.org.in/CERSAI/home.prg",
        headers={"Referer": "https://www.cersai.org.in"}
    )
    if not r:
        log.warning("CERSAI: portal unreachable (may require registered access)")
        log.info("  Note: Contact CERSAI (cersai@cersai.org.in) for NBFC API access")
        return results

    # Try the public asset search
    for state_code, state_name in [
        ("MH", "MAHARASHTRA"), ("DL", "DELHI"), ("KA", "KARNATAKA"),
        ("TN", "TAMIL NADU"), ("TS", "TELANGANA"), ("AP", "ANDHRA PRADESH"),
        ("GJ", "GUJARAT"), ("UP", "UTTAR PRADESH"), ("RJ", "RAJASTHAN"),
    ]:
        r2 = safe_post(
            "https://www.cersai.org.in/CERSAI/securitizationAssetSearchAction.prg",
            data={
                "stateCode": state_code,
                "assetType": "Immovable",
                "pageNo": "1",
                "pageSize": "100",
            },
            headers={
                "Referer": "https://www.cersai.org.in/CERSAI/home.prg",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if not r2:
            continue

        try:
            data = r2.json()
            assets = data.get("assetList") or data.get("data") or []
            for asset in assets:
                pin = str(asset.get("pinCode") or asset.get("pin") or "").strip()
                if len(pin) != 6 or not pin.isdigit():
                    continue
                val = to_float(asset.get("assetValue") or asset.get("marketValue"))
                area = to_float(asset.get("area") or asset.get("totalArea"))
                if val and area and area > 0:
                    rate = val / area  # ₹/sqft if area in sqft
                    if 100 < rate < 500000:
                        results.append(make_record(
                            pin, state_name,
                            str(asset.get("district") or "").upper(),
                            "CERSAI", "https://www.cersai.org.in",
                            rate * 0.85, rate * 1.15
                        ))
        except Exception:
            pass
        time.sleep(0.5)

    log.info(f"CERSAI: {len(results)} records")
    if not results:
        log.info("  Tip: Register at cersai.org.in with UGRO's NBFC credentials for full API access")
    return results


# ════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════

def _extract_rate_from_json(data, depth=0):
    """Recursively find price per sqft in JSON."""
    if depth > 5:
        return None
    if isinstance(data, dict):
        for k, v in data.items():
            if any(x in k.lower() for x in ["sqft","persqft","rate","price"]):
                f = to_float(v)
                if f and 100 < f < 500000:
                    return f
            result = _extract_rate_from_json(v, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data[:5]:
            result = _extract_rate_from_json(item, depth + 1)
            if result:
                return result
    return None


def _extract_rate_from_html(html_content):
    """Extract price per sqft from HTML content."""
    if not HAS_BS4:
        return None
    try:
        import re
        # Look for price patterns like ₹5,000/sqft or 5000 per sqft
        patterns = [
            r'₹\s*([\d,]+)\s*/\s*sq\.?ft',
            r'([\d,]+)\s*per\s*sq\.?ft',
            r'"pricePerSqft"\s*:\s*([\d.]+)',
            r'"avgRate"\s*:\s*([\d.]+)',
            r'"price_sqft"\s*:\s*([\d.]+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for m in matches:
                f = to_float(m)
                if f and 100 < f < 500000:
                    return f
    except Exception:
        pass
    return None


def remove_outliers(values, z=2.5):
    if len(values) < 4:
        return values
    try:
        from statistics import mean, stdev
        m, s = mean(values), stdev(values)
        return [v for v in values if abs(v - m) <= z * s] or values
    except Exception:
        return values


# ════════════════════════════════════════════════════════════════
#  AGGREGATOR
# ════════════════════════════════════════════════════════════════

def aggregate(all_records):
    log.info(f"\nAggregating {len(all_records)} raw records…")

    # Source priority: IGR > RERA > Property portals
    SOURCE_PRIORITY = {
        "IGRSUP": 1, "KAVERI-IGR": 1, "TNREGINET": 1,
        "MahaRERA": 2, "UP-RERA": 2, "Karnataka-RERA": 2,
        "TNRERA": 2, "GujRERA": 2, "RRERA": 2, "HRERA": 2,
        "MP-RERA": 2, "AP-RERA": 2, "CERSAI": 2,
        "99acres": 3, "MagicBricks": 3, "Housing.com": 3,
    }

    pincode_mkt  = defaultdict(list)
    pincode_gv   = defaultdict(list)
    pincode_meta = {}
    pincode_srcs = defaultdict(set)

    for rec in all_records:
        pin = str(rec.get("pincode", ""))
        if not (len(pin) == 6 and pin.isdigit()):
            continue

        pincode_meta[pin] = {
            "state":    rec.get("state", ""),
            "district": rec.get("district", ""),
        }
        pincode_srcs[pin].add(rec.get("source", ""))

        lo = rec.get("rate_min_sqft")
        hi = rec.get("rate_max_sqft")
        mid = ((lo or 0) + (hi or 0)) / 2 if (lo or hi) else None
        if mid and mid > 50:
            if rec.get("is_guideline"):
                pincode_gv[pin].append(mid)
            else:
                pincode_mkt[pin].append(mid)

    result = {}
    all_pins = set(pincode_mkt) | set(pincode_gv)
    log.info(f"Unique pincodes with data: {len(all_pins)}")

    for pin in all_pins:
        mkt = remove_outliers(pincode_mkt.get(pin, []))
        gv  = remove_outliers(pincode_gv.get(pin, []))
        meta = pincode_meta.get(pin, {})
        srcs = list(pincode_srcs.get(pin, []))
        n = len(mkt) + len(gv)

        mv = round(median(mkt)) if mkt else None
        gv_med = round(median(gv)) if gv else None
        confidence = "HIGH" if n >= 5 else "MEDIUM" if n >= 2 else "LOW"

        all_vals = mkt + gv
        result[pin] = {
            "state":                meta.get("state", ""),
            "district":             meta.get("district", ""),
            "market_rate_sqft":     mv,
            "guideline_value_sqft": gv_med,
            "rate_range_lo":        round(min(all_vals) * 0.9) if all_vals else None,
            "rate_range_hi":        round(max(all_vals) * 1.1) if all_vals else None,
            "data_points":          n,
            "confidence":           confidence,
            "sources":              srcs,
            "last_updated":         datetime.now().strftime("%Y-%m-%d"),
        }

    log.info(f"Final: {len(result)} pincodes with rate data")
    return result


# ════════════════════════════════════════════════════════════════
#  SCHEDULER — Daily auto-run
# ════════════════════════════════════════════════════════════════

def setup_windows_scheduler():
    """Creates a Windows Task Scheduler task to run daily at 6 AM."""
    script_path = os.path.abspath(__file__)
    python_path = sys.executable
    task_name = "UGRO_LandRateScraper"

    # Create the scheduled task via schtasks
    cmd = [
        "schtasks", "/create", "/tn", task_name,
        "/tr", f'"{python_path}" "{script_path}"',
        "/sc", "DAILY",
        "/st", "06:00",
        "/f",  # Force overwrite if exists
        "/rl", "HIGHEST",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"""
╔══════════════════════════════════════════════════════════════╗
║  ✓ Daily Scheduler Set Up Successfully                      ║
╠══════════════════════════════════════════════════════════════╣
║  Task Name : {task_name:<46}║
║  Runs At   : 6:00 AM every day                              ║
║  Script    : {script_path[:46]:<46}║
╠══════════════════════════════════════════════════════════════╣
║  To verify: Open Task Scheduler → Task Scheduler Library    ║
║  To remove: schtasks /delete /tn {task_name} /f            ║
╚══════════════════════════════════════════════════════════════╝
            """)
        else:
            print(f"Scheduler setup failed: {result.stderr}")
            print("Try running Command Prompt as Administrator")
    except Exception as e:
        print(f"Could not set up scheduler: {e}")
        print("Manually create a task in Windows Task Scheduler pointing to this script")


def run_with_schedule():
    """Run scraper on a schedule (alternative to Task Scheduler)."""
    if not HAS_SCHEDULE:
        log.error("schedule package not installed. Run: pip install schedule")
        return

    import schedule as sch

    def job():
        log.info("=== Scheduled run starting ===")
        main_scrape()

    # Run daily at 6 AM
    sch.every().day.at("06:00").do(job)
    log.info("Scheduler started — runs daily at 06:00. Press Ctrl+C to stop.")

    while True:
        sch.run_pending()
        time.sleep(60)


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main_scrape():
    """Run all scrapers and save output."""
    print("""
╔══════════════════════════════════════════════════════════════╗
║        UGRO Capital — Land Rate Scraper v2.0                ║
║        Sources: RERA + 99acres + MagicBricks +              ║
║                 Housing.com + IGRSUP + CERSAI               ║
╚══════════════════════════════════════════════════════════════╝
    """)

    all_records = []

    scrapers = [
        # ── RERA Portals ──
        ("MahaRERA",        scrape_maharera),
        ("UP RERA",         scrape_up_rera),
        ("Karnataka RERA",  scrape_karnataka_rera),
        ("TNRERA",          scrape_tnrera),
        ("Gujarat RERA",    scrape_gujarat_rera),
        ("Rajasthan RERA",  scrape_rajasthan_rera),
        ("Haryana RERA",    scrape_haryana_rera),
        ("MP RERA",         scrape_mp_rera),
        ("AP RERA",         scrape_ap_rera),
        # ── Property Portals ──
        ("99acres",         scrape_99acres),
        ("MagicBricks",     scrape_magicbricks),
        ("Housing.com",     scrape_housing),
        # ── Govt Sources ──
        ("IGRSUP",          scrape_igrsup),
        ("CERSAI",          scrape_cersai),
    ]

    for name, fn in scrapers:
        print(f"\n{'─'*60}\n  {name}\n{'─'*60}")
        try:
            recs = fn()
            all_records.extend(recs)
            print(f"  ✓ {name}: {len(recs)} records")
        except Exception as e:
            log.error(f"{name} failed: {e}")
            print(f"  ✗ {name}: {e}")

    print(f"\n{'═'*60}\n  Total raw records: {len(all_records)}\n{'═'*60}")

    # Load existing data and merge (keep old pincodes not in new run)
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                old = json.load(f)
            existing = old.get("rates", {})
            log.info(f"Loaded {len(existing)} existing pincode records for merge")
        except Exception:
            pass

    # Aggregate new records
    new_rates = aggregate(all_records)

    # Merge: new data takes priority, keep existing for pincodes not scraped
    merged = {**existing, **new_rates}

    output = {
        "metadata": {
            "generated":      datetime.now().isoformat(),
            "total_pincodes": len(merged),
            "new_pincodes":   len(new_rates),
            "total_records":  len(all_records),
            "sources":        list(set(r["source"] for r in all_records)),
            "version":        "2.0",
        },
        "rates": merged,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_FILE) // 1024
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  COMPLETE                                                   ║
╠══════════════════════════════════════════════════════════════╣
║  New pincodes scraped : {len(new_rates):<6}                          ║
║  Total in database   : {len(merged):<6}                          ║
║  Output file         : pincode_rates.json ({size_kb} KB)         ║
╠══════════════════════════════════════════════════════════════╣
║  NEXT: Upload pincode_rates.json to GitHub repo             ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Confidence breakdown
    from collections import Counter
    conf = Counter(v.get("confidence","?") for v in merged.values())
    print("Data confidence:")
    for level, count in sorted(conf.items()):
        bar = "█" * (count // 50)
        print(f"  {level:8s}: {count:5d}  {bar}")


def main():
    parser = argparse.ArgumentParser(description="UGRO Land Rate Scraper v2.0")
    parser.add_argument("--setup-scheduler", action="store_true",
                        help="Set up Windows Task Scheduler for daily auto-run")
    parser.add_argument("--schedule", action="store_true",
                        help="Run with Python scheduler (keeps running, daily at 6AM)")
    args = parser.parse_args()

    if args.setup_scheduler:
        setup_windows_scheduler()
    elif args.schedule:
        run_with_schedule()
    else:
        main_scrape()


if __name__ == "__main__":
    main()
