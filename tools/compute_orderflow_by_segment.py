#!/usr/bin/env python3
"""
compute_orderflow_by_segment.py

Real OHLCV-derived volume/liquidity proxy features per segment (no
orderbook/tick data exists on this host -- confirmed via direct
filesystem check before building this). Computes:
- total volume, bar count
- normalized volume z-score vs the full-series baseline
- VWAP shift (segment VWAP vs the immediately-preceding same-length window)
- realized volatility (std of log returns)
- (high-low)/close as a coarse spread/range proxy
"""
import argparse
import json
import numpy as np
import pandas as pd
from pathlib import Path


def load_ohlcv(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"])
    df["ret"] = np.log(df["close"]).diff()
    return df


def load_segments(path):
    with open(path) as f:
        segs = json.load(f)
    for s in segs:
        s["start"] = pd.to_datetime(s["start"])
        s["end"] = pd.to_datetime(s["end"])
    return segs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--features", required=True)
    p.add_argument("--ohlcv", required=True)
    p.add_argument("--segments", required=True)
    p.add_argument("--out", default="reports/orderflow_by_segment.csv")
    p.add_argument("--out-dir", default="reports")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ohlcv = load_ohlcv(args.ohlcv)
    segments = load_segments(args.segments)
    global_vol_mean = ohlcv["volume"].mean()
    global_vol_std = ohlcv["volume"].std()

    rows = []
    for seg in segments:
        mask = (ohlcv["timestamp"] >= seg["start"]) & (ohlcv["timestamp"] <= seg["end"])
        seg_bars = ohlcv[mask]
        n_bars = len(seg_bars)
        if n_bars == 0:
            continue

        # Preceding window of the same length, for the VWAP-shift comparison
        seg_len = seg["end"] - seg["start"]
        prior_mask = (ohlcv["timestamp"] >= seg["start"] - seg_len) & (ohlcv["timestamp"] < seg["start"])
        prior_bars = ohlcv[prior_mask]

        total_volume = seg_bars["volume"].sum()
        vol_zscore = (seg_bars["volume"].mean() - global_vol_mean) / global_vol_std if global_vol_std > 0 else np.nan

        seg_vwap = (seg_bars["close"] * seg_bars["volume"]).sum() / total_volume if total_volume > 0 else np.nan
        if len(prior_bars) > 0 and prior_bars["volume"].sum() > 0:
            prior_vwap = (prior_bars["close"] * prior_bars["volume"]).sum() / prior_bars["volume"].sum()
            vwap_shift = (seg_vwap - prior_vwap) / prior_vwap
        else:
            vwap_shift = np.nan

        realized_vol = seg_bars["ret"].std()
        range_proxy = ((seg_bars["high"] - seg_bars["low"]) / seg_bars["close"]).mean()

        rows.append({
            "segment_id": seg["id"],
            "n_bars": n_bars,
            "total_volume": total_volume,
            "mean_volume_zscore": vol_zscore,
            "vwap_shift": vwap_shift,
            "realized_volatility": realized_vol,
            "range_proxy": range_proxy,
        })

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")
    print(df.to_string(index=False))

    # Merge onto the per-trade features file so each trade carries its
    # segment's real orderflow-proxy values, for the regression test.
    feats = pd.read_parquet(args.features)
    merged = feats.merge(df, on="segment_id", how="left")
    merged_path = out_dir / "hbar_trades_with_orderflow.parquet"
    merged.to_parquet(merged_path, index=False)
    print(f"Wrote {merged_path}")


if __name__ == "__main__":
    main()
