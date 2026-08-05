#!/usr/bin/env python3
"""
compute_per_trade_reversal.py

Computes a per-trade (not per-segment) local reversal density: for each
trade, looks at a fixed window of OHLCV bars immediately BEFORE its real
entry time, and runs the exact same zigzag_reversals() function from
map_segment_features.py (imported directly, not reimplemented, so results
are guaranteed consistent with the segment-level feature already computed).

Writes a new parquet (does not overwrite the existing segment-level one).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from map_segment_features import zigzag_reversals, load_ohlcv  # real, existing functions


def compute_pretrade_density(ohlcv, entry_ts, window_bars, threshold_pct):
    """Real reversal density in the window_bars immediately before entry_ts."""
    idx = ohlcv["timestamp"].searchsorted(entry_ts, side="right") - 1
    start_idx = max(0, idx - window_bars + 1)
    window = ohlcv.iloc[start_idx:idx + 1]
    if len(window) < 3:
        return np.nan
    prices = window["close"].values
    reversals, _, _ = zigzag_reversals(prices, threshold_pct)
    return reversals / len(prices)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True, help="Path to the segment-level parquet from map_segment_features.py")
    parser.add_argument("--ohlcv", required=True)
    parser.add_argument("--window", type=int, default=40, help="Bars before entry to look at (real, 15m bars)")
    parser.add_argument("--threshold", type=float, default=1.0, help="Reversal threshold pct -- must match a threshold present in --features")
    parser.add_argument("--out", default="/tmp/hbar_segment_trades_pertrade.parquet")
    args = parser.parse_args()

    print(f"Loading segment-level features from {args.features}")
    df = pd.read_parquet(args.features)
    df = df[df["threshold"] == args.threshold].copy()
    print(f"  {len(df)} real trades at threshold={args.threshold}")

    print(f"Loading OHLCV from {args.ohlcv}")
    ohlcv = load_ohlcv(args.ohlcv)
    print(f"  {len(ohlcv)} real bars loaded")

    print(f"Computing real per-trade pre-entry reversal density (window={args.window} bars)")
    df["pretrade_reversal_density"] = df["entry_ts"].apply(
        lambda ts: compute_pretrade_density(ohlcv, ts, args.window, args.threshold)
    )

    n_valid = df["pretrade_reversal_density"].notna().sum()
    print(f"  Computed for {n_valid}/{len(df)} real trades")

    df.to_parquet(args.out, index=False)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
