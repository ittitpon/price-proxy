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
def scrape_gold():
    t = get("https://classic.goldtraders.or.th/UpdatePriceList.aspx")
    m = re.search(r"UpdatePriceListUpdatePanel.*?</table>", t, re.S)
    seg = m.group(0) if m else t
    cells = re.findall(r"<td[^>]*>(.*?)</td>", seg, re.S)
    cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip() for c in cells]
    cells = [c for c in cells if c]
    # [0..2 headers, 3 datetime, 4 round, 5 barBuy, 6 barSell, 7 ornBuy, 8 ornSell, 9 spot, 10 fx, 11 change]
    if len(cells) < 12 or "," not in cells[5]:
        raise ValueError("gold table not found / unexpected shape")
    return {
        "bar_buy":  num(cells[5]),  "bar_sell": num(cells[6]),
        "orn_buy":  num(cells[7]),  "orn_sell": num(cells[8]),
        "spot":     num(cells[9]),  "fx":       num(cells[10]),
        "change":   num(cells[11]),
        "round":    cells[4],       "time":     cells[3],
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

    json.dump(out, open("data.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print("wrote data.json", len(json.dumps(out)), "bytes")
    if out["errors"]:
        print("errors:", out["errors"], file=sys.stderr)


if __name__ == "__main__":
    main()
