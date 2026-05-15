#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  UGRO Capital — Land Rate Scraper v3.0
  ─────────────────────────────────────────────────────────────────
  MARKET VALUE  → RERA portals (declared project rates)
  GUIDELINE VALUE → SRO / IGR portals (govt circle/stamp rates)
  ─────────────────────────────────────────────────────────────────
  Sources:
    Market Value  : MahaRERA, UP RERA, Karnataka RERA, TNRERA,
                    Gujarat RERA, Rajasthan RERA, Haryana RERA,
                    MP RERA, AP RERA, 99acres, MagicBricks, Housing.com
    Guideline Value: IGR Maharashtra (Ready Reckoner), KAVERI Karnataka,
                    TNREGINET Tamil Nadu, IGRS Telangana, IGRS AP (VLRD),
                    IGRSUP Uttar Pradesh, IGR Rajasthan (DLC Rates),
                    GARVI Gujarat (Jantri), WB Registration,
                    Jamabandi Haryana, Delhi DORIS, MP IGR,
                    Kerala Registration, Bihar Bhumijankari

  Output  : pincode_rates.json
  Schedule: Daily auto-run via Windows Task Scheduler

  SETUP:
    pip install requests beautifulsoup4 tqdm playwright schedule
    python -m playwright install chromium

  RUN:
    python ugro_rera_scraper.py

  DAILY AUTO-RUN:
    python ugro_rera_scraper.py --setup-scheduler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests, json, time, os, sys, logging, argparse, subprocess
from datetime import datetime
from collections import defaultdict
from statistics import median
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

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

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "pincode_rates.json")
LOG_FILE    = os.path.join(SCRIPT_DIR, "ugro_scraper.log")

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

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-IN,en;q=0.9",
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
            r = SESSION.post(url, json=json_body, data=data, headers=headers, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            log.warning(f"HTTP {r.status_code} POST — {url[:60]}")
        except Exception as e:
            log.warning(f"POST attempt {attempt+1}: {str(e)[:60]}")
        time.sleep([2, 5, 10][attempt])
    return None

def to_float(val) -> Optional[float]:
    if val is None: return None
    try:
        f = float(str(val).replace(",","").replace("₹","").strip())
        return f if f > 0 else None
    except: return None

def make_record(pin, state, district, source, url,
                rate_min, rate_max, is_guideline=False, prop_type="Residential"):
    return {
        "pincode": str(pin), "state": state,
        "district": str(district).upper(), "source": source,
        "source_url": url,
        "rate_min_sqft": round(rate_min) if rate_min else None,
        "rate_max_sqft": round(rate_max) if rate_max else None,
        "property_type": prop_type,
        "is_guideline": is_guideline,
        "scraped_date": datetime.now().strftime("%Y-%m-%d"),
    }

def sqm_to_sqft(v): return v / 10.764 if v else None
def sqyard_to_sqft(v): return v / 1.196 if v else None
def cent_to_sqft(v): return v / 4356 if v else None   # 1 cent = 435.6 sqft, rate/cent → rate/sqft

# ════════════════════════════════════════════════════════════════
#  SECTION A — MARKET VALUE SCRAPERS (RERA Portals)
#  Purpose: Capture declared project sale rates
# ════════════════════════════════════════════════════════════════

def _parse_rera_generic(data, state, pin_keys, rate_keys,
                        dist_key="district", source="", url=""):
    records, projects = [], None
    for k in ["Projects","projects","data","content","ProjectList","projectList"]:
        if isinstance(data.get(k), list):
            projects = data[k]; break
    if not projects: return [], 0
    total = data.get("TotalCount") or data.get("total") or 0
    for proj in projects:
        pin = next((str(proj.get(k,"")).strip() for k in pin_keys
                    if len(str(proj.get(k,"")).strip())==6
                    and str(proj.get(k,"")).strip().isdigit()), None)
        if not pin: continue
        rate = next((to_float(proj.get(k)) for k in rate_keys
                     if to_float(proj.get(k)) and to_float(proj.get(k))>50), None)
        if not rate: continue
        dist = str(proj.get(dist_key) or proj.get("District") or "").upper()
        records.append(make_record(pin, state, dist, source, url,
                                   rate*0.88, rate*1.12, is_guideline=False))
    return records, int(total)

def _scrape_rera(name, url, method, payload_fn, state, parse_fn, max_pages=80):
    results = []
    for page in range(1, max_pages+1):
        payload = payload_fn(page)
        r = safe_post(url, json_body=payload) if method=="POST" \
            else safe_get(url, params=payload)
        if not r: break
        try: data = r.json()
        except: break
        recs, total = parse_fn(data, state)
        if not recs: break
        results.extend(recs)
        log.info(f"  {name} p{page}: {len(recs)} → {len(results)}")
        time.sleep(0.5)
        if total and page*100 >= total: break
    log.info(f"{name} MARKET: {len(results)} records")
    return results

def scrape_maharera_market(mp=100):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["PinCode","Pincode"],["CarpetAreaRateMin","MinRate","basicRate","rate"],
        source="MahaRERA",url="https://maharerait.mahaonline.gov.in")
    return _scrape_rera("MahaRERA",
        "https://maharerait.mahaonline.gov.in/api/Project/GetProjectList",
        "POST",lambda p:{"PageNo":p,"PageSize":100,"ProjectStatus":"1"},
        "MAHARASHTRA",parse,mp)

def scrape_up_rera_market(mp=80):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode","PinCode"],["costOfProject","rate","unitRate","basicRate"],
        source="UP-RERA",url="https://www.up-rera.in")
    return _scrape_rera("UP RERA",
        "https://www.up-rera.in/api/project/getList",
        "GET",lambda p:{"pageNo":p,"pageSize":100,"status":"approved"},
        "UTTAR PRADESH",parse,mp)

def scrape_karnataka_rera_market(mp=60):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode","pin"],["unitRate","rate","basicRate"],
        source="Karnataka-RERA",url="https://rera.karnataka.gov.in")
    return _scrape_rera("Karnataka RERA",
        "https://rera.karnataka.gov.in/api/project/list",
        "GET",lambda p:{"page":p,"size":100},
        "KARNATAKA",parse,mp)

def scrape_tnrera_market(mp=40):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode","PinCode"],["basicRate","rate","unitRate"],
        source="TNRERA",url="https://www.tnrera.in")
    return _scrape_rera("TNRERA",
        "https://www.tnrera.in/api/projects/list",
        "POST",lambda p:{"pageNo":p,"pageSize":100,"status":"REGISTERED"},
        "TAMIL NADU",parse,mp)

def scrape_gujarat_rera_market(mp=40):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pincode","PinCode"],["basicSalePrice","rate"],
        dist_key="districtName",
        source="GujRERA",url="https://gujrera.gujarat.gov.in")
    return _scrape_rera("Gujarat RERA",
        "https://gujrera.gujarat.gov.in/online/project/getProjectDetails",
        "GET",lambda p:{"pageNo":p,"pageSize":100,"status":"A"},
        "GUJARAT",parse,mp)

def scrape_rajasthan_rera_market(mp=30):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode"],["basicRate","unitRate"],
        source="RRERA",url="https://rera.rajasthan.gov.in")
    return _scrape_rera("Rajasthan RERA",
        "https://rera.rajasthan.gov.in/api/project/list",
        "GET",lambda p:{"page":p,"size":100},
        "RAJASTHAN",parse,mp)

def scrape_haryana_rera_market(mp=30):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode"],["basicRate","saleRate"],
        source="HRERA",url="https://hrera.gov.in")
    return _scrape_rera("Haryana RERA",
        "https://hrera.gov.in/api/project/list",
        "GET",lambda p:{"pageNo":p,"pageSize":100,"status":"REGISTERED"},
        "HARYANA",parse,mp)

def scrape_mp_rera_market(mp=30):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode"],["basicRate","rate"],
        source="MP-RERA",url="https://www.rera.mp.gov.in")
    return _scrape_rera("MP RERA",
        "https://www.rera.mp.gov.in/mprera/api/project/getProjectList",
        "POST",lambda p:{"pageNo":p,"pageSize":100},
        "MADHYA PRADESH",parse,mp)

def scrape_ap_rera_market(mp=30):
    def parse(d,s): return _parse_rera_generic(d,s,
        ["pinCode"],["saleRate","basicRate"],
        source="AP-RERA",url="https://rera.ap.gov.in")
    return _scrape_rera("AP RERA",
        "https://rera.ap.gov.in/api/project/list",
        "GET",lambda p:{"page":p,"size":100,"status":"registered"},
        "ANDHRA PRADESH",parse,mp)

# ════════════════════════════════════════════════════════════════
#  SECTION B — GUIDELINE VALUE SCRAPERS (SRO / IGR Portals)
#  Purpose: Capture government-notified circle/stamp rates
#  NOTE: These are the ONLY correct sources for guideline values
# ════════════════════════════════════════════════════════════════

def scrape_maharashtra_igr_gv():
    """
    IGR Maharashtra — Ready Reckoner Rates
    Source: freesearchigrservice.maharashtra.gov.in
    Rates: ₹/sqm land → convert to ₹/sqft
    Updated: Annually on 1st April
    """
    log.info("── IGR Maharashtra (Ready Reckoner GV) ──────────────")
    results = []

    # Step 1: Get districts
    r = safe_get("https://freesearchigrservice.maharashtra.gov.in/api/RR/GetDistrictList",
                 headers={"Referer": "https://igrmaharashtra.gov.in"})
    if not r:
        log.warning("  IGR Maharashtra: district list unavailable")
        return results

    try:
        districts = r.json()
    except:
        return results

    for dist in (districts if isinstance(districts, list) else []):
        dist_id   = dist.get("districtId") or dist.get("id")
        dist_name = dist.get("districtName") or dist.get("name","")

        # Step 2: Get talukas
        r2 = safe_get(
            "https://freesearchigrservice.maharashtra.gov.in/api/RR/GetTalukaList",
            params={"districtId": dist_id},
            headers={"Referer":"https://igrmaharashtra.gov.in"})
        if not r2: continue
        try: talukas = r2.json()
        except: continue

        for taluka in (talukas if isinstance(talukas, list) else [])[:5]:
            taluka_id = taluka.get("talukaId") or taluka.get("id")

            # Step 3: Get RR rate details
            r3 = safe_post(
                "https://freesearchigrservice.maharashtra.gov.in/api/RR/GetRRRateDetails",
                json_body={"districtId": dist_id, "talukaId": taluka_id,
                           "year": datetime.now().year},
                headers={"Referer":"https://igrmaharashtra.gov.in",
                         "Content-Type":"application/json"})
            if not r3: continue
            try: rate_data = r3.json()
            except: continue

            items = rate_data if isinstance(rate_data,list) else rate_data.get("data",[])
            for item in items:
                pin = str(item.get("pinCode") or item.get("Pincode") or "").strip()
                if len(pin) != 6 or not pin.isdigit(): continue
                # Maharashtra RR: OpenLand rate in ₹/sqm
                rate_sqm = to_float(item.get("openLandRate") or
                                    item.get("LandRate") or item.get("rate"))
                if not rate_sqm or rate_sqm < 100: continue
                rate_sqft = sqm_to_sqft(rate_sqm)
                results.append(make_record(pin, "MAHARASHTRA", dist_name,
                    "IGR-Maharashtra-RR","https://igrmaharashtra.gov.in",
                    rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            time.sleep(0.3)

    log.info(f"IGR Maharashtra GV: {len(results)} records")
    return results


def scrape_kaveri_karnataka_gv():
    """
    KAVERI IGR Karnataka — Guideline Values
    Source: kaveri.karnataka.gov.in
    Rates: ₹/sqm or ₹/sqft depending on property type
    Updated: Annually
    """
    log.info("── KAVERI Karnataka (Guideline Values GV) ───────────")
    results = []

    r = safe_get("https://kaveri.karnataka.gov.in/MobileService/api/District/GetDistrictList")
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        dist_id   = dist.get("DistrictCode") or dist.get("id")
        dist_name = dist.get("DistrictName") or dist.get("name","")

        rv = safe_get("https://kaveri.karnataka.gov.in/MobileService/api/Village/GetVillageList",
                      params={"districtCode": dist_id})
        if not rv: continue
        try: villages = rv.json()
        except: continue

        for village in (villages if isinstance(villages,list) else [])[:8]:
            v_id = village.get("VillageCode") or village.get("id")
            rg = safe_get(
                "https://kaveri.karnataka.gov.in/MobileService/api/GuidelineValue/GetGVDetails",
                params={"villageCode": v_id})
            if not rg: continue
            try: gv_data = rg.json()
            except: continue

            for item in (gv_data if isinstance(gv_data,list) else [gv_data]):
                pin = str(item.get("Pincode") or item.get("pinCode","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("LandRate") or item.get("GVRate") or item.get("rate"))
                if not rate or rate<50: continue
                unit = str(item.get("RateUnit") or "sqm").lower()
                if "sqm" in unit or "sq.m" in unit:
                    rate = sqm_to_sqft(rate)
                results.append(make_record(pin,"KARNATAKA",dist_name,
                    "KAVERI-IGR","https://kaveri.karnataka.gov.in",
                    rate*0.95, rate*1.05, is_guideline=True))
            time.sleep(0.3)

    log.info(f"KAVERI Karnataka GV: {len(results)} records")
    return results


def scrape_tnreginet_gv():
    """
    TNREGINET Tamil Nadu — Guideline Values
    Source: tnreginet.gov.in
    Rates: ₹/sqft (residential land)
    Updated: Annually
    """
    log.info("── TNREGINET Tamil Nadu (Guideline Values GV) ───────")
    results = []

    for dist_id in range(1, 39):  # 38 TN districts
        r = safe_post(
            "https://tnreginet.gov.in/portal/AppController",
            data={
                "requestType": "AjaxRequest",
                "actionVal":   "getGuidelineDetails",
                "districtId":  str(dist_id),
                "pageNo":      "1",
            },
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer":"https://tnreginet.gov.in"})
        if not r: continue
        try:
            data  = r.json()
            items = data.get("data") or data.get("guidelineList") or []
            for item in items:
                pin  = str(item.get("pincode") or item.get("PinCode","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("guidelineValue") or item.get("rate"))
                if not rate or rate < 50: continue
                # If suspiciously high, likely ₹/sqm — convert
                if rate > 50000:
                    rate = sqm_to_sqft(rate)
                results.append(make_record(pin,"TAMIL NADU",
                    str(item.get("district","")).upper(),
                    "TNREGINET","https://tnreginet.gov.in",
                    rate*0.95, rate*1.05, is_guideline=True))
        except: pass
        time.sleep(0.3)

    log.info(f"TNREGINET TN GV: {len(results)} records")
    return results


def scrape_igrs_telangana_gv():
    """
    IGRS Telangana — Market Value Assistance (Govt assessed rates)
    Source: igrs.telangana.gov.in
    Rates: ₹/sqyard → convert to ₹/sqft
    Updated: Periodically by SRO
    """
    log.info("── IGRS Telangana (Market Value Assistance GV) ──────")
    results = []

    # Get district list first
    r = safe_get("https://igrs.telangana.gov.in/igrs/DistrictDetails.do",
                 headers={"Referer":"https://igrs.telangana.gov.in"})
    if not r:
        # Fallback: use known Telangana district codes
        ts_districts = [
            ("1","Hyderabad"),("2","Rangareddy"),("3","Medchal"),
            ("4","Sangareddy"),("5","Nizamabad"),("6","Karimnagar"),
            ("7","Warangal"),("8","Khammam"),("9","Nalgonda"),("10","Mahabubnagar"),
        ]
    else:
        try:
            data = r.json()
            ts_districts = [(str(d.get("districtCode")), d.get("districtName",""))
                            for d in (data if isinstance(data,list) else [])]
        except:
            ts_districts = []

    for dist_code, dist_name in ts_districts:
        # Get mandals
        rm = safe_post(
            "https://igrs.telangana.gov.in/igrs/MandalDetails.do",
            data={"districtCode": dist_code},
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer":"https://igrs.telangana.gov.in"})
        if not rm: continue
        try: mandals = rm.json()
        except: continue

        for mandal in (mandals if isinstance(mandals,list) else [])[:5]:
            m_code = mandal.get("mandalCode") or mandal.get("id")

            rv = safe_post(
                "https://igrs.telangana.gov.in/igrs/MarketValue.do",
                data={"districtCode":dist_code,"mandalCode":str(m_code),
                      "propertyType":"1"},
                headers={"X-Requested-With":"XMLHttpRequest",
                         "Referer":"https://igrs.telangana.gov.in"})
            if not rv: continue
            try:
                mv_data = rv.json()
                items   = mv_data if isinstance(mv_data,list) else mv_data.get("data",[])
                for item in items:
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqyard = to_float(item.get("landRate") or item.get("rate"))
                    if not rate_sqyard or rate_sqyard<50: continue
                    rate_sqft = sqyard_to_sqft(rate_sqyard)
                    results.append(make_record(pin,"TELANGANA",dist_name,
                        "IGRS-Telangana-MVA","https://igrs.telangana.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"IGRS Telangana GV: {len(results)} records")
    return results


def scrape_igrs_ap_gv():
    """
    IGRS AP — VLRD (Village Level Registration Data)
    Source: registration.ap.gov.in
    Rates: ₹/sqyard → convert to ₹/sqft
    """
    log.info("── IGRS AP (VLRD Guideline Values GV) ───────────────")
    results = []

    r = safe_get("https://registration.ap.gov.in/vlrd/getDistrictList",
                 headers={"Referer":"https://registration.ap.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        dist_code = dist.get("districtCode") or dist.get("id")
        dist_name = dist.get("districtName") or ""

        rm = safe_post(
            "https://registration.ap.gov.in/vlrd/getMandalList",
            json_body={"districtCode": dist_code},
            headers={"Referer":"https://registration.ap.gov.in"})
        if not rm: continue
        try: mandals = rm.json()
        except: continue

        for mandal in (mandals if isinstance(mandals,list) else [])[:5]:
            m_code = mandal.get("mandalCode") or mandal.get("id")
            rv = safe_post(
                "https://registration.ap.gov.in/vlrd/getValuationDetails",
                json_body={"districtCode":dist_code,"mandalCode":str(m_code),
                           "propertyType":"Residential"},
                headers={"Referer":"https://registration.ap.gov.in"})
            if not rv: continue
            try:
                items = rv.json()
                if isinstance(items, dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqyard = to_float(item.get("landRate") or item.get("rate"))
                    if not rate_sqyard or rate_sqyard<50: continue
                    rate_sqft = sqyard_to_sqft(rate_sqyard)
                    results.append(make_record(pin,"ANDHRA PRADESH",dist_name,
                        "IGRS-AP-VLRD","https://registration.ap.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"IGRS AP VLRD GV: {len(results)} records")
    return results


def scrape_igrsup_gv():
    """
    IGRSUP — Circle Rates (Uttar Pradesh)
    Source: igrsup.gov.in
    Rates: ₹/sqm → convert to ₹/sqft
    Updated: Annually
    """
    log.info("── IGRSUP UP (Circle Rates GV) ──────────────────────")
    results = []

    for dist_id in range(1, 76):  # 75 UP districts
        r_tehsil = safe_post(
            "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
            data={"stampDutyCalcBean.districtId": str(dist_id),
                  "actionType": "getTehsilList"},
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer":"https://igrsup.gov.in/igrsup/"})
        if not r_tehsil: continue
        try: tehsils = r_tehsil.json()
        except: continue

        for tehsil in (tehsils if isinstance(tehsils,list) else []):
            t_id = tehsil.get("id") or tehsil.get("value")
            if not t_id: continue

            r_rate = safe_post(
                "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
                data={"stampDutyCalcBean.districtId": str(dist_id),
                      "stampDutyCalcBean.tehsilId":   str(t_id),
                      "stampDutyCalcBean.propertyType": "1",
                      "actionType": "getCircleRate"},
                headers={"X-Requested-With":"XMLHttpRequest",
                         "Referer":"https://igrsup.gov.in/igrsup/"})
            if not r_rate: continue
            try:
                rd = r_rate.json()
                rate_sqm = to_float(rd.get("circleRate") or rd.get("rate") or
                                    rd.get("residentialRate"))
                if not rate_sqm or rate_sqm < 100: continue
                rate_sqft = sqm_to_sqft(rate_sqm)
                dist_name = str(tehsil.get("districtName") or "").upper()
                # Approximate pincode from tehsil (best effort)
                pin = str(tehsil.get("pinCode") or "").strip()
                if len(pin)==6 and pin.isdigit():
                    results.append(make_record(pin,"UTTAR PRADESH",dist_name,
                        "IGRSUP-CircleRate","https://igrsup.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"IGRSUP UP GV: {len(results)} records")
    return results


def scrape_garvi_gujarat_gv():
    """
    GARVI Gujarat — Jantri Rates
    Source: garvi.gujarat.gov.in
    Rates: ₹/sqm → convert to ₹/sqft
    Updated: Periodically (last major revision 2023)
    """
    log.info("── GARVI Gujarat (Jantri Rates GV) ──────────────────")
    results = []

    r = safe_get("https://garvi.gujarat.gov.in/grvweb/jantri/getDistrictList",
                 headers={"Referer":"https://garvi.gujarat.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_code = dist.get("districtCode") or dist.get("id")
        d_name = dist.get("districtName") or ""

        rt = safe_get("https://garvi.gujarat.gov.in/grvweb/jantri/getTalukaList",
                      params={"districtCode": d_code},
                      headers={"Referer":"https://garvi.gujarat.gov.in"})
        if not rt: continue
        try: talukas = rt.json()
        except: continue

        for taluka in (talukas if isinstance(talukas,list) else [])[:5]:
            t_code = taluka.get("talukaCode") or taluka.get("id")

            rv = safe_post(
                "https://garvi.gujarat.gov.in/grvweb/jantri/getJantriRate",
                json_body={"districtCode":d_code,"talukaCode":str(t_code),
                           "propertyType":"RESIDENTIAL_LAND"},
                headers={"Referer":"https://garvi.gujarat.gov.in",
                         "Content-Type":"application/json"})
            if not rv: continue
            try:
                items = rv.json()
                if isinstance(items,dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqm = to_float(item.get("jantriRate") or item.get("rate"))
                    if not rate_sqm or rate_sqm<100: continue
                    rate_sqft = sqm_to_sqft(rate_sqm)
                    results.append(make_record(pin,"GUJARAT",d_name,
                        "GARVI-Jantri","https://garvi.gujarat.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"GARVI Gujarat GV: {len(results)} records")
    return results


def scrape_rajasthan_dlc_gv():
    """
    IGR Rajasthan — DLC (District Level Committee) Rates
    Source: epanjiyan.rajasthan.gov.in
    Rates: ₹/sqm → convert to ₹/sqft
    Updated: Annually
    """
    log.info("── IGR Rajasthan (DLC Rates GV) ─────────────────────")
    results = []

    r = safe_get("https://epanjiyan.rajasthan.gov.in/api/district/list",
                 headers={"Referer":"https://igrs.rajasthan.gov.in"})
    if not r:
        r = safe_get("https://igrs.rajasthan.gov.in/api/district/list")
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("districtId") or dist.get("id")
        d_name = dist.get("districtName") or ""

        rv = safe_get(
            "https://epanjiyan.rajasthan.gov.in/api/dlcrate/getByDistrict",
            params={"districtId": d_id, "propertyType": "residential"},
            headers={"Referer":"https://epanjiyan.rajasthan.gov.in"})
        if not rv: continue
        try:
            items = rv.json()
            if isinstance(items,dict): items = items.get("data",[])
            for item in (items if isinstance(items,list) else []):
                pin = str(item.get("pinCode") or item.get("pin","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate_sqm = to_float(item.get("dlcRate") or item.get("rate"))
                if not rate_sqm or rate_sqm<50: continue
                rate_sqft = sqm_to_sqft(rate_sqm)
                results.append(make_record(pin,"RAJASTHAN",d_name,
                    "IGR-Rajasthan-DLC","https://igrs.rajasthan.gov.in",
                    rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
        except: pass
        time.sleep(0.3)

    log.info(f"IGR Rajasthan DLC GV: {len(results)} records")
    return results


def scrape_haryana_dlc_gv():
    """
    Jamabandi Haryana — Collector/DLC Rates
    Source: jamabandi.nic.in
    Rates: ₹/sqyard → convert to ₹/sqft
    """
    log.info("── Jamabandi Haryana (DLC Rates GV) ─────────────────")
    results = []

    r = safe_get("https://jamabandi.nic.in/land%20records/CollectorRates",
                 headers={"Referer":"https://jamabandi.nic.in"})
    if not r: return results

    if HAS_BS4:
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("table tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 4:
                pin  = cells[-1].get_text(strip=True)
                rate_txt = cells[2].get_text(strip=True)
                if len(pin)==6 and pin.isdigit():
                    rate_sqyard = to_float(rate_txt)
                    if rate_sqyard and rate_sqyard > 10:
                        rate_sqft = sqyard_to_sqft(rate_sqyard)
                        results.append(make_record(pin,"HARYANA","",
                            "Jamabandi-DLC","https://jamabandi.nic.in",
                            rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))

    log.info(f"Jamabandi Haryana GV: {len(results)} records")
    return results


def scrape_kerala_fair_value_gv():
    """
    Kerala Registration — Fair Value of Land
    Source: keralaregistration.gov.in
    Rates: ₹/cent → convert to ₹/sqft (1 cent = 435.6 sqft)
    Updated: Periodically by SRO
    """
    log.info("── Kerala Registration (Fair Value GV) ──────────────")
    results = []

    r = safe_get("https://keralaregistration.gov.in/pearlapi/Districts",
                 headers={"Referer":"https://keralaregistration.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("districtId") or dist.get("id")
        d_name = dist.get("districtName") or ""

        rt = safe_get("https://keralaregistration.gov.in/pearlapi/Taluks",
                      params={"districtId": d_id},
                      headers={"Referer":"https://keralaregistration.gov.in"})
        if not rt: continue
        try: taluks = rt.json()
        except: continue

        for taluk in (taluks if isinstance(taluks,list) else [])[:5]:
            t_id = taluk.get("talukId") or taluk.get("id")

            rv = safe_post(
                "https://keralaregistration.gov.in/pearlapi/FairValue",
                json_body={"districtId":d_id,"talukId":str(t_id),"propertyType":"L"},
                headers={"Referer":"https://keralaregistration.gov.in",
                         "Content-Type":"application/json"})
            if not rv: continue
            try:
                items = rv.json()
                if isinstance(items,dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    # Kerala rates in ₹/cent (1 cent = 435.6 sqft)
                    rate_cent = to_float(item.get("fairValue") or item.get("rate"))
                    if not rate_cent or rate_cent < 1000: continue
                    rate_sqft = rate_cent / 435.6
                    results.append(make_record(pin,"KERALA",d_name,
                        "Kerala-FairValue","https://keralaregistration.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"Kerala Fair Value GV: {len(results)} records")
    return results


def scrape_wb_circle_rate_gv():
    """
    WB Registration — Circle Rates
    Source: wbregistration.gov.in
    Rates: ₹/decimal (1 decimal = 435.6 sqft) or ₹/sqft
    """
    log.info("── WB Registration (Circle Rates GV) ────────────────")
    results = []

    for dist_id in range(1, 24):  # 23 WB districts
        r = safe_post(
            "https://wbregistration.gov.in/api/circleRate/getByDistrict",
            json_body={"districtId": dist_id, "propertyType": "land"},
            headers={"Referer":"https://wbregistration.gov.in",
                     "Content-Type":"application/json"})
        if not r: continue
        try:
            items = r.json()
            if isinstance(items,dict): items = items.get("data",[])
            for item in (items if isinstance(items,list) else []):
                pin = str(item.get("pinCode") or item.get("pin","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("circleRate") or item.get("rate"))
                if not rate or rate < 100: continue
                unit = str(item.get("unit") or "sqft").lower()
                if "sqm" in unit: rate = sqm_to_sqft(rate)
                elif "decimal" in unit or "deci" in unit: rate = rate / 435.6
                results.append(make_record(pin,"WEST BENGAL","",
                    "WB-CircleRate","https://wbregistration.gov.in",
                    rate*0.95, rate*1.05, is_guideline=True))
        except: pass
        time.sleep(0.3)

    log.info(f"WB Circle Rate GV: {len(results)} records")
    return results


def scrape_mp_igr_gv():
    """
    MP IGR — Guideline Values
    Source: mpigr.gov.in
    Rates: ₹/sqft (residential land)
    """
    log.info("── MP IGR (Guideline Values GV) ─────────────────────")
    results = []

    r = safe_get("https://mpigr.gov.in/api/district/list",
                 headers={"Referer":"https://mpigr.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("districtId") or dist.get("id")
        d_name = dist.get("districtName") or ""

        rv = safe_post(
            "https://mpigr.gov.in/api/guidelineValue/getByDistrict",
            json_body={"districtId": d_id, "propertyType": "Residential"},
            headers={"Referer":"https://mpigr.gov.in","Content-Type":"application/json"})
        if not rv: continue
        try:
            items = rv.json()
            if isinstance(items,dict): items = items.get("data",[])
            for item in (items if isinstance(items,list) else []):
                pin = str(item.get("pinCode") or item.get("pin","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("guidelineValue") or item.get("rate"))
                if not rate or rate<50: continue
                unit = str(item.get("unit") or "sqft").lower()
                if "sqm" in unit: rate = sqm_to_sqft(rate)
                results.append(make_record(pin,"MADHYA PRADESH",d_name,
                    "MP-IGR-GV","https://mpigr.gov.in",
                    rate*0.95, rate*1.05, is_guideline=True))
        except: pass
        time.sleep(0.3)

    log.info(f"MP IGR GV: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════
#  SECTION C — PROPERTY PORTALS (Market Value only)
# ════════════════════════════════════════════════════════════════

CITY_PINCODES = {
    "Mumbai":    ["400001","400002","400003","400004","400005","400010","400016","400018","400019","400020","400025"],
    "Delhi":     ["110001","110002","110003","110004","110005","110010","110015","110016","110017","110018","110020"],
    "Bengaluru": ["560001","560002","560003","560010","560011","560020","560030","560040","560050","560060","560070"],
    "Chennai":   ["600001","600002","600003","600004","600005","600010","600020","600030","600040","600050"],
    "Hyderabad": ["500001","500002","500003","500010","500016","500018","500032","500034","500050","500072"],
    "Pune":      ["411001","411002","411003","411004","411005","411010","411015","411020","411030","411040"],
    "Kolkata":   ["700001","700002","700003","700010","700015","700020","700025","700030","700040","700050"],
    "Ahmedabad": ["380001","380002","380004","380005","380006","380007","380013","380015","380019","380054"],
    "Jaipur":    ["302001","302002","302003","302004","302006","302012","302015","302017","302018","302019"],
    "Lucknow":   ["226001","226002","226003","226004","226005","226010","226012","226016","226017","226020"],
    "Noida":     ["201301","201302","201303","201304","201305","201306","201307","201308","201310","201313"],
    "Gurugram":  ["122001","122002","122003","122004","122005","122006","122007","122008","122009","122015"],
}
CITY_STATE = {
    "Mumbai":"MAHARASHTRA","Delhi":"DELHI","Bengaluru":"KARNATAKA",
    "Chennai":"TAMIL NADU","Hyderabad":"TELANGANA","Pune":"MAHARASHTRA",
    "Kolkata":"WEST BENGAL","Ahmedabad":"GUJARAT","Jaipur":"RAJASTHAN",
    "Lucknow":"UTTAR PRADESH","Noida":"UTTAR PRADESH","Gurugram":"HARYANA",
}

def scrape_property_portals_market():
    """99acres / MagicBricks / Housing.com — Market rates only."""
    log.info("── Property Portals (Market Value) ──────────────────")
    results = []
    for city, pincodes in CITY_PINCODES.items():
        state = CITY_STATE.get(city,"")
        for pin in pincodes:
            for url, rate_keys in [
                ("https://www.99acres.com/api/v1/locality/search",
                 ["avg_price_sqft","price_per_sqft","avgRate"]),
                ("https://www.magicbricks.com/mbsearch/ajax/localityDataSearch.html",
                 ["avgPricePerSqft","pricePerSqft","avg_price"]),
                ("https://housing.com/api/v2/locality/insights",
                 ["avg_price_per_sqft","price_per_sqft","avgRate","average_price"]),
            ]:
                r = safe_get(url, params={"pinCode" if "99" not in url else "q": pin},
                             headers={"X-Requested-With":"XMLHttpRequest",
                                      "Referer": url.split("/api")[0]})
                if not r: continue
                try:
                    d = r.json()
                    rate = next((to_float(d.get(k)) for k in rate_keys
                                 if to_float(d.get(k)) and to_float(d.get(k))>100), None)
                    if rate:
                        portal = "99acres" if "99acres" in url else \
                                 "MagicBricks" if "magic" in url else "Housing.com"
                        results.append(make_record(pin, state, city, portal, url,
                                                   rate*0.9, rate*1.1, is_guideline=False))
                        break
                except: pass
            time.sleep(0.4)
    log.info(f"Property Portals market: {len(results)} records")
    return results


# ════════════════════════════════════════════════════════════════
#  AGGREGATOR
# ════════════════════════════════════════════════════════════════

def aggregate(all_records):
    log.info(f"\nAggregating {len(all_records)} records…")

    pin_mkt  = defaultdict(list)
    pin_gv   = defaultdict(list)
    pin_meta = {}
    pin_srcs = defaultdict(set)

    for rec in all_records:
        pin = str(rec.get("pincode",""))
        if not (len(pin)==6 and pin.isdigit()): continue
        pin_meta[pin] = {"state": rec.get("state",""), "district": rec.get("district","")}
        pin_srcs[pin].add(rec.get("source",""))
        lo = rec.get("rate_min_sqft")
        hi = rec.get("rate_max_sqft")
        mid = ((lo or 0)+(hi or 0))/2 if (lo or hi) else None
        if mid and mid > 50:
            if rec.get("is_guideline"):
                pin_gv[pin].append(mid)
            else:
                pin_mkt[pin].append(mid)

    result = {}
    for pin in set(pin_mkt)|set(pin_gv):
        mkt = pin_mkt.get(pin,[])
        gv  = pin_gv.get(pin,[])
        meta = pin_meta.get(pin,{})
        n = len(mkt)+len(gv)
        mv = round(median(mkt)) if mkt else None
        gv_med = round(median(gv)) if gv else None
        conf = "HIGH" if n>=5 else "MEDIUM" if n>=2 else "LOW"
        result[pin] = {
            "state":                meta.get("state",""),
            "district":             meta.get("district",""),
            "market_rate_sqft":     mv,
            "guideline_value_sqft": gv_med,
            "data_points":          n,
            "confidence":           conf,
            "sources":              list(pin_srcs.get(pin,[])),
            "last_updated":         datetime.now().strftime("%Y-%m-%d"),
        }

    log.info(f"Aggregated: {len(result)} pincodes")
    mkt_pins = sum(1 for v in result.values() if v["market_rate_sqft"])
    gv_pins  = sum(1 for v in result.values() if v["guideline_value_sqft"])
    log.info(f"  Market value data : {mkt_pins} pincodes")
    log.info(f"  Guideline value data: {gv_pins} pincodes")
    return result


# ════════════════════════════════════════════════════════════════
#  SCHEDULER
# ════════════════════════════════════════════════════════════════

def setup_windows_scheduler():
    cmd = ["schtasks","/create","/tn","UGRO_LandRateScraper",
           "/tr",f'"{sys.executable}" "{os.path.abspath(__file__)}"',
           "/sc","DAILY","/st","06:00","/f","/rl","HIGHEST"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Daily scheduler set — runs at 6:00 AM every day")
        else:
            print(f"Scheduler error: {result.stderr}\nTry running as Administrator")
    except Exception as e:
        print(f"Could not set up scheduler: {e}")


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main_scrape():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  UGRO Capital — Land Rate Scraper v3.0                      ║
║  Market Value  → RERA portals                               ║
║  Guideline Val → SRO / IGR portals (circle rates)           ║
╚══════════════════════════════════════════════════════════════╝
    """)

    all_records = []

    market_scrapers = [
        ("MahaRERA (Market)",         scrape_maharera_market),
        ("UP RERA (Market)",          scrape_up_rera_market),
        ("Karnataka RERA (Market)",   scrape_karnataka_rera_market),
        ("TNRERA (Market)",           scrape_tnrera_market),
        ("Gujarat RERA (Market)",     scrape_gujarat_rera_market),
        ("Rajasthan RERA (Market)",   scrape_rajasthan_rera_market),
        ("Haryana RERA (Market)",     scrape_haryana_rera_market),
        ("MP RERA (Market)",          scrape_mp_rera_market),
        ("AP RERA (Market)",          scrape_ap_rera_market),
        ("Property Portals (Market)", scrape_property_portals_market),
    ]

    gv_scrapers = [
        ("IGR Maharashtra (GV)",   scrape_maharashtra_igr_gv),
        ("KAVERI Karnataka (GV)",  scrape_kaveri_karnataka_gv),
        ("TNREGINET Tamil Nadu (GV)", scrape_tnreginet_gv),
        ("IGRS Telangana (GV)",    scrape_igrs_telangana_gv),
        ("IGRS AP VLRD (GV)",      scrape_igrs_ap_gv),
        ("IGRSUP UP (GV)",         scrape_igrsup_gv),
        ("GARVI Gujarat (GV)",     scrape_garvi_gujarat_gv),
        ("IGR Rajasthan DLC (GV)", scrape_rajasthan_dlc_gv),
        ("Jamabandi Haryana (GV)", scrape_haryana_dlc_gv),
        ("Kerala Fair Value (GV)", scrape_kerala_fair_value_gv),
        ("WB Circle Rate (GV)",    scrape_wb_circle_rate_gv),
        ("MP IGR (GV)",            scrape_mp_igr_gv),
    ]

    print(f"\n{'═'*60}")
    print("  PHASE 1 — MARKET VALUE (RERA + Portals)")
    print(f"{'═'*60}")
    for name, fn in market_scrapers:
        print(f"\n  ▶ {name}")
        try:
            recs = fn()
            all_records.extend(recs)
            print(f"    ✓ {len(recs)} records")
        except Exception as e:
            log.error(f"{name}: {e}")
            print(f"    ✗ Failed: {e}")

    print(f"\n{'═'*60}")
    print("  PHASE 2 — GUIDELINE VALUE (SRO / IGR Portals)")
    print(f"{'═'*60}")
    for name, fn in gv_scrapers:
        print(f"\n  ▶ {name}")
        try:
            recs = fn()
            all_records.extend(recs)
            print(f"    ✓ {len(recs)} records")
        except Exception as e:
            log.error(f"{name}: {e}")
            print(f"    ✗ Failed: {e}")

    print(f"\n{'═'*60}")
    print(f"  Total raw records: {len(all_records)}")
    print(f"{'═'*60}")

    # Load existing and merge
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                old = json.load(f)
            existing = old.get("rates", {})
        except: pass

    new_rates = aggregate(all_records)
    merged    = {**existing, **new_rates}

    output = {
        "metadata": {
            "generated":      datetime.now().isoformat(),
            "total_pincodes": len(merged),
            "new_pincodes":   len(new_rates),
            "total_records":  len(all_records),
            "market_sources": [n for n,_ in market_scrapers],
            "gv_sources":     [n for n,_ in gv_scrapers],
            "note":           "Market Value from RERA. Guideline Value from SRO/IGR only.",
            "version":        "3.0",
        },
        "rates": merged,
    }

    with open(OUTPUT_FILE,"w") as f:
        json.dump(output, f, separators=(",",":"))

    sz = os.path.getsize(OUTPUT_FILE)//1024
    mkt_pins = sum(1 for v in merged.values() if v.get("market_rate_sqft"))
    gv_pins  = sum(1 for v in merged.values() if v.get("guideline_value_sqft"))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  COMPLETE — pincode_rates.json                              ║
╠══════════════════════════════════════════════════════════════╣
║  Total pincodes      : {len(merged):<6}                          ║
║  With market value   : {mkt_pins:<6} (from RERA / portals)     ║
║  With guideline value: {gv_pins:<6} (from SRO / IGR)           ║
║  File size           : {sz} KB                              ║
╠══════════════════════════════════════════════════════════════╣
║  Upload pincode_rates.json to GitHub repo                   ║
╚══════════════════════════════════════════════════════════════╝
    """)


def main():
    parser = argparse.ArgumentParser(description="UGRO Land Rate Scraper v3.0")
    parser.add_argument("--setup-scheduler", action="store_true",
                        help="Set up Windows Task Scheduler for daily auto-run at 6AM")
    args = parser.parse_args()
    if args.setup_scheduler:
        setup_windows_scheduler()
    else:
        main_scrape()

if __name__ == "__main__":
    main()
