#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  UGRO Capital — Land Rate Scraper v4.0
  ─────────────────────────────────────────────────────────────────
  MARKET VALUE   → Online listing portals only
                   (99acres, MagicBricks, Housing.com,
                    Squareyards, PropTiger)
                   Uses Playwright headless browser to bypass
                   bot protection and intercept API calls.

  GUIDELINE VALUE → SRO / IGR portals only (govt circle rates)
                   IGR Maharashtra (Ready Reckoner)
                   KAVERI Karnataka
                   TNREGINET Tamil Nadu
                   IGRS Telangana (MVA)
                   IGRS AP (VLRD)
                   IGRSUP Uttar Pradesh
                   GARVI Gujarat (Jantri)
                   IGR Rajasthan (DLC)
                   Jamabandi Haryana
                   Kerala Registration (Fair Value)
                   WB Registration (Circle Rates)
                   MP IGR

  FMV            → Simple average of market value entries
                   (portal data + manual team entries)

  NOTE: RERA rates are NOT used — they are declared project
        rates, not actual market transaction prices.
  ─────────────────────────────────────────────────────────────────
  SETUP:
    pip install requests beautifulsoup4 tqdm playwright schedule
    python -m playwright install chromium

  RUN:
    python ugro_rera_scraper.py

  DAILY AUTO-RUN:
    python ugro_rera_scraper.py --setup-scheduler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests, json, time, os, sys, logging, argparse, subprocess, re
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
    print("WARNING: Playwright not installed.")
    print("Run: pip install playwright && python -m playwright install chromium")
    print("Market value scraping from portals requires Playwright.\n")

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
            if r.status_code == 200: return r
            log.warning(f"HTTP {r.status_code} — {url[:60]}")
        except Exception as e:
            log.warning(f"GET attempt {attempt+1}: {str(e)[:60]}")
        time.sleep([2,5,10][attempt])
    return None

def safe_post(url, json_body=None, data=None, headers=None, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.post(url, json=json_body, data=data,
                             headers=headers, timeout=TIMEOUT)
            if r.status_code == 200: return r
            log.warning(f"HTTP {r.status_code} POST — {url[:60]}")
        except Exception as e:
            log.warning(f"POST attempt {attempt+1}: {str(e)[:60]}")
        time.sleep([2,5,10][attempt])
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

# Unit conversions → always to ₹/sqft
def sqm_to_sqft(v):    return v / 10.764  if v else None
def sqyard_to_sqft(v): return v / 1.196   if v else None
def cent_to_sqft(v):   return v / 435.6   if v else None  # 1 cent = 435.6 sqft

def extract_rate_recursive(data, depth=0):
    """Find ₹/sqft rate recursively in any JSON structure."""
    if depth > 5 or not data: return None
    rate_keys = ["avgRate","avg_rate","avgPricePerSqft","price_per_sqft",
                 "pricePerSqft","avg_price","averagePrice","ratePerSqft",
                 "avg_price_sqft","rate","currentRate","medianRate"]
    if isinstance(data, dict):
        for k, v in data.items():
            if any(x in k.lower() for x in ["sqft","persqft","rate","price"]):
                f = to_float(v)
                if f and 100 < f < 500000: return f
            r = extract_rate_recursive(v, depth+1)
            if r: return r
    elif isinstance(data, list):
        for item in data[:5]:
            r = extract_rate_recursive(item, depth+1)
            if r: return r
    return None

def extract_rate_from_html(html):
    """Extract ₹/sqft from HTML page content."""
    patterns = [
        r'₹\s*([\d,]+)\s*/\s*sq\.?ft',
        r'([\d,]+)\s*per\s*sq\.?ft',
        r'"pricePerSqft"\s*:\s*([\d.]+)',
        r'"avgRate"\s*:\s*([\d.]+)',
        r'"price_sqft"\s*:\s*([\d.]+)',
        r'"ratePerSqft"\s*:\s*([\d.]+)',
        r'"avgPricePerSqft"\s*:\s*([\d.]+)',
        r'"currentRate"\s*:\s*([\d.]+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for m in matches:
            f = to_float(m.replace(",",""))
            if f and 100 < f < 500000:
                return f
    return None


# ════════════════════════════════════════════════════════════════
#  SECTION A — MARKET VALUE: ONLINE LISTING PORTALS
#  Uses Playwright to render pages and intercept API calls
#  Portals: 99acres, MagicBricks, Housing.com, Squareyards, PropTiger
# ════════════════════════════════════════════════════════════════

# Pincode list for major cities — extend as needed
CITY_PINCODES = {
    "Mumbai":     ["400001","400002","400003","400004","400005","400006","400007",
                   "400008","400009","400010","400011","400012","400013","400014",
                   "400016","400018","400019","400020","400021","400022","400025",
                   "400028","400029","400030","400031","400050","400051","400052",
                   "400053","400054","400055","400056","400057","400058","400059"],
    "Delhi":      ["110001","110002","110003","110004","110005","110006","110007",
                   "110008","110009","110010","110011","110012","110013","110014",
                   "110015","110016","110017","110018","110019","110020","110021",
                   "110022","110023","110024","110025","110026","110027","110028",
                   "110029","110030","110031","110032","110033","110034","110035"],
    "Bengaluru":  ["560001","560002","560003","560004","560005","560006","560007",
                   "560008","560009","560010","560011","560012","560013","560014",
                   "560015","560016","560017","560018","560019","560020","560021",
                   "560022","560023","560024","560025","560026","560027","560028",
                   "560029","560030","560032","560034","560037","560038","560040"],
    "Chennai":    ["600001","600002","600003","600004","600005","600006","600007",
                   "600008","600009","600010","600011","600012","600013","600014",
                   "600015","600016","600017","600018","600020","600024","600025",
                   "600028","600030","600031","600032","600033","600034","600035"],
    "Hyderabad":  ["500001","500002","500003","500004","500005","500006","500007",
                   "500008","500009","500010","500011","500012","500013","500014",
                   "500016","500018","500019","500020","500026","500027","500028",
                   "500029","500032","500034","500035","500038","500040","500044"],
    "Pune":       ["411001","411002","411003","411004","411005","411006","411007",
                   "411008","411009","411010","411011","411012","411013","411014",
                   "411015","411016","411017","411018","411019","411020","411021",
                   "411022","411023","411024","411027","411030","411033","411037"],
    "Kolkata":    ["700001","700002","700003","700004","700005","700006","700007",
                   "700008","700009","700010","700011","700012","700013","700014",
                   "700015","700016","700017","700018","700019","700020","700025",
                   "700026","700027","700028","700029","700030","700032","700035"],
    "Ahmedabad":  ["380001","380002","380004","380005","380006","380007","380008",
                   "380009","380013","380014","380015","380016","380019","380021",
                   "380051","380052","380054","380055","380058","380059","380060"],
    "Jaipur":     ["302001","302002","302003","302004","302005","302006","302010",
                   "302011","302012","302013","302015","302016","302017","302018",
                   "302019","302020","302021","302022","302023","302024","302025"],
    "Lucknow":    ["226001","226002","226003","226004","226005","226006","226007",
                   "226008","226009","226010","226011","226012","226013","226014",
                   "226016","226017","226018","226019","226020","226021","226022"],
    "Noida":      ["201301","201302","201303","201304","201305","201306","201307",
                   "201308","201309","201310","201311","201312","201313","201314"],
    "Gurugram":   ["122001","122002","122003","122004","122005","122006","122007",
                   "122008","122009","122010","122011","122012","122013","122015",
                   "122017","122018","122051","122052","122101","122102","122103"],
    "Coimbatore": ["641001","641002","641003","641004","641005","641006","641007",
                   "641008","641009","641010","641011","641012","641013","641014",
                   "641015","641016","641017","641018","641019","641020","641021"],
    "Kochi":      ["682001","682002","682003","682004","682005","682006","682007",
                   "682008","682009","682010","682011","682012","682013","682014",
                   "682016","682017","682018","682019","682020","682021","682022"],
    "Chandigarh": ["160001","160002","160003","160004","160005","160006","160009",
                   "160010","160011","160012","160014","160015","160017","160018",
                   "160019","160020","160022","160023","160026","160030","160036"],
    "Bhopal":     ["462001","462002","462003","462004","462010","462011","462016",
                   "462020","462023","462024","462026","462030","462031","462038"],
    "Patna":      ["800001","800002","800003","800004","800005","800006","800007",
                   "800008","800009","800010","800011","800012","800013","800014"],
    "Indore":     ["452001","452002","452003","452004","452005","452006","452007",
                   "452008","452009","452010","452011","452012","452013","452014"],
    "Nagpur":     ["440001","440002","440003","440004","440005","440006","440007",
                   "440008","440009","440010","440011","440012","440013","440014"],
    "Surat":      ["395001","395002","395003","395004","395005","395006","395007",
                   "395008","395009","395010","395011","395012","395013","395014"],
}

CITY_STATE = {
    "Mumbai":"MAHARASHTRA","Delhi":"DELHI","Bengaluru":"KARNATAKA",
    "Chennai":"TAMIL NADU","Hyderabad":"TELANGANA","Pune":"MAHARASHTRA",
    "Kolkata":"WEST BENGAL","Ahmedabad":"GUJARAT","Jaipur":"RAJASTHAN",
    "Lucknow":"UTTAR PRADESH","Noida":"UTTAR PRADESH","Gurugram":"HARYANA",
    "Coimbatore":"TAMIL NADU","Kochi":"KERALA","Chandigarh":"CHANDIGARH",
    "Bhopal":"MADHYA PRADESH","Patna":"BIHAR","Indore":"MADHYA PRADESH",
    "Nagpur":"MAHARASHTRA","Surat":"GUJARAT",
}

# Portal configurations: URL pattern + API interception endpoints
PORTAL_CONFIGS = {
    "99acres": {
        "url_pattern": "https://www.99acres.com/locality-detail/{pin}",
        "api_intercepts": [
            "ratetrend", "pricetrend", "localityrate",
            "price-trend", "locality/price",
        ],
        "fallback_url": "https://www.99acres.com/search/property/buy/{pin}",
    },
    "MagicBricks": {
        "url_pattern": "https://www.magicbricks.com/locality-rates/{pin}",
        "api_intercepts": [
            "pricetrend", "localityData", "price-trend",
            "avgPrice", "ratetrend",
        ],
        "fallback_url": "https://www.magicbricks.com/property-rates-trends/residential-properties-in-{city}",
    },
    "Housing.com": {
        "url_pattern": "https://housing.com/in/buy/{city}/locality/{pin}",
        "api_intercepts": [
            "pricetrend", "locality/insights", "price-trend",
            "averagePrice", "locality_rates",
        ],
        "fallback_url": "https://housing.com/price-trends/{city}",
    },
    "Squareyards": {
        "url_pattern": "https://www.squareyards.com/buy-property/{pin}",
        "api_intercepts": [
            "pricetrend", "locality-rate", "propertyRates",
            "avgPrice", "price-insight",
        ],
        "fallback_url": "https://www.squareyards.com/{city}/property-rates",
    },
    "PropTiger": {
        "url_pattern": "https://www.proptiger.com/{city}/locality/{pin}",
        "api_intercepts": [
            "price-trend", "locality/price", "avgRate",
            "priceInsight", "ratetrend",
        ],
        "fallback_url": "https://www.proptiger.com/{city}/property-rates",
    },
}


def scrape_portal_playwright(portal_name, config, city_pincodes):
    """
    Use Playwright to scrape a property portal.
    Strategy:
    1. Navigate to pincode/locality page
    2. Intercept API responses containing price data
    3. Fallback: extract from rendered HTML
    """
    results = []
    if not HAS_PLAYWRIGHT:
        log.warning(f"Playwright not available — skipping {portal_name}")
        return results

    log.info(f"── {portal_name} (Market Value) ─────────────────────")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                viewport={"width":1366,"height":768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            # Stealth: hide automation markers
            context.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                window.chrome={runtime:{}};
            """)
            page = context.new_page()

            # Intercept API responses
            intercepted_rates = {}

            def handle_response(response):
                url = response.url.lower()
                if any(kw in url for kw in config["api_intercepts"]):
                    try:
                        data = response.json()
                        rate = extract_rate_recursive(data)
                        if rate and 100 < rate < 500000:
                            # Try to find pincode context from URL
                            pin_match = re.search(r'\b(\d{6})\b', response.url)
                            if pin_match:
                                intercepted_rates[pin_match.group(1)] = rate
                            else:
                                intercepted_rates["_last"] = rate
                    except: pass

            page.on("response", handle_response)

            for city, pincodes in city_pincodes.items():
                state = CITY_STATE.get(city, "")
                city_lower = city.lower().replace(" ","-")
                log.info(f"  {portal_name} → {city} ({len(pincodes)} pincodes)")

                for pin in pincodes:
                    try:
                        intercepted_rates.clear()
                        url = config["url_pattern"].format(
                            pin=pin, city=city_lower)

                        page.goto(url, timeout=25000,
                                  wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)

                        rate = intercepted_rates.get(pin) or \
                               intercepted_rates.get("_last")

                        # Fallback: extract from HTML
                        if not rate:
                            rate = extract_rate_from_html(page.content())

                        if rate and 100 < rate < 500000:
                            results.append(make_record(
                                pin, state, city,
                                portal_name,
                                config["url_pattern"].split("/")[2],
                                rate*0.92, rate*1.08,
                                is_guideline=False
                            ))
                            log.info(f"    {pin}: ₹{rate:,.0f}/sqft")
                        else:
                            log.debug(f"    {pin}: no rate found")

                        time.sleep(1.2)  # polite delay
                    except Exception as e:
                        log.warning(f"    {pin}: {str(e)[:50]}")
                        time.sleep(0.5)

            browser.close()
    except Exception as e:
        log.error(f"{portal_name} Playwright error: {e}")

    log.info(f"{portal_name} market: {len(results)} records")
    return results


def scrape_all_portals_market():
    """Scrape all 5 listing portals for market value."""
    all_results = []
    for portal_name, config in PORTAL_CONFIGS.items():
        try:
            recs = scrape_portal_playwright(
                portal_name, config, CITY_PINCODES)
            all_results.extend(recs)
        except Exception as e:
            log.error(f"{portal_name}: {e}")
    log.info(f"All portals total market: {len(all_results)} records")
    return all_results


# ════════════════════════════════════════════════════════════════
#  SECTION B — GUIDELINE VALUE: SRO / IGR PORTALS
#  These are the ONLY correct sources for circle/stamp rates
# ════════════════════════════════════════════════════════════════

def scrape_maharashtra_igr_gv():
    """IGR Maharashtra Ready Reckoner — ₹/sqm → ₹/sqft"""
    log.info("── IGR Maharashtra (Ready Reckoner GV) ──────────────")
    results = []
    r = safe_get(
        "https://freesearchigrservice.maharashtra.gov.in/api/RR/GetDistrictList",
        headers={"Referer":"https://igrmaharashtra.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("districtId") or dist.get("id")
        d_name = dist.get("districtName") or ""

        r2 = safe_get(
            "https://freesearchigrservice.maharashtra.gov.in/api/RR/GetTalukaList",
            params={"districtId": d_id},
            headers={"Referer":"https://igrmaharashtra.gov.in"})
        if not r2: continue
        try: talukas = r2.json()
        except: continue

        for taluka in (talukas if isinstance(talukas,list) else [])[:5]:
            t_id = taluka.get("talukaId") or taluka.get("id")
            r3 = safe_post(
                "https://freesearchigrservice.maharashtra.gov.in/api/RR/GetRRRateDetails",
                json_body={"districtId":d_id,"talukaId":t_id,
                           "year":datetime.now().year},
                headers={"Referer":"https://igrmaharashtra.gov.in",
                         "Content-Type":"application/json"})
            if not r3: continue
            try:
                items = r3.json()
                if isinstance(items,dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("Pincode","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqm = to_float(item.get("openLandRate") or
                                        item.get("LandRate") or item.get("rate"))
                    if not rate_sqm or rate_sqm<100: continue
                    rate_sqft = sqm_to_sqft(rate_sqm)
                    results.append(make_record(pin,"MAHARASHTRA",d_name,
                        "IGR-MH-ReadyReckoner",
                        "https://igrmaharashtra.gov.in",
                        rate_sqft*0.95, rate_sqft*1.05, is_guideline=True))
            except: pass
            time.sleep(0.3)

    log.info(f"IGR Maharashtra GV: {len(results)}")
    return results


def scrape_kaveri_karnataka_gv():
    """KAVERI Karnataka Guideline Values — ₹/sqm or ₹/sqft"""
    log.info("── KAVERI Karnataka (Guideline Values GV) ───────────")
    results = []
    r = safe_get(
        "https://kaveri.karnataka.gov.in/MobileService/api/District/GetDistrictList")
    if not r: return results
    try: districts = r.json()
    except: return results

    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("DistrictCode") or dist.get("id")
        d_name = dist.get("DistrictName") or ""
        rv = safe_get(
            "https://kaveri.karnataka.gov.in/MobileService/api/Village/GetVillageList",
            params={"districtCode":d_id})
        if not rv: continue
        try: villages = rv.json()
        except: continue

        for village in (villages if isinstance(villages,list) else [])[:10]:
            v_id = village.get("VillageCode") or village.get("id")
            rg = safe_get(
                "https://kaveri.karnataka.gov.in/MobileService/api/GuidelineValue/GetGVDetails",
                params={"villageCode":v_id})
            if not rg: continue
            try: gv_data = rg.json()
            except: continue
            for item in (gv_data if isinstance(gv_data,list) else [gv_data]):
                pin = str(item.get("Pincode") or item.get("pinCode","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("LandRate") or item.get("GVRate") or
                                item.get("rate"))
                if not rate or rate<50: continue
                unit = str(item.get("RateUnit") or "sqm").lower()
                if "sqm" in unit or "sq.m" in unit:
                    rate = sqm_to_sqft(rate)
                results.append(make_record(pin,"KARNATAKA",d_name,
                    "KAVERI-IGR","https://kaveri.karnataka.gov.in",
                    rate*0.95, rate*1.05, is_guideline=True))
            time.sleep(0.3)

    log.info(f"KAVERI Karnataka GV: {len(results)}")
    return results


def scrape_tnreginet_gv():
    """TNREGINET Tamil Nadu — ₹/sqft"""
    log.info("── TNREGINET Tamil Nadu (GV) ────────────────────────")
    results = []
    for dist_id in range(1,39):
        r = safe_post(
            "https://tnreginet.gov.in/portal/AppController",
            data={"requestType":"AjaxRequest","actionVal":"getGuidelineDetails",
                  "districtId":str(dist_id),"pageNo":"1"},
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer":"https://tnreginet.gov.in"})
        if not r: continue
        try:
            data  = r.json()
            items = data.get("data") or data.get("guidelineList") or []
            for item in items:
                pin = str(item.get("pincode") or item.get("PinCode","")).strip()
                if len(pin)!=6 or not pin.isdigit(): continue
                rate = to_float(item.get("guidelineValue") or item.get("rate"))
                if not rate or rate<50: continue
                if rate > 50000: rate = sqm_to_sqft(rate)
                results.append(make_record(pin,"TAMIL NADU",
                    str(item.get("district","")).upper(),
                    "TNREGINET","https://tnreginet.gov.in",
                    rate*0.95,rate*1.05,is_guideline=True))
        except: pass
        time.sleep(0.3)
    log.info(f"TNREGINET TN GV: {len(results)}")
    return results


def scrape_igrs_telangana_gv():
    """IGRS Telangana MVA — ₹/sqyard → ₹/sqft"""
    log.info("── IGRS Telangana (MVA GV) ──────────────────────────")
    results = []
    ts_districts = [
        ("1","Hyderabad"),("2","Rangareddy"),("3","Medchal"),
        ("4","Sangareddy"),("5","Nizamabad"),("6","Karimnagar"),
        ("7","Warangal"),("8","Khammam"),("9","Nalgonda"),
        ("10","Mahabubnagar"),("11","Vikarabad"),("12","Siddipet"),
    ]
    for dist_code, dist_name in ts_districts:
        rm = safe_post(
            "https://igrs.telangana.gov.in/igrs/MandalDetails.do",
            data={"districtCode":dist_code},
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
                items = rv.json()
                if isinstance(items,dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqyard = to_float(item.get("landRate") or item.get("rate"))
                    if not rate_sqyard or rate_sqyard<50: continue
                    rate_sqft = sqyard_to_sqft(rate_sqyard)
                    results.append(make_record(pin,"TELANGANA",dist_name,
                        "IGRS-Telangana-MVA","https://igrs.telangana.gov.in",
                        rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
            except: pass
            time.sleep(0.3)
    log.info(f"IGRS Telangana GV: {len(results)}")
    return results


def scrape_igrs_ap_gv():
    """IGRS AP VLRD — ₹/sqyard → ₹/sqft"""
    log.info("── IGRS AP (VLRD GV) ────────────────────────────────")
    results = []
    r = safe_get("https://registration.ap.gov.in/vlrd/getDistrictList",
                 headers={"Referer":"https://registration.ap.gov.in"})
    if not r: return results
    try: districts = r.json()
    except: return results
    for dist in (districts if isinstance(districts,list) else []):
        d_code = dist.get("districtCode") or dist.get("id")
        d_name = dist.get("districtName") or ""
        rm = safe_post("https://registration.ap.gov.in/vlrd/getMandalList",
                       json_body={"districtCode":d_code},
                       headers={"Referer":"https://registration.ap.gov.in"})
        if not rm: continue
        try: mandals = rm.json()
        except: continue
        for mandal in (mandals if isinstance(mandals,list) else [])[:5]:
            m_code = mandal.get("mandalCode") or mandal.get("id")
            rv = safe_post("https://registration.ap.gov.in/vlrd/getValuationDetails",
                           json_body={"districtCode":d_code,"mandalCode":str(m_code),
                                      "propertyType":"Residential"},
                           headers={"Referer":"https://registration.ap.gov.in"})
            if not rv: continue
            try:
                items = rv.json()
                if isinstance(items,dict): items = items.get("data",[])
                for item in (items if isinstance(items,list) else []):
                    pin = str(item.get("pinCode") or item.get("pin","")).strip()
                    if len(pin)!=6 or not pin.isdigit(): continue
                    rate_sqyard = to_float(item.get("landRate") or item.get("rate"))
                    if not rate_sqyard or rate_sqyard<50: continue
                    rate_sqft = sqyard_to_sqft(rate_sqyard)
                    results.append(make_record(pin,"ANDHRA PRADESH",d_name,
                        "IGRS-AP-VLRD","https://registration.ap.gov.in",
                        rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
            except: pass
            time.sleep(0.3)
    log.info(f"IGRS AP GV: {len(results)}")
    return results


def scrape_igrsup_gv():
    """IGRSUP UP Circle Rates — ₹/sqm → ₹/sqft"""
    log.info("── IGRSUP UP (Circle Rates GV) ──────────────────────")
    results = []
    for dist_id in range(1,76):
        r_t = safe_post(
            "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
            data={"stampDutyCalcBean.districtId":str(dist_id),
                  "actionType":"getTehsilList"},
            headers={"X-Requested-With":"XMLHttpRequest",
                     "Referer":"https://igrsup.gov.in/igrsup/"})
        if not r_t: continue
        try: tehsils = r_t.json()
        except: continue
        for tehsil in (tehsils if isinstance(tehsils,list) else []):
            t_id = tehsil.get("id") or tehsil.get("value")
            if not t_id: continue
            r_r = safe_post(
                "https://igrsup.gov.in/igrsup/stampdutycalcAction.action",
                data={"stampDutyCalcBean.districtId":str(dist_id),
                      "stampDutyCalcBean.tehsilId":str(t_id),
                      "stampDutyCalcBean.propertyType":"1",
                      "actionType":"getCircleRate"},
                headers={"X-Requested-With":"XMLHttpRequest",
                         "Referer":"https://igrsup.gov.in/igrsup/"})
            if not r_r: continue
            try:
                rd = r_r.json()
                rate_sqm = to_float(rd.get("circleRate") or rd.get("rate") or
                                    rd.get("residentialRate"))
                if not rate_sqm or rate_sqm<100: continue
                rate_sqft = sqm_to_sqft(rate_sqm)
                pin = str(tehsil.get("pinCode","")).strip()
                if len(pin)==6 and pin.isdigit():
                    results.append(make_record(pin,"UTTAR PRADESH",
                        str(tehsil.get("districtName","")).upper(),
                        "IGRSUP-CircleRate","https://igrsup.gov.in",
                        rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
            except: pass
            time.sleep(0.3)
    log.info(f"IGRSUP UP GV: {len(results)}")
    return results


def scrape_garvi_gujarat_gv():
    """GARVI Gujarat Jantri Rates — ₹/sqm → ₹/sqft"""
    log.info("── GARVI Gujarat (Jantri GV) ────────────────────────")
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
                      params={"districtCode":d_code},
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
                        rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
            except: pass
            time.sleep(0.3)
    log.info(f"GARVI Gujarat GV: {len(results)}")
    return results


def scrape_rajasthan_dlc_gv():
    """IGR Rajasthan DLC Rates — ₹/sqm → ₹/sqft"""
    log.info("── IGR Rajasthan (DLC Rates GV) ─────────────────────")
    results = []
    for url in ["https://epanjiyan.rajasthan.gov.in/api/district/list",
                "https://igrs.rajasthan.gov.in/api/district/list"]:
        r = safe_get(url, headers={"Referer":"https://igrs.rajasthan.gov.in"})
        if r: break
    if not r: return results
    try: districts = r.json()
    except: return results
    for dist in (districts if isinstance(districts,list) else []):
        d_id   = dist.get("districtId") or dist.get("id")
        d_name = dist.get("districtName") or ""
        rv = safe_get(
            "https://epanjiyan.rajasthan.gov.in/api/dlcrate/getByDistrict",
            params={"districtId":d_id,"propertyType":"residential"},
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
                    "IGR-RJ-DLC","https://igrs.rajasthan.gov.in",
                    rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
        except: pass
        time.sleep(0.3)
    log.info(f"Rajasthan DLC GV: {len(results)}")
    return results


def scrape_haryana_dlc_gv():
    """Jamabandi Haryana Collector Rates — ₹/sqyard → ₹/sqft"""
    log.info("── Jamabandi Haryana (DLC GV) ───────────────────────")
    results = []
    r = safe_get("https://jamabandi.nic.in/land%20records/CollectorRates",
                 headers={"Referer":"https://jamabandi.nic.in"})
    if not r: return results
    if HAS_BS4:
        soup = BeautifulSoup(r.text,"lxml")
        for row in soup.select("table tbody tr"):
            cells = row.find_all("td")
            if len(cells) >= 4:
                pin = cells[-1].get_text(strip=True)
                if len(pin)==6 and pin.isdigit():
                    rate_sqyard = to_float(cells[2].get_text(strip=True))
                    if rate_sqyard and rate_sqyard > 10:
                        rate_sqft = sqyard_to_sqft(rate_sqyard)
                        results.append(make_record(pin,"HARYANA","",
                            "Jamabandi-DLC","https://jamabandi.nic.in",
                            rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
    log.info(f"Haryana DLC GV: {len(results)}")
    return results


def scrape_kerala_fair_value_gv():
    """Kerala Registration Fair Value — ₹/cent → ₹/sqft"""
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
                      params={"districtId":d_id},
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
                    rate_cent = to_float(item.get("fairValue") or item.get("rate"))
                    if not rate_cent or rate_cent<1000: continue
                    rate_sqft = cent_to_sqft(rate_cent)
                    results.append(make_record(pin,"KERALA",d_name,
                        "Kerala-FairValue","https://keralaregistration.gov.in",
                        rate_sqft*0.95,rate_sqft*1.05,is_guideline=True))
            except: pass
            time.sleep(0.3)
    log.info(f"Kerala Fair Value GV: {len(results)}")
    return results


def scrape_wb_circle_rate_gv():
    """WB Registration Circle Rates — ₹/decimal or ₹/sqft"""
    log.info("── WB Registration (Circle Rates GV) ────────────────")
    results = []
    for dist_id in range(1,24):
        r = safe_post(
            "https://wbregistration.gov.in/api/circleRate/getByDistrict",
            json_body={"districtId":dist_id,"propertyType":"land"},
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
                if not rate or rate<100: continue
                unit = str(item.get("unit") or "sqft").lower()
                if "sqm" in unit: rate = sqm_to_sqft(rate)
                elif "decimal" in unit: rate = cent_to_sqft(rate)
                results.append(make_record(pin,"WEST BENGAL","",
                    "WB-CircleRate","https://wbregistration.gov.in",
                    rate*0.95,rate*1.05,is_guideline=True))
        except: pass
        time.sleep(0.3)
    log.info(f"WB Circle Rate GV: {len(results)}")
    return results


def scrape_mp_igr_gv():
    """MP IGR Guideline Values — ₹/sqft"""
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
            json_body={"districtId":d_id,"propertyType":"Residential"},
            headers={"Referer":"https://mpigr.gov.in",
                     "Content-Type":"application/json"})
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
                    rate*0.95,rate*1.05,is_guideline=True))
        except: pass
        time.sleep(0.3)
    log.info(f"MP IGR GV: {len(results)}")
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
        pin_meta[pin] = {"state":rec.get("state",""),"district":rec.get("district","")}
        pin_srcs[pin].add(rec.get("source",""))
        lo, hi = rec.get("rate_min_sqft"), rec.get("rate_max_sqft")
        mid = ((lo or 0)+(hi or 0))/2 if (lo or hi) else None
        if mid and mid>50:
            if rec.get("is_guideline"):
                pin_gv[pin].append(mid)
            else:
                pin_mkt[pin].append(mid)

    result = {}
    for pin in set(pin_mkt)|set(pin_gv):
        mkt  = pin_mkt.get(pin,[])
        gv   = pin_gv.get(pin,[])
        meta = pin_meta.get(pin,{})
        n    = len(mkt)+len(gv)
        # Market value: simple average (for FMV calculation in platform)
        mv   = round(sum(mkt)/len(mkt)) if mkt else None
        # Guideline value: median of SRO/IGR data points
        gv_v = round(median(gv)) if gv else None
        conf = "HIGH" if n>=5 else "MEDIUM" if n>=2 else "LOW"
        result[pin] = {
            "state":                meta.get("state",""),
            "district":             meta.get("district",""),
            "market_rate_sqft":     mv,
            "guideline_value_sqft": gv_v,
            "data_points":          n,
            "market_data_points":   len(mkt),
            "gv_data_points":       len(gv),
            "confidence":           conf,
            "sources":              list(pin_srcs.get(pin,[])),
            "last_updated":         datetime.now().strftime("%Y-%m-%d"),
        }

    mkt_pins = sum(1 for v in result.values() if v["market_rate_sqft"])
    gv_pins  = sum(1 for v in result.values() if v["guideline_value_sqft"])
    log.info(f"Aggregated: {len(result)} pincodes | "
             f"Market: {mkt_pins} | GV: {gv_pins}")
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
        if result.returncode==0:
            print("✓ Daily scheduler set — runs at 6:00 AM every day")
        else:
            print(f"Scheduler error: {result.stderr}")
            print("Try running Command Prompt as Administrator")
    except Exception as e:
        print(f"Could not set up scheduler: {e}")


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main_scrape():
    print("""
╔══════════════════════════════════════════════════════════════╗
║  UGRO Capital — Land Rate Scraper v4.0                      ║
╠══════════════════════════════════════════════════════════════╣
║  Market Value    → Listing portals only                     ║
║                    (99acres, MagicBricks, Housing.com,      ║
║                     Squareyards, PropTiger)                 ║
║  Guideline Value → SRO / IGR portals only                   ║
║                    (circle/stamp rates — 12 states)         ║
║  FMV             → Simple avg of market entries             ║
╚══════════════════════════════════════════════════════════════╝
    """)

    all_records = []

    print(f"\n{'═'*60}")
    print("  PHASE 1 — MARKET VALUE (Listing Portals via Playwright)")
    print(f"{'═'*60}")
    if not HAS_PLAYWRIGHT:
        print("  ⚠ Playwright not installed — skipping market value scraping")
        print("  Run: pip install playwright && python -m playwright install chromium")
    else:
        try:
            recs = scrape_all_portals_market()
            all_records.extend(recs)
            print(f"  ✓ Market value: {len(recs)} records from portals")
        except Exception as e:
            log.error(f"Portal scraping: {e}")

    print(f"\n{'═'*60}")
    print("  PHASE 2 — GUIDELINE VALUE (SRO / IGR Portals)")
    print(f"{'═'*60}")

    gv_scrapers = [
        ("IGR Maharashtra RR (GV)",   scrape_maharashtra_igr_gv),
        ("KAVERI Karnataka (GV)",     scrape_kaveri_karnataka_gv),
        ("TNREGINET Tamil Nadu (GV)", scrape_tnreginet_gv),
        ("IGRS Telangana MVA (GV)",   scrape_igrs_telangana_gv),
        ("IGRS AP VLRD (GV)",         scrape_igrs_ap_gv),
        ("IGRSUP UP (GV)",            scrape_igrsup_gv),
        ("GARVI Gujarat (GV)",        scrape_garvi_gujarat_gv),
        ("IGR Rajasthan DLC (GV)",    scrape_rajasthan_dlc_gv),
        ("Jamabandi Haryana (GV)",    scrape_haryana_dlc_gv),
        ("Kerala Fair Value (GV)",    scrape_kerala_fair_value_gv),
        ("WB Circle Rate (GV)",       scrape_wb_circle_rate_gv),
        ("MP IGR (GV)",               scrape_mp_igr_gv),
    ]

    for name, fn in gv_scrapers:
        print(f"\n  ▶ {name}")
        try:
            recs = fn()
            all_records.extend(recs)
            print(f"    ✓ {len(recs)} records")
        except Exception as e:
            log.error(f"{name}: {e}")
            print(f"    ✗ {e}")

    print(f"\n{'═'*60}")
    print(f"  Total raw records: {len(all_records)}")

    # Load existing + merge
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                old = json.load(f)
            existing = old.get("rates",{})
        except: pass

    new_rates = aggregate(all_records)
    merged    = {**existing, **new_rates}

    output = {
        "metadata": {
            "generated":        datetime.now().isoformat(),
            "total_pincodes":   len(merged),
            "total_records":    len(all_records),
            "market_source":    "Listing portals: 99acres, MagicBricks, Housing.com, Squareyards, PropTiger",
            "gv_source":        "SRO/IGR only: IGR-MH, KAVERI, TNREGINET, IGRS-TS, IGRS-AP, IGRSUP, GARVI, IGR-RJ, Jamabandi, Kerala, WB, MP-IGR",
            "fmv_method":       "Simple average of market value entries (portal data + manual team entries)",
            "rera_excluded":    True,
            "version":          "4.0",
        },
        "rates": merged,
    }

    with open(OUTPUT_FILE,"w") as f:
        json.dump(output, f, separators=(",",":"))

    sz       = os.path.getsize(OUTPUT_FILE)//1024
    mkt_pins = sum(1 for v in merged.values() if v.get("market_rate_sqft"))
    gv_pins  = sum(1 for v in merged.values() if v.get("guideline_value_sqft"))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  COMPLETE — pincode_rates.json                              ║
╠══════════════════════════════════════════════════════════════╣
║  Total pincodes        : {len(merged):<6}                        ║
║  With market value     : {mkt_pins:<6} (portal listings)        ║
║  With guideline value  : {gv_pins:<6} (SRO/IGR only)            ║
║  File size             : {sz} KB                            ║
╠══════════════════════════════════════════════════════════════╣
║  Upload pincode_rates.json to GitHub repo                   ║
╚══════════════════════════════════════════════════════════════╝
    """)


def main():
    parser = argparse.ArgumentParser(description="UGRO Land Rate Scraper v4.0")
    parser.add_argument("--setup-scheduler", action="store_true",
                        help="Set up Windows Task Scheduler (daily at 6AM)")
    args = parser.parse_args()
    if args.setup_scheduler:
        setup_windows_scheduler()
    else:
        main_scrape()

if __name__ == "__main__":
    main()
