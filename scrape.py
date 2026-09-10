#!/usr/bin/env python3
"""Scrape Thai gold / fuel / air-quality and write one clean data.json.

Runs in GitHub Actions every 15 min. Each source is independent: if one
fails, the previous value is kept and noted in `errors` so the ESP32 keeps
showing something. No third-party libraries (stdlib only).
"""
import json, re, ssl, sys, urllib.request, html
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_NOVERIFY = ssl._create_unverified_context()


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "th,en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:                       # local cert store gaps - public price data
        if "CERTIFICATE" not in str(e):
            raise
        with urllib.request.urlopen(req, timeout=timeout, context=_NOVERIFY) as r:
            return r.read().decode("utf-8", "replace")


def num(s):
    s = re.sub(r"[^\d.\-]", "", s or "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


# ---- gold : classic.goldtraders.or.th -------------------------------
# UpdatePriceList.aspx (intraday-change page) died 2026-09-10 -> "ไม่พบข้อมูล".
# The home page server-renders the current GTA announcement in lbl* spans;
# spot comes from the foreign-market tab, fx from a forex API, and `change`
# is computed in main() by diffing bar_sell against the previous run.
def _span(t, id_sub):
    m = re.search(r'id="[^"]*' + re.escape(id_sub) + r'[^"]*"[^>]*>(.*?)</span>', t, re.S)
    if not m:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()


def scrape_gold():
    t = get("https://classic.goldtraders.or.th/")
    bar_buy  = num(_span(t, "lblBLBuy"))
    bar_sell = num(_span(t, "lblBLSell"))
    orn_buy  = num(_span(t, "lblOMBuy"))
    orn_sell = num(_span(t, "lblOMSell"))
    if None in (bar_buy, bar_sell, orn_sell):
        raise ValueError("gold spans not found / unexpected shape")

    # "10/09/2569 เวลา 09:04 น. (ครั้งที่ 1)"
    astime = _span(t, "lblAsTime") or ""
    dt = re.search(r"(\d{2}/\d{2}/\d{4})\D+(\d{2}:\d{2})", astime)
    rnd = re.search(r"ครั้งที่\s*(\d+)", astime)

    spot = num(_span(t, "lblLDPClose")) or num(_span(t, "lblNYClose"))  # USD/oz, foreign tab

    fx = None
    try:
        fx = round(float(json.loads(get("https://open.er-api.com/v6/latest/USD"))
                         ["rates"]["THB"]), 2)
    except Exception:
        pass

    return {
        "bar_buy": bar_buy, "bar_sell": bar_sell,
        "orn_buy": orn_buy, "orn_sell": orn_sell,
        "spot": spot, "fx": fx,
        "change": None,                       # filled by main() vs previous bar_sell
        "round": (rnd.group(1) if rnd else ""),
        "time": (f"{dt.group(1)} {dt.group(2)}" if dt else astime),
    }


# ---- fuel : motorist.co.th ------------------------------------------
FUEL_GRADES = {
    "g95":    r"แก๊สโซฮอล 95",
    "e20":    r"E20",
    "e85":    r"E85",
    "g91":    r"แก๊สโซฮอล 91",
    "b95":    r"เบนซิน 95",
    "diesel": r"ดีเซล B7",
}


def scrape_fuel():
    t = get("https://www.motorist.co.th/petrol-prices")
    out = {}
    for key, name in FUEL_GRADES.items():
        # grade name in a <td>, then the first ฿price (cheapest station column)
        m = re.search(re.escape(name) + r"\s*</td>((?:\s*<td[^>]*>[^<]*</td>){1,10})", t)
        if not m:
            continue
        price = re.search(r"฿\s*([\d.,]+)", m.group(1))
        if price:
            out[key] = num(price.group(1))
    if "g95" not in out and "diesel" not in out:
        raise ValueError("fuel table not found")
    return out


# ---- air : air4thai.pcd.go.th (Udon Thani, station 91t) -------------
def scrape_air():
    d = json.loads(get("https://air4thai.pcd.go.th/services/getNewAQI_JSON.php"))
    for s in d["stations"]:
        if s["stationID"] == "91t":
            a = s["AQILast"]
            pm = a["PM25"]["value"]
            if pm in ("-1", "-", None):
                raise ValueError("station 91t has no PM2.5")
            return {
                "pm25":    num(pm),
                "aqi":     int(a["PM25"]["aqi"]) if a["PM25"]["aqi"] not in ("-1", "-999") else None,
                "station": "Udon Thani (Nong Prajak)",
                "time":    f"{a['date']} {a['time']}",
            }
    raise ValueError("station 91t not in feed")


# ---- assemble ------------------------------------------------------
def main():
    try:
        prev = json.load(open("data.json"))
    except Exception:
        prev = {}

    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "errors": {}}

    for key, fn in (("gold", scrape_gold), ("fuel", scrape_fuel), ("air", scrape_air)):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = prev.get(key)                 # keep last known
            out["errors"][key] = str(e)[:200]
            print(f"[{key}] FAILED: {e}", file=sys.stderr)
        else:
            print(f"[{key}] ok: {out[key]}")

    # gold `change` = today's bar_sell - yesterday's closing bar_sell.
    # `day_ref` carries yesterday's close forward; it rolls over when the date changes.
    g, pg = out.get("gold"), prev.get("gold") or {}
    if g and g.get("bar_sell") is not None:
        p_date = (pg.get("time") or "")[:10]
        g_date = (g.get("time") or "")[:10]
        if g_date and p_date and g_date != p_date:
            day_ref = pg.get("bar_sell")             # new day -> anchor on last close
        else:
            day_ref = pg.get("day_ref", pg.get("bar_sell"))
        g["day_ref"] = day_ref
        g["change"] = round(g["bar_sell"] - day_ref, 2) if day_ref is not None else 0.0

    json.dump(out, open("data.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print("wrote data.json", len(json.dumps(out)), "bytes")
    if out["errors"]:
        print("errors:", out["errors"], file=sys.stderr)


if __name__ == "__main__":
    main()
