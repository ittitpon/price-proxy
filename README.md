# price-proxy

GitHub Action that scrapes Thai **gold / fuel / PM2.5** every 15 min and commits a
single clean `data.json`. The ESP32 (`claude_limits`) fetches that one file instead
of parsing 3 fragile HTML pages itself.

## data.json

```json
{
  "updated": "2026-09-09T15:40:00Z",
  "errors": {},
  "gold": {"bar_buy":68300,"bar_sell":68500,"orn_buy":66931.4,"orn_sell":69300,
           "spot":4406,"fx":32.85,"change":-50,"round":"18","time":"09/09/2569 15:16"},
  "fuel": {"g95":39.09,"e20":34.09,"e85":30.03,"g91":38.72,"b95":48.08,"diesel":39.84},
  "air":  {"pm25":9.3,"aqi":16,"station":"Udon Thani (Nong Prajak)","time":"2026-09-09 15:00"}
}
```

Each source is independent. If one fails to scrape, the previous value is kept and
the reason goes in `errors` — the ESP32 keeps showing something and you get a log.

## Sources

| key | site | note |
|---|---|---|
| gold | `classic.goldtraders.or.th/UpdatePriceList.aspx` | GTA official, `<td>` table |
| fuel | `motorist.co.th/petrol-prices` | Bangkok, cheapest station per grade |
| air  | `air4thai.pcd.go.th/services/getNewAQI_JSON.php` | station `91t` = Nong Prajak Park, Udon Thani |

## Setup (once)

```
gh auth login
cd ~/Desktop/esp32/price-proxy
git init && git add -A && git commit -m "init"
gh repo create price-proxy --public --source=. --push
```

Then the Action runs on its own every 15 min. The ESP32 reads:
```
https://raw.githubusercontent.com/<you>/price-proxy/main/data.json
```

## When a site changes its HTML

Edit `scrape.py` (regex / cell index), commit, push. **No ESP32 reflash.**
Check `errors` in the latest `data.json` or the Actions log to see what broke.

## Local test

```
python3 scrape.py      # writes data.json, prints each source
```
