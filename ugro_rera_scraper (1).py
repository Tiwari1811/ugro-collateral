#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  UGRO Capital — RERA & IGR Land Rate Scraper
  Produces: pincode_rates.json  (loaded by ugro_collateral_platform.html)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  HOW TO RUN:
    pip install requests beautifulsoup4 pandas tqdm
    python ugro_rera_scraper.py

  OUTPUT:
    pincode_rates.json  — place in same folder as ugro_collateral_platform.html

  SOURCES SCRAPED:
    1. MahaRERA          → Maharashtra (100K+ projects)
    2. UP RERA           → Uttar Pradesh
    3. Karnataka RERA    → Karnataka
    4. TNRERA            → Tamil Nadu
    5. Gujarat RERA      → Gujarat
    6. Rajasthan RERA    → Rajasthan
    7. Telangana IGRS    → Market Value Assistance
    8. AP RERA           → Andhra Pradesh
    9. Haryana RERA      → Haryana
   10. MP RERA           → Madhya Pradesh
   11. KAVERI IGR        → Karnataka Circle Rates
   12. TNREGINET         → Tamil Nadu Guideline Values
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import json
import time
import os
import sys
import logging
from datetime import datetime
from collections import defaultdict
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

# ── Try importing optional packages ────────────────────────────────
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    class tqdm:
        def __init__(self, iterable=None, **kw):
            self.iterable = iterable or []
        def __iter__(self): return iter(self.iterable)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def set_description(self, s): print(f"  → {s}")
        def update(self, n=1): pass

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("ugro_scraper.log", mode="w"),
    ]
)
log = logging.getLogger("ugro")

# ── Session with retry + browser headers ────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
})
SESSION.verify = False

TIMEOUT = 20
RETRY_DELAYS = [2, 5, 10]


def safe_get(url, params=None, headers=None, retries=3):
    for attempt, delay in enumerate(RETRY_DELAYS[:retries]):
        try:
            r = SESSION.get(url, params=params, headers=headers,
                            timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            log.warning(f"HTTP {r.status_code} on {url}")
        except requests.exceptions.Timeout:
            log.warning(f"Timeout attempt {attempt+1} — {url}")
        except requests.exceptions.ConnectionError as e:
            log.warning(f"Connection error attempt {attempt+1}: {e}")
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def safe_post(url, json_body=None, data=None, headers=None, retries=3):
    for attempt, delay in enumerate(RETRY_DELAYS[:retries]):
        try:
            r = SESSION.post(url, json=json_body, data=data,
                             headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            log.warning(f"HTTP {r.status_code} POST {url}")
        except Exception as e:
            log.warning(f"POST attempt {attempt+1}: {e}")
        if attempt < retries - 1:
            time.sleep(delay)
    return None


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 1 — MahaRERA  (Maharashtra)
#  Endpoint: documented public REST API
#  Returns: project list with pincode + declared rates
# ════════════════════════════════════════════════════════════════════
def scrape_maharera(max_pages=50):
    log.info("── MahaRERA ─────────────────────────────────────────")
    results = []
    base_url = "https://maharerait.mahaonline.gov.in/api/Project/GetProjectList"

    for page in range(1, max_pages + 1):
        payload = {
            "PageNo": page,
            "PageSize": 100,
            "ProjectStatus": "1",
            "IsOnlyMaharashtra": True,
        }
        r = safe_post(base_url, json_body=payload)
        if not r:
            log.warning(f"MahaRERA page {page}: no response, stopping")
            break

        try:
            data = r.json()
        except Exception:
            log.warning(f"MahaRERA page {page}: JSON parse failed")
            break

        # Response structure: {"Projects": [...], "TotalCount": N}
        projects = data.get("Projects") or data.get("projects") or []
        if not projects:
            # Try alternate key names
            for key in data:
                if isinstance(data[key], list) and len(data[key]) > 0:
                    projects = data[key]
                    break

        if not projects:
            log.info(f"MahaRERA: no more projects at page {page}")
            break

        for proj in projects:
            pin = str(proj.get("PinCode") or proj.get("Pincode") or
                      proj.get("pin_code") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue

            # Extract declared rates (carpet area rate in ₹/sqft)
            rate_min = _to_float(proj.get("CarpetAreaRateMin") or
                                 proj.get("MinRate") or proj.get("min_rate"))
            rate_max = _to_float(proj.get("CarpetAreaRateMax") or
                                 proj.get("MaxRate") or proj.get("max_rate"))
            # Some portals report per sqm — convert
            unit = str(proj.get("RateUnit") or "sqft").lower()
            if "sqm" in unit or "sq.m" in unit:
                rate_min = rate_min / 10.764 if rate_min else None
                rate_max = rate_max / 10.764 if rate_max else None

            if not (rate_min or rate_max):
                continue

            results.append({
                "pincode": pin,
                "state": "MAHARASHTRA",
                "district": str(proj.get("District") or proj.get("district") or "").upper(),
                "source": "MahaRERA",
                "source_url": "https://maharerait.mahaonline.gov.in",
                "rate_min_sqft": round(rate_min, 0) if rate_min else None,
                "rate_max_sqft": round(rate_max, 0) if rate_max else None,
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("ProjectName") or "")[:60],
            })

        log.info(f"  MahaRERA page {page}: {len(projects)} projects → {len(results)} with rates")
        time.sleep(0.5)

        total = data.get("TotalCount") or data.get("total_count") or 0
        if total and page * 100 >= total:
            break

    log.info(f"MahaRERA: {len(results)} rate records collected")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 2 — UP RERA  (Uttar Pradesh)
# ════════════════════════════════════════════════════════════════════
def scrape_up_rera(max_pages=50):
    log.info("── UP RERA ──────────────────────────────────────────")
    results = []
    base_url = "https://www.up-rera.in/api/project/getList"

    for page in range(1, max_pages + 1):
        r = safe_get(base_url, params={
            "pageNo": page, "pageSize": 100, "status": "approved"
        })
        if not r:
            break

        try:
            data = r.json()
        except Exception:
            break

        projects = (data.get("projects") or data.get("data") or
                    data.get("ProjectList") or [])
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or proj.get("PinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("costOfProject") or
                             proj.get("rate") or proj.get("unitRate"))
            if not rate or rate < 100:
                continue
            results.append({
                "pincode": pin,
                "state": "UTTAR PRADESH",
                "district": str(proj.get("district") or proj.get("District") or "").upper(),
                "source": "UP-RERA",
                "source_url": "https://www.up-rera.in",
                "rate_min_sqft": round(rate * 0.85),
                "rate_max_sqft": round(rate * 1.15),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })

        log.info(f"  UP RERA page {page}: {len(results)} records so far")
        time.sleep(0.5)
        if len(projects) < 100:
            break

    log.info(f"UP RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 3 — Karnataka RERA
# ════════════════════════════════════════════════════════════════════
def scrape_karnataka_rera(max_pages=40):
    log.info("── Karnataka RERA ───────────────────────────────────")
    results = []
    endpoints = [
        "https://rera.karnataka.gov.in/viewAllProjects",
        "https://rera.karnataka.gov.in/api/project/list",
    ]

    for base_url in endpoints:
        for page in range(1, max_pages + 1):
            r = safe_get(base_url, params={"page": page, "size": 100})
            if not r:
                continue

            try:
                data = r.json()
                projects = (data.get("content") or data.get("projects") or
                            data.get("data") or [])
            except Exception:
                # Try HTML scraping
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "lxml")
                # Extract from table rows
                rows = soup.select("table tbody tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) >= 5:
                        pin_text = cells[-1].get_text(strip=True)
                        if len(pin_text) == 6 and pin_text.isdigit():
                            results.append({
                                "pincode": pin_text,
                                "state": "KARNATAKA",
                                "district": "",
                                "source": "Karnataka-RERA",
                                "source_url": "https://rera.karnataka.gov.in",
                                "rate_min_sqft": None,
                                "rate_max_sqft": None,
                                "property_type": "Residential",
                                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                                "project_name": cells[0].get_text(strip=True)[:60],
                            })
                break

            if not projects:
                break

            for proj in projects:
                pin = str(proj.get("pinCode") or proj.get("pin") or "").strip()
                if len(pin) != 6 or not pin.isdigit():
                    continue
                rate = _to_float(proj.get("unitRate") or proj.get("rate"))
                if rate and rate > 100:
                    results.append({
                        "pincode": pin,
                        "state": "KARNATAKA",
                        "district": str(proj.get("district") or "").upper(),
                        "source": "Karnataka-RERA",
                        "source_url": "https://rera.karnataka.gov.in",
                        "rate_min_sqft": round(rate * 0.9),
                        "rate_max_sqft": round(rate * 1.1),
                        "property_type": "Residential",
                        "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                        "project_name": str(proj.get("projectName") or "")[:60],
                    })
            time.sleep(0.4)

    log.info(f"Karnataka RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 4 — Tamil Nadu RERA (TNRERA)
# ════════════════════════════════════════════════════════════════════
def scrape_tnrera(max_pages=30):
    log.info("── TNRERA ───────────────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_post(
            "https://www.tnrera.in/api/projects/list",
            json_body={"pageNo": page, "pageSize": 100, "status": "REGISTERED"},
        )
        if not r:
            r = safe_get(
                "https://www.tnrera.in/Home/ProjectList",
                params={"page": page, "rows": 100},
            )
        if not r:
            break

        try:
            data = r.json()
            projects = data.get("data") or data.get("projects") or []
        except Exception:
            break

        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or proj.get("PinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("basicRate") or proj.get("rate") or
                             proj.get("unitRate"))
            if not rate or rate < 200:
                continue
            results.append({
                "pincode": pin,
                "state": "TAMIL NADU",
                "district": str(proj.get("district") or "").upper(),
                "source": "TNRERA",
                "source_url": "https://www.tnrera.in",
                "rate_min_sqft": round(rate * 0.88),
                "rate_max_sqft": round(rate * 1.12),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"TNRERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 5 — Gujarat RERA
# ════════════════════════════════════════════════════════════════════
def scrape_gujarat_rera(max_pages=30):
    log.info("── Gujarat RERA ─────────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_get(
            "https://gujrera.gujarat.gov.in/online/project/getProjectDetails",
            params={"pageNo": page, "pageSize": 100, "status": "A"},
        )
        if not r:
            break
        try:
            data = r.json()
            projects = data.get("projectDetails") or data.get("data") or []
        except Exception:
            break
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pincode") or proj.get("PinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("basicSalePrice") or proj.get("rate"))
            if not rate or rate < 200:
                continue
            results.append({
                "pincode": pin,
                "state": "GUJARAT",
                "district": str(proj.get("districtName") or "").upper(),
                "source": "GujRERA",
                "source_url": "https://gujrera.gujarat.gov.in",
                "rate_min_sqft": round(rate * 0.88),
                "rate_max_sqft": round(rate * 1.12),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"Gujarat RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 6 — Rajasthan RERA
# ════════════════════════════════════════════════════════════════════
def scrape_rajasthan_rera(max_pages=20):
    log.info("── Rajasthan RERA ───────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_get(
            "https://rera.rajasthan.gov.in/api/project/list",
            params={"page": page, "size": 100},
        )
        if not r:
            break
        try:
            data = r.json()
            projects = data.get("content") or data.get("data") or []
        except Exception:
            break
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("basicRate") or proj.get("unitRate"))
            if not rate or rate < 100:
                continue
            results.append({
                "pincode": pin,
                "state": "RAJASTHAN",
                "district": str(proj.get("district") or "").upper(),
                "source": "RRERA",
                "source_url": "https://rera.rajasthan.gov.in",
                "rate_min_sqft": round(rate * 0.85),
                "rate_max_sqft": round(rate * 1.15),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"Rajasthan RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 7 — Telangana IGRS  (Market Value Assistance)
# ════════════════════════════════════════════════════════════════════
def scrape_telangana_igrs():
    log.info("── Telangana IGRS ───────────────────────────────────")
    results = []

    # Telangana IGRS has a documented market value lookup API
    # that returns rates by mandal/village
    mandal_url = "https://igrs.telangana.gov.in/en/market-value-assistance"
    r = safe_get(mandal_url)
    if not r:
        log.warning("Telangana IGRS: portal unreachable")
        return results

    # Try the AJAX endpoint for market value data
    ajax_url = "https://igrs.telangana.gov.in/api/mv/getDetails"
    # Iterate over key Telangana pincodes
    ts_pincodes_sample = [
        "500001", "500002", "500003", "500004", "500005", "500006",
        "500008", "500010", "500016", "500018", "500032", "500034",
        "500038", "500040", "500044", "500050", "500060", "500070",
        "500072", "500080", "500081", "500082", "500083", "500084",
        "500085", "500086", "500087", "500088", "500089", "500090",
        "501101", "501201", "501301", "501401", "501501",
        "502001", "503001", "504001", "505001", "506001",
        "507001", "508001", "509001", "510001", "511001",
    ]

    for pin in ts_pincodes_sample:
        r2 = safe_post(ajax_url, json_body={"pincode": pin})
        if r2:
            try:
                d = r2.json()
                rate = _to_float(d.get("rate") or d.get("marketValue"))
                if rate and rate > 50:
                    results.append({
                        "pincode": pin,
                        "state": "TELANGANA",
                        "district": str(d.get("district") or "").upper(),
                        "source": "IGRS-Telangana",
                        "source_url": "https://igrs.telangana.gov.in",
                        "rate_min_sqft": round(rate * 0.9),
                        "rate_max_sqft": round(rate * 1.1),
                        "property_type": "Residential",
                        "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                        "project_name": "",
                        "is_guideline": True,
                    })
            except Exception:
                pass
        time.sleep(0.3)

    log.info(f"Telangana IGRS: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 8 — Haryana RERA
# ════════════════════════════════════════════════════════════════════
def scrape_haryana_rera(max_pages=20):
    log.info("── Haryana RERA ─────────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_get(
            "https://hrera.gov.in/api/project/list",
            params={"pageNo": page, "pageSize": 100, "status": "REGISTERED"},
        )
        if not r:
            break
        try:
            data = r.json()
            projects = data.get("projects") or data.get("data") or []
        except Exception:
            break
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or proj.get("pin") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("basicRate") or proj.get("saleRate"))
            if not rate or rate < 200:
                continue
            results.append({
                "pincode": pin,
                "state": "HARYANA",
                "district": str(proj.get("district") or "").upper(),
                "source": "HRERA",
                "source_url": "https://hrera.gov.in",
                "rate_min_sqft": round(rate * 0.88),
                "rate_max_sqft": round(rate * 1.12),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"Haryana RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 9 — Madhya Pradesh RERA
# ════════════════════════════════════════════════════════════════════
def scrape_mp_rera(max_pages=20):
    log.info("── MP RERA ──────────────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_post(
            "https://www.rera.mp.gov.in/mprera/api/project/getProjectList",
            json_body={"pageNo": page, "pageSize": 100},
        )
        if not r:
            break
        try:
            data = r.json()
            projects = data.get("data") or data.get("projects") or []
        except Exception:
            break
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or proj.get("PinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("basicRate") or proj.get("rate"))
            if not rate or rate < 100:
                continue
            results.append({
                "pincode": pin,
                "state": "MADHYA PRADESH",
                "district": str(proj.get("district") or "").upper(),
                "source": "MP-RERA",
                "source_url": "https://www.rera.mp.gov.in",
                "rate_min_sqft": round(rate * 0.85),
                "rate_max_sqft": round(rate * 1.15),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"MP RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 10 — AP RERA
# ════════════════════════════════════════════════════════════════════
def scrape_ap_rera(max_pages=20):
    log.info("── AP RERA ──────────────────────────────────────────")
    results = []

    for page in range(1, max_pages + 1):
        r = safe_get(
            "https://rera.ap.gov.in/api/project/list",
            params={"page": page, "size": 100, "status": "registered"},
        )
        if not r:
            break
        try:
            data = r.json()
            projects = data.get("content") or data.get("data") or []
        except Exception:
            break
        if not projects:
            break

        for proj in projects:
            pin = str(proj.get("pinCode") or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            rate = _to_float(proj.get("saleRate") or proj.get("basicRate"))
            if not rate or rate < 100:
                continue
            results.append({
                "pincode": pin,
                "state": "ANDHRA PRADESH",
                "district": str(proj.get("district") or "").upper(),
                "source": "AP-RERA",
                "source_url": "https://rera.ap.gov.in",
                "rate_min_sqft": round(rate * 0.88),
                "rate_max_sqft": round(rate * 1.12),
                "property_type": "Residential",
                "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                "project_name": str(proj.get("projectName") or "")[:60],
            })
        time.sleep(0.4)

    log.info(f"AP RERA: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 11 — KAVERI IGR Karnataka (Guideline Values)
#  Endpoint: kaveri.karnataka.gov.in market value
# ════════════════════════════════════════════════════════════════════
def scrape_kaveri_igr():
    log.info("── KAVERI IGR (Karnataka Circle Rates) ─────────────")
    results = []

    # KAVERI exposes a market value lookup by village/survey
    # We iterate district-wise for Karnataka
    districts_url = "https://kaveri.karnataka.gov.in/MobileService/api/District/GetDistrictList"
    r = safe_get(districts_url)
    if not r:
        log.warning("KAVERI: districts endpoint unreachable")
        return results

    try:
        districts = r.json()
    except Exception:
        return results

    # For each district, get villages and their guideline values
    for dist in districts[:20]:  # Limit to first 20 districts
        dist_id = dist.get("DistrictCode") or dist.get("id")
        dist_name = dist.get("DistrictName") or dist.get("name", "")

        villages_url = "https://kaveri.karnataka.gov.in/MobileService/api/Village/GetVillageList"
        rv = safe_get(villages_url, params={"districtCode": dist_id})
        if not rv:
            continue

        try:
            villages = rv.json()
        except Exception:
            continue

        for village in villages[:10]:  # Sample first 10 villages per district
            v_id = village.get("VillageCode") or village.get("id")

            gv_url = "https://kaveri.karnataka.gov.in/MobileService/api/GuidelineValue/GetGVDetails"
            rg = safe_get(gv_url, params={"villageCode": v_id})
            if not rg:
                continue

            try:
                gv_data = rg.json()
            except Exception:
                continue

            for item in (gv_data if isinstance(gv_data, list) else [gv_data]):
                pin = str(item.get("Pincode") or item.get("pinCode") or "").strip()
                if len(pin) != 6 or not pin.isdigit():
                    continue
                rate = _to_float(item.get("LandRate") or item.get("GVRate") or
                                 item.get("rate"))
                if not rate or rate < 50:
                    continue
                # Convert from ₹/sqm to ₹/sqft if needed
                unit = str(item.get("RateUnit") or "sqm").lower()
                if "sqm" in unit:
                    rate = rate / 10.764
                results.append({
                    "pincode": pin,
                    "state": "KARNATAKA",
                    "district": dist_name.upper(),
                    "source": "KAVERI-IGR",
                    "source_url": "https://kaveri.karnataka.gov.in",
                    "rate_min_sqft": round(rate),
                    "rate_max_sqft": round(rate * 1.05),
                    "property_type": "Residential",
                    "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                    "project_name": "",
                    "is_guideline": True,
                })
            time.sleep(0.3)

    log.info(f"KAVERI IGR: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  SCRAPER 12 — TNREGINET (Tamil Nadu Guideline Values)
# ════════════════════════════════════════════════════════════════════
def scrape_tnreginet():
    log.info("── TNREGINET (Tamil Nadu GV) ────────────────────────")
    results = []

    # TNREGINET exposes guideline value by street/zone
    gv_url = "https://tnreginet.gov.in/portal/webHP?requestType=PageRequest&actionVal=guidelineSearch&screenId=53&langId=4"
    r = safe_get(gv_url)
    if not r:
        log.warning("TNREGINET: portal unreachable")
        return results

    # Try the AJAX search endpoint
    ajax_url = "https://tnreginet.gov.in/portal/AppController"
    payload = {
        "requestType": "AjaxRequest",
        "actionVal": "getGuidelineDetails",
        "districtId": "1",
        "pageNo": "1",
    }
    # Iterate over all TN districts (38 districts)
    for dist_id in range(1, 39):
        payload["districtId"] = str(dist_id)
        r2 = safe_post(ajax_url, data=payload)
        if not r2:
            continue
        try:
            data = r2.json()
            items = data.get("data") or data.get("guidelineList") or []
            for item in items:
                pin = str(item.get("pincode") or item.get("PinCode") or "").strip()
                if len(pin) != 6 or not pin.isdigit():
                    continue
                rate = _to_float(item.get("guidelineValue") or item.get("rate"))
                if not rate or rate < 50:
                    continue
                if rate > 10000:  # Likely in ₹/sqm
                    rate = rate / 10.764
                results.append({
                    "pincode": pin,
                    "state": "TAMIL NADU",
                    "district": str(item.get("district") or "").upper(),
                    "source": "TNREGINET",
                    "source_url": "https://tnreginet.gov.in",
                    "rate_min_sqft": round(rate),
                    "rate_max_sqft": round(rate * 1.05),
                    "property_type": "Residential",
                    "scraped_date": datetime.now().strftime("%Y-%m-%d"),
                    "project_name": "",
                    "is_guideline": True,
                })
        except Exception:
            pass
        time.sleep(0.3)

    log.info(f"TNREGINET: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════════
#  AGGREGATOR — Combine + deduplicate + compute pincode-level stats
# ════════════════════════════════════════════════════════════════════
def aggregate_rates(all_records: list) -> dict:
    """
    Group by pincode, compute:
    - median market rate (₹/sqft)
    - median guideline value (₹/sqft) — from IGR sources
    - confidence score (based on # data points)
    - source list
    """
    from statistics import median, stdev

    log.info(f"\nAggregating {len(all_records)} total records…")

    # Separate market (RERA) from guideline (IGR) records
    pincode_market = defaultdict(list)
    pincode_gv = defaultdict(list)
    pincode_meta = {}

    for rec in all_records:
        pin = rec["pincode"]
        pincode_meta[pin] = {
            "state": rec["state"],
            "district": rec["district"],
        }

        lo = rec.get("rate_min_sqft")
        hi = rec.get("rate_max_sqft")
        mid = None
        if lo and hi:
            mid = (lo + hi) / 2
        elif lo:
            mid = lo
        elif hi:
            mid = hi

        if not mid or mid < 50:
            continue

        if rec.get("is_guideline"):
            pincode_gv[pin].append(mid)
        else:
            pincode_market[pin].append(mid)

    result = {}
    all_pins = set(pincode_market.keys()) | set(pincode_gv.keys())
    log.info(f"Unique pincodes with data: {len(all_pins)}")

    for pin in all_pins:
        mkt_rates = pincode_market.get(pin, [])
        gv_rates = pincode_gv.get(pin, [])
        meta = pincode_meta.get(pin, {})

        # Remove outliers (>3σ from mean)
        mkt_clean = _remove_outliers(mkt_rates)
        gv_clean = _remove_outliers(gv_rates)

        mkt_median = round(median(mkt_clean)) if mkt_clean else None
        gv_median = round(median(gv_clean)) if gv_clean else None

        # Confidence: 1 record=LOW, 2-4=MEDIUM, 5+=HIGH
        n = len(mkt_clean) + len(gv_clean)
        confidence = "HIGH" if n >= 5 else "MEDIUM" if n >= 2 else "LOW"

        # Compute range
        all_combined = mkt_clean + gv_clean
        rate_lo = round(min(all_combined) * 0.9) if all_combined else None
        rate_hi = round(max(all_combined) * 1.1) if all_combined else None

        result[pin] = {
            "state": meta.get("state", ""),
            "district": meta.get("district", ""),
            "market_rate_sqft": mkt_median,
            "guideline_value_sqft": gv_median,
            "rate_range_lo": rate_lo,
            "rate_range_hi": rate_hi,
            "data_points": n,
            "confidence": confidence,
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "sources": list(set(
                r["source"] for r in all_records if r["pincode"] == pin
            )),
        }

    log.info(f"Aggregated: {len(result)} pincodes with rate data")
    return result


def _remove_outliers(values: list, z=2.5) -> list:
    if len(values) < 4:
        return values
    try:
        from statistics import mean, stdev
        m, s = mean(values), stdev(values)
        return [v for v in values if abs(v - m) <= z * s]
    except Exception:
        return values


def _to_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("₹", "").strip())
        return f if f > 0 else None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════
def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║        UGRO Capital — RERA & IGR Land Rate Scraper          ║
║        Producing: pincode_rates.json                        ║
╚══════════════════════════════════════════════════════════════╝
    """)

    all_records = []

    scrapers = [
        ("MahaRERA",        scrape_maharera),
        ("UP RERA",         scrape_up_rera),
        ("Karnataka RERA",  scrape_karnataka_rera),
        ("TNRERA",          scrape_tnrera),
        ("Gujarat RERA",    scrape_gujarat_rera),
        ("Rajasthan RERA",  scrape_rajasthan_rera),
        ("Telangana IGRS",  scrape_telangana_igrs),
        ("Haryana RERA",    scrape_haryana_rera),
        ("MP RERA",         scrape_mp_rera),
        ("AP RERA",         scrape_ap_rera),
        ("KAVERI IGR",      scrape_kaveri_igr),
        ("TNREGINET",       scrape_tnreginet),
    ]

    for name, fn in scrapers:
        print(f"\n{'─'*60}")
        print(f"  Scraping: {name}")
        print(f"{'─'*60}")
        try:
            records = fn()
            all_records.extend(records)
            print(f"  ✓ {name}: {len(records)} records collected")
        except Exception as e:
            log.error(f"{name} failed: {e}")
            print(f"  ✗ {name}: Failed — {e}")

    # Aggregate into pincode-level stats
    print(f"\n{'═'*60}")
    print(f"  Total raw records: {len(all_records)}")
    print(f"{'═'*60}\n")

    pincode_rates = aggregate_rates(all_records)

    # Save output
    output = {
        "metadata": {
            "generated": datetime.now().isoformat(),
            "total_pincodes": len(pincode_rates),
            "total_records": len(all_records),
            "sources": list(set(r["source"] for r in all_records)),
            "version": "1.0",
            "description": "UGRO Capital pincode land rates from RERA + IGR sources",
        },
        "rates": pincode_rates,
    }

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pincode_rates.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    file_size = os.path.getsize(out_path) / 1024
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SCRAPING COMPLETE                                          ║
╠══════════════════════════════════════════════════════════════╣
║  Pincodes with data : {len(pincode_rates):<6}                            ║
║  Total raw records  : {len(all_records):<6}                            ║
║  Output file        : pincode_rates.json ({file_size:.0f} KB)         ║
╠══════════════════════════════════════════════════════════════╣
║  NEXT STEP:                                                  ║
║  Place pincode_rates.json in the same folder as             ║
║  ugro_collateral_platform.html and open the platform.       ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # Print confidence breakdown
    c_counts = defaultdict(int)
    for v in pincode_rates.values():
        c_counts[v["confidence"]] += 1
    print("Confidence breakdown:")
    for level, count in sorted(c_counts.items()):
        print(f"  {level:8s}: {count} pincodes")


if __name__ == "__main__":
    main()
