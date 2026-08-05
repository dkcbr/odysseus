#!/usr/bin/env python3
"""
Fetch HBAR trades from trader-dev API and write to CSV.
Usage: python3 fetch_trades.py <your_pk_key>
"""
import sys, os, csv, json
import urllib.request
import urllib.error

OUT_CSV = "/home/dk/jarvis/projects/odysseus/data/trades/hbar_trades.csv"
BASE = "https://mcp-api.trader.dev"

def get(url, key):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_trades.py <pk_...key>")
        sys.exit(1)
    key = sys.argv[1]

    print("Listing strategies...")
    strategies = get(f"{BASE}/strategies", key)
    if not strategies:
        print("No strategies found.")
        sys.exit(1)

    # Find HBAR strategy
    hbar = next((s for s in strategies
                 if "hbar" in s.get("name","").lower()
                 or "hbarusdt" in str(s).lower()), None)
    if not hbar:
        print("No HBAR strategy found. Available:")
        for s in strategies:
            print(f"  {s.get('id')} — {s.get('name')}")
        sys.exit(1)

    sid = hbar["id"]
    print(f"Using strategy: {hbar.get('name')} ({sid})")

    print("Listing backtests...")
    backtests = get(f"{BASE}/strategies/{sid}/backtests", key)
    if not backtests:
        print("No backtests found.")
        sys.exit(1)

    # Most recent
    bt = sorted(backtests, key=lambda b: b.get("created_at",""), reverse=True)[0]
    bid = bt["id"]
    print(f"Using backtest: {bid} (created {bt.get('created_at','')})")

    print("Fetching trades...")
    trades = get(f"{BASE}/backtests/{bid}/trades", key)
    if not trades:
        print("No trades returned.")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    keys = list(trades[0].keys())
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for t in trades:
            w.writerow([t.get(k, "") for k in keys])

    print(f"Wrote {len(trades)} trades to {OUT_CSV}")
    print("Header:", ",".join(keys))

if __name__ == "__main__":
    main()
