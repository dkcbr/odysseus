#!/usr/bin/env python3
"""
run_orderflow_tests.py

IMPORTANT, real limitation: orderflow_by_segment.csv features are
segment-level (constant within each segment), so a within-segment
Mann-Whitney/regression on win vs loss is not meaningful for them (no
within-group variance). What CAN honestly be looked at is whether these
segment-level features correlate with segment-level win rate ACROSS
the 5 segments -- but n=5 is far too small for a real significance
test. This script reports that correlation descriptively, clearly
labeled as non-significant-testable given the sample size, rather than
running a formal test that would overstate confidence.
"""
import argparse
import pandas as pd
import numpy as np
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--orderflow", required=True)
    p.add_argument("--features", required=True)
    p.add_argument("--out-dir", default="reports")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    orderflow = pd.read_csv(args.orderflow)
    trades = pd.read_parquet(args.features)
    trades["is_win"] = trades["profit"] > 0

    win_rate = trades.groupby("segment_id")["is_win"].mean().reset_index()
    win_rate.columns = ["segment_id", "win_rate"]

    merged = orderflow.merge(win_rate, on="segment_id")
    print(f"n={len(merged)} segments -- too small for a formal significance test; descriptive only\n")
    print(merged.to_string(index=False))

    feature_cols = ["mean_volume_zscore", "vwap_shift", "realized_volatility", "range_proxy"]
    corrs = {}
    for col in feature_cols:
        valid = merged[[col, "win_rate"]].dropna()
        if len(valid) >= 3:
            corrs[col] = valid[col].corr(valid["win_rate"])
        else:
            corrs[col] = None

    print("\nDescriptive correlation with segment-level win rate (n=5, NOT a formal test):")
    for k, v in corrs.items():
        print(f"  {k}: {v:.3f}" if v is not None else f"  {k}: insufficient data")

    out_path = out_dir / "orderflow_winrate_correlation.csv"
    pd.DataFrame([corrs]).to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
