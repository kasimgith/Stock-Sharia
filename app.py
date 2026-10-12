import os
import re
import sys
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
import streamlit as st

# =====================================================================
# 1. PAGE CONFIGURATION & COMPACT STYLING
# =====================================================================
st.set_page_config(
    page_title="Shariah Compliance Screener",
    page_icon="🕌",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1000px; }
    .ratio-card-pass {
        background-color: #16a34a;
        color: #ffffff !important;
        border-radius: 10px;
        padding: 16px 12px;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 10px;
    }
    .ratio-card-fail {
        background-color: #dc2626;
        color: #ffffff !important;
        border-radius: 10px;
        padding: 16px 12px;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        margin-bottom: 10px;
    }
    .card-title {
        font-size: 0.85rem;
        font-weight: 600;
        opacity: 0.95;
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .card-value {
        font-size: 1.7rem;
        font-weight: 800;
        line-height: 1.1;
    }
    .card-limit {
        font-size: 0.75rem;
        opacity: 0.85;
        margin-top: 4px;
    }
    .meta-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

GOOGLE_SHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1J6338cEY3ikNu6qR4g871fG_Hdgywe8RH2CawPT2cjc/export?format=csv&gid=0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PROHIBITED_SECTORS = ["bank", "finance", "media", "defence"]

PROHIBITED_INDUSTRIES = [
    "finance & investments", "banks - private sector", "banks - public sector",
    "bank", "finance - housing", "hotels", "finance",
    "finance - term-lending institutions", "photographic and allied products",
    "breweries & distilleries", "cigarettes",
    "entertainment / electronic media software", "recreation / amusement parks"
]

PROHIBITED_KEYWORDS = [
    "beer", "liquor", "pork", "alcohol", "gambling", "betting", "tobacco", "cigar",
    "cloning", "television", "advertising", "media", "bank", "insurance", "porn",
    "advertisment", "breweries", "brewery", "distilleries", "distillery", "club",
    "photo", "video", "broker", "hotel", "music", "disco", "wine", "winery",
    "tattoo", "bfsi", "finance", "entertainment", "defence",
    "vodka", "whiskey", "whisky", "rum", "gin", "brandy", "ethanol", "ena", "imil",
    "spirit", "spirits", "brew", "cider", "beedi", "cigarette", "casino", "lending",
    "nbfc", "microfinance"
]

BLACKLIST_REASONS = {
    "AGI": "Wine bottle manufacturer",
    "ATULAUTO": "Finance & banking income",
    "526187": "Ritual / Religious ashram operations",
    "PRAJIND": "Wine factory equipment / setup maker",
    "GULPOLY": "Wine / distillery raw material provider",
    "INFY": "Finance & banking core software/income",
}

# =====================================================================
# 2. INSTANT CACHED MASTER INVENTORY & AUTO-SUGGEST LIST
# =====================================================================
@st.cache_data(ttl=86400, show_spinner=False)
def load_master_inventory_and_suggestions():
    try:
        df = pd.read_csv(GOOGLE_SHEET_CSV_URL)
        df.columns = [str(c).strip() for c in df.columns]

        inv_cols = [c for c in df.columns if "inventory" in c.lower()]
        sym_cols = [c for c in df.columns if "symbol" in c.lower()]
        name_cols = [c for c in df.columns if "name" in c.lower()]

        inv_db = {}
        suggestions = []

        if inv_cols and sym_cols:
            inv_col = inv_cols[0]
            sym_col = sym_cols[0]
            name_col = name_cols[0] if name_cols else None

            for _, row in df.iterrows():
                val_raw = re.sub(r"[^\d.-]", "", str(row[inv_col]))
                val = float(val_raw) if val_raw else 0.0

                sym = str(row[sym_col]).strip().upper()
                name_val = str(row[name_col]).strip() if name_col else ""

                if sym and sym not in ["0", "NAN"]:
                    inv_db[sym] = val
                    if name_val:
                        suggestions.append(f"{sym} — {name_val}")
                    else:
                        suggestions.append(sym)

                if name_col and name_val:
                    n_clean = re.sub(r"[^\w\s]", "", name_val).strip().upper()
                    if n_clean:
                        inv_db[n_clean] = val

            return inv_db, sorted(list(set(suggestions)))
    except Exception as e:
        st.warning(f"Google Sheet load warning: {e}")
    return {}, []

# =====================================================================
# 3. BACKEND CORE PARSERS & SCRAPING (100% SAME AS VERIFIED)
# =====================================================================
def parse_screener_date(date_str):
    if not date_str:
        return datetime.min
    clean = date_str.strip()
    for fmt in ("%b %Y", "%B %Y", "%m/%Y"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            pass
    m = re.search(r"(\d{4})", clean)
    if m:
        return datetime(int(m.group(1)), 3, 31)
    return datetime.min

def extract_deep_business_and_keypoints(primary_soup):
    industry_name = "Not Available"
    sector_name = "Not Available"

    market_links = []
    for a in primary_soup.find_all("a", href=True):
        href = a.get("href", "")
        txt = a.text.strip()
        if "/market/" in href and txt:
            market_links.append(txt)

    peers_sec = primary_soup.find("section", {"id": "peers"})
    if peers_sec and not market_links:
        for a in peers_sec.find_all("a", href=True):
            href = a.get("href", "")
            txt = a.text.strip()
            if "/market/" in href and txt and txt not in market_links:
                market_links.append(txt)

    if len(market_links) >= 2:
        sector_name = market_links[0]
        industry_name = market_links[1]
    elif len(market_links) == 1:
        industry_name = market_links[0]

    sector_flagged = any(ps in sector_name.lower() for ps in PROHIBITED_SECTORS)
    industry_flagged = any(pi in industry_name.lower() for pi in PROHIBITED_INDUSTRIES)

    about_text = ""
    about_div = primary_soup.find("div", {"class": "about"})
    if about_div:
        about_text = about_div.get_text(" ", strip=True)

    key_points_list = []
    for d in primary_soup.find_all("div", {"class": lambda x: x and ("commentary" in x or "key-points" in x)}):
        txt = d.get_text(" ", strip=True)
        txt = re.sub(r"Run queries on 10 years.*?developments\.", "", txt, flags=re.DOTALL)
        if txt:
            key_points_list.append(txt)

    for d in primary_soup.find_all("div", {"class": "sub"}):
        txt = d.get_text(" ", strip=True)
        txt = re.sub(r"Run queries on 10 years.*?developments\.", "", txt, flags=re.DOTALL)
        if txt and txt not in key_points_list:
            key_points_list.append(txt)

    full_narrative = f"{about_text} {' '.join(key_points_list)} {sector_name} {industry_name}".lower()

    flagged_keywords = []
    for kw in PROHIBITED_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", full_narrative):
            flagged_keywords.append(kw.title())

    is_biz_pass = (not sector_flagged) and (not industry_flagged) and (len(flagged_keywords) == 0)

    return {
        "sector": sector_name,
        "industry": industry_name,
        "about": about_text if about_text else "Not specified",
        "key_points": " ".join(key_points_list) if key_points_list else "No extra commentary listed",
        "sector_flagged": sector_flagged,
        "industry_flagged": industry_flagged,
        "flagged_keywords": sorted(list(set(flagged_keywords))),
        "is_biz_pass": is_biz_pass,
    }

def fetch_screener_page(ticker):
    session = requests.Session()
    session.headers.update(HEADERS)

    url_c = f"https://www.screener.in/company/{ticker}/consolidated/"
    url_s = f"https://www.screener.in/company/{ticker}/"

    r_c = session.get(url_c, timeout=12)
    soup_c = None
    if r_c.status_code == 200:
        parsed_c = BeautifulSoup(r_c.text, "html.parser")
        if parsed_c.find("section", {"id": "profit-loss"}):
            soup_c = parsed_c

    r_s = session.get(url_s, timeout=12)
    soup_s = None
    if r_s.status_code == 200:
        parsed_s = BeautifulSoup(r_s.text, "html.parser")
        if parsed_s.find("section", {"id": "profit-loss"}):
            soup_s = parsed_s

    primary_soup = soup_c if soup_c else soup_s
    if not primary_soup:
        return None, None, {}

    biz_info = extract_deep_business_and_keypoints(primary_soup)
    return soup_c, soup_s, biz_info

def parse_screener_table_exact(soup, section_id):
    if not soup:
        return {}, [], False
    sec = soup.find("section", {"id": section_id})
    if not sec:
        return {}, [], False
    table = sec.find("table")
    if not table:
        return {}, [], False

    headers = []
    thead = table.find("thead")
    if thead:
        headers = [th.text.strip() for th in thead.find_all("th") if th.text.strip()]

    has_ttm = any("ttm" in h.lower() for h in headers)
    grid = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue
        key = re.sub(r"[^\w\s]", "", tds[0].text).lower().replace("button", "").strip()
        vals = []
        for cell in tds[1:]:
            clean = re.sub(r"[^\d.-]", "", cell.text.replace(",", "")).strip()
            if clean and clean != "-":
                try:
                    vals.append(float(clean))
                except ValueError:
                    vals.append(None)
            else:
                vals.append(None)
        if key:
            grid[key] = vals

    return grid, headers, has_ttm

def get_excel_exact_row_value(grid, target_keys, has_ttm, is_pnl=False):
    for row_key, vals in grid.items():
        for t in target_keys:
            if t in row_key:
                valid_indexed = [(idx, v) for idx, v in enumerate(vals) if v is not None]
                if not valid_indexed:
                    return 0.0
                if is_pnl:
                    if has_ttm and len(valid_indexed) >= 2:
                        return valid_indexed[-2][1]
                    return valid_indexed[-1][1]
                else:
                    return valid_indexed[-1][1]
    return 0.0

def extract_latest_table_date(headers):
    for h in reversed(headers):
        if "ttm" not in h.lower() and re.search(r"\d{4}", h):
            return h, parse_screener_date(h)
    return "", datetime.min

def run_evaluation(ticker, inv_db):
    try:
        soup_c, soup_s, biz_info = fetch_screener_page(ticker)
    except Exception:
        return None

    if not soup_c and not soup_s:
        return None

    primary_soup = soup_c if soup_c else soup_s

    def safe_to_float(txt):
        if not txt:
            return 0.0
        clean = re.sub(r"[^\d.]", "", str(txt).replace(",", "")).strip()
        try:
            return float(clean) if clean and clean != "." else 0.0
        except ValueError:
            return 0.0

    # Market Cap & CMP extraction
    mcap = 0.0
    cmp_val = 0.0
    for s_candidate in [primary_soup, soup_s, soup_c]:
        if not s_candidate:
            continue
        top_ratios = s_candidate.find("ul", {"id": "top-ratios"})
        if top_ratios:
            for li in top_ratios.find_all("li"):
                val_span = li.find("span", {"class": "number"})
                txt = li.get_text(" ", strip=True)
                if "market cap" in txt.lower() and mcap == 0.0:
                    if val_span:
                        mcap = safe_to_float(val_span.text)
                    if mcap == 0.0:
                        mcap = safe_to_float(txt.split("Market Cap")[-1])
                if ("current price" in txt.lower() or "cmp" in txt.lower()) and cmp_val == 0.0:
                    if val_span:
                        cmp_val = safe_to_float(val_span.text)
                    if cmp_val == 0.0:
                        cmp_val = safe_to_float(txt.split("Current Price")[-1])
        if mcap > 0.0 and cmp_val > 0.0:
            break

    if mcap == 0.0:
        for s_candidate in [primary_soup, soup_s, soup_c]:
            if s_candidate:
                m_match = re.search(r"Market Cap.*?₹?\s*([\d,]+(?:\.\d+)?)", s_candidate.text, re.IGNORECASE)
                if m_match:
                    mcap = safe_to_float(m_match.group(1))
                    if mcap > 0.0:
                        break

    # P&L and Balance Sheet Parsing
    pnl_c, pnl_c_heads, pnl_c_ttm = parse_screener_table_exact(soup_c, "profit-loss")
    pnl_s, pnl_s_heads, pnl_s_ttm = parse_screener_table_exact(soup_s, "profit-loss")

    bs_c, bs_c_heads, _ = parse_screener_table_exact(soup_c, "balance-sheet")
    bs_s, bs_s_heads, _ = parse_screener_table_exact(soup_s, "balance-sheet")

    sales_c = get_excel_exact_row_value(pnl_c, ["sales", "revenue"], pnl_c_ttm, is_pnl=True)
    other_c = get_excel_exact_row_value(pnl_c, ["other income"], pnl_c_ttm, is_pnl=True)
    int_c = get_excel_exact_row_value(pnl_c, ["interest"], pnl_c_ttm, is_pnl=True)

    sales_s = get_excel_exact_row_value(pnl_s, ["sales", "revenue"], pnl_s_ttm, is_pnl=True)
    other_s = get_excel_exact_row_value(pnl_s, ["other income"], pnl_s_ttm, is_pnl=True)
    int_s = get_excel_exact_row_value(pnl_s, ["interest"], pnl_s_ttm, is_pnl=True)

    if sales_c > 0:
        sales, other_inc, interest = sales_c, other_c, int_c
        pnl_source = "Consolidated"
    else:
        sales, other_inc, interest = sales_s, other_s, int_s
        pnl_source = "Standalone"

    bs_c_dstr, bs_c_dt = extract_latest_table_date(bs_c_heads)
    bs_s_dstr, bs_s_dt = extract_latest_table_date(bs_s_heads)

    tot_assets_c = get_excel_exact_row_value(bs_c, ["total assets", "total asset"], False)
    tot_assets_s = get_excel_exact_row_value(bs_s, ["total assets", "total asset"], False)

    if bs_s_dt > bs_c_dt and tot_assets_s > 0:
        chosen_bs = bs_s
        bs_source = f"Standalone ({bs_s_dstr})"
    elif tot_assets_c > 0:
        chosen_bs = bs_c
        bs_source = f"Consolidated ({bs_c_dstr})"
    else:
        chosen_bs = bs_s
        bs_source = f"Standalone ({bs_s_dstr})"

    tot_assets = get_excel_exact_row_value(chosen_bs, ["total assets", "total asset"], False)
    fixed_assets = get_excel_exact_row_value(chosen_bs, ["fixed assets"], False)
    cwip = get_excel_exact_row_value(chosen_bs, ["cwip", "capital work in progress"], False)
    borrowings = get_excel_exact_row_value(chosen_bs, ["borrowings"], False)
    tot_liab = get_excel_exact_row_value(chosen_bs, ["total liabilities"], False)

    clean_sym = re.sub(r"[^\w\s]", "", ticker).strip().upper()
    inventory = inv_db.get(ticker.upper(), inv_db.get(clean_sym, 0.0))
    if inventory == 0.0:
        inventory = get_excel_exact_row_value(chosen_bs, ["inventory", "inventories", "stock"], False)

    # Shariah Ratios Math
    impermissible = interest if other_inc <= 0 else other_inc
    non_shariah_pct = (impermissible / sales * 100) if sales > 0 else 0.0
    debt_pct = (borrowings / tot_assets * 100) if tot_assets > 0 else 0.0
    illiquid_assets = fixed_assets + cwip + inventory
    illiquid_pct = (illiquid_assets / tot_assets * 100) if tot_assets > 0 else 0.0
    liquid_assets = tot_assets - illiquid_assets - tot_liab

    inc_pass = non_shariah_pct <= 5.0
    debt_pass = debt_pct <= 33.0
    illiquid_pass = illiquid_pct >= 20.0
    liquid_pass = liquid_assets < mcap

    blacklist_hit = BLACKLIST_REASONS.get(ticker.upper(), None)
    biz_pass = biz_info["is_biz_pass"] and (blacklist_hit is None)
    overall = all([biz_pass, inc_pass, debt_pass, illiquid_pass, liquid_pass])

    return {
        "ticker": ticker.upper(),
        "cmp": cmp_val,
        "mcap": mcap,
        "sector": biz_info["sector"],
        "industry": biz_info["industry"],
        "sector_flagged": biz_info["sector_flagged"],
        "industry_flagged": biz_info["industry_flagged"],
        "biz_pass": biz_pass,
        "blacklist_hit": blacklist_hit,
        "flagged_keywords": biz_info["flagged_keywords"],
        "non_shariah_pct": non_shariah_pct,
        "inc_pass": inc_pass,
        "debt_pct": debt_pct,
        "debt_pass": debt_pass,
        "illiquid_pct": illiquid_pct,
        "illiquid_pass": illiquid_pass,
        "liquid_assets": liquid_assets,
        "liquid_pass": liquid_pass,
        "inventory": inventory,
        "overall": overall,
        "pnl_source": pnl_source,
        "bs_source": bs_source,
    }

# =====================================================================
# 4. STREAMLIT VISUAL DASHBOARD
# =====================================================================
inv_db, suggestions_list = load_master_inventory_and_suggestions()

st.title("⚡ Shariah Equity Screener")

selected_stock_raw = st.selectbox(
    "Search Stock Name or Ticker (e.g. MARICO, BCLIND, KNACK, INGERRAND):",
    options=[""] + suggestions_list,
    format_func=lambda x: "🔍 Type to search stock..." if x == "" else x,
    index=0
)

target_ticker = ""
if selected_stock_raw:
    target_ticker = selected_stock_raw.split("—")[0].strip().upper()

if target_ticker:
    with st.spinner(f"Evaluating {target_ticker}..."):
        res = run_evaluation(target_ticker, inv_db)

    if not res:
        st.error(f"❌ Stock '{target_ticker}' Screener.in par nahi mila. Symbol check karein.")
    else:
        overall_badge = (
            "<span style='background:#16a34a; color:#fff; padding:5px 12px; border-radius:6px; font-weight:700;'>✅ SHARIAH COMPLIANT</span>"
            if res["overall"] else
            "<span style='background:#dc2626; color:#fff; padding:5px 12px; border-radius:6px; font-weight:700;'>❌ NON-COMPLIANT</span>"
        )
        biz_badge = (
            "<span style='color:#16a34a; font-weight:700;'>🟢 Pass</span>"
            if res["biz_pass"] else
            "<span style='color:#dc2626; font-weight:700;'>🔴 Fail</span>"
        )

        st.markdown(f"""
        <div class="meta-box">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                <div>
                    <span style="font-size:1.6rem; font-weight:800; margin-right:12px;">{res['ticker']}</span>
                    <span style="font-size:1.1rem; color:#475569; font-weight:600;">CMP: ₹{res['cmp']:,.0f}</span>
                    <span style="color:#94a3b8; margin: 0 8px;">|</span>
                    <span style="font-size:1.1rem; color:#475569; font-weight:600;">MCAP: ₹{int(res['mcap']):,} Cr</span>
                </div>
                <div>{overall_badge}</div>
            </div>
            <div style="margin-top:10px; font-size:0.95rem; color:#334155;">
                <b>Sector:</b> {res['sector']} &nbsp;|&nbsp; 
                <b>Industry:</b> {res['industry']} &nbsp;|&nbsp; 
                <b>Business Activity:</b> {biz_badge} &nbsp;|&nbsp;
                <b>Inventories:</b> ₹{res['inventory']:,.1f} Cr
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Flagged Words & Sector Reasons Display
        if not res["biz_pass"]:
            reasons = []
            if res["blacklist_hit"]:
                reasons.append(f"<b>Blacklist:</b> {res['blacklist_hit']}")
            if res["sector_flagged"]:
                reasons.append(f"<b>Prohibited Sector:</b> {res['sector']}")
            if res["industry_flagged"]:
                reasons.append(f"<b>Prohibited Industry:</b> {res['industry']}")
            if res["flagged_keywords"]:
                reasons.append(f"<b>Flagged Words:</b> {', '.join(res['flagged_keywords'])}")
            
            st.error("⚠️ Business Activity Failed: " + " | ".join(reasons))

        # Four Ratio Value Cards with Dynamic Green/Red Background
        c1, c2, c3, c4 = st.columns(4)

        with c1:
            bg_cls = "ratio-card-pass" if res["inc_pass"] else "ratio-card-fail"
            st.markdown(f"""
            <div class="{bg_cls}">
                <div class="card-title">Non-Sharia Income</div>
                <div class="card-value">{res['non_shariah_pct']:.2f}%</div>
                <div class="card-limit">Limit: ≤ 5.0%</div>
            </div>
            """, unsafe_allow_html=True)

        with c2:
            bg_cls = "ratio-card-pass" if res["debt_pass"] else "ratio-card-fail"
            st.markdown(f"""
            <div class="{bg_cls}">
                <div class="card-title">Debt / Total Assets</div>
                <div class="card-value">{res['debt_pct']:.2f}%</div>
                <div class="card-limit">Limit: ≤ 33.0%</div>
            </div>
            """, unsafe_allow_html=True)

        with c3:
            bg_cls = "ratio-card-pass" if res["illiquid_pass"] else "ratio-card-fail"
            st.markdown(f"""
            <div class="{bg_cls}">
                <div class="card-title">Illiquid Asset Ratio</div>
                <div class="card-value">{res['illiquid_pct']:.2f}%</div>
                <div class="card-limit">Limit: ≥ 20.0%</div>
            </div>
            """, unsafe_allow_html=True)

        with c4:
            bg_cls = "ratio-card-pass" if res["liquid_pass"] else "ratio-card-fail"
            st.markdown(f"""
            <div class="{bg_cls}">
                <div class="card-title">Liquid Assets</div>
                <div class="card-value">₹{int(res['liquid_assets']):,} Cr</div>
                <div class="card-limit">&lt; MCAP (₹{int(res['mcap']):,} Cr)</div>
            </div>
            """, unsafe_allow_html=True)

        st.caption(f"Financial Statements Used: P&L: {res['pnl_source']} | Balance Sheet: {res['bs_source']}")