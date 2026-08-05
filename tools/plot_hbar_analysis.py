#!/usr/bin/env python3
"""
plot_hbar_analysis.py

Loads the already-computed segment-features parquet from
map_segment_features.py (NOT raw trades directly -- that script already
does the real zigzag reversal computation and trade-to-segment mapping;
duplicating that logic separately would risk inconsistent results).

Produces:
- reports/summary_by_segment.csv
- reports/figs/violin_by_segment.png
- reports/figs/density_scatter.png
- reports/figs/entry_timing.png

Usage:
  python3 tools/plot_hbar_analysis.py --input /tmp/hbar_segment_trades.parquet
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import mannwhitneyu


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/tmp/hbar_segment_trades.parquet",
                         help="Path to the parquet output from map_segment_features.py")
    parser.add_argument("--threshold", type=float, default=1.0,
                         help="Which reversal threshold to plot (the parquet has multiple per trade)")
    parser.add_argument("--out-dir", default="reports", help="output directory for CSV and figs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    figs_dir = out_dir / "figs"
    figs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.input}")
    df = pd.read_parquet(args.input)
    print(f"  {len(df)} rows loaded (trades x thresholds)")

    df = df[df["threshold"] == args.threshold].copy()
    print(f"  {len(df)} rows at threshold={args.threshold}")

    df["is_win"] = df["profit"] > 0
    df["entry_pos_in_segment"] = (
        (df["entry_ts"] - df["seg_start"]) / (df["seg_end"] - df["seg_start"])
    )

    # Real, honest summary per segment
    summary_rows = []
    for seg_id, g in df.groupby("segment_id"):
        wins = g[g["is_win"]]["reversal_density"].dropna()
        losses = g[~g["is_win"]]["reversal_density"].dropna()
        pval = np.nan
        if len(wins) >= 5 and len(losses) >= 5:
            _, pval = mannwhitneyu(wins, losses, alternative="two-sided")
        summary_rows.append({
            "segment_id": seg_id,
            "n_trades": len(g),
            "win_rate": g["is_win"].mean(),
            "median_reversal_density_win": wins.median() if len(wins) else None,
            "median_reversal_density_loss": losses.median() if len(losses) else None,
            "mannwhitney_p": pval if not np.isnan(pval) else None,
        })
    summary_df = pd.DataFrame.from_records(summary_rows)
    summary_path = out_dir / "summary_by_segment.csv"
    summary_df.to_csv(summary_path, index=False)
    print(f"Wrote {summary_path}")
    print(summary_df.to_string(index=False))

    sns.set_style("whitegrid")

    # 1. Violin: reversal density by segment and outcome
    plt.figure(figsize=(10, 6))
    plot_df = df.dropna(subset=["reversal_density"]).copy()
    plot_df["segment_id"] = plot_df["segment_id"].astype(str)
    sns.violinplot(x="segment_id", y="reversal_density", hue="is_win", data=plot_df,
                   split=True, inner="quart")
    plt.title(f"Reversal density by segment and outcome (threshold={args.threshold}%)")
    plt.xlabel("Segment")
    plt.ylabel("Reversal density (reversals/bar)")
    violin_path = figs_dir / "violin_by_segment.png"
    plt.savefig(violin_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Wrote {violin_path}")

    # 2. Global density vs outcome boxplot
    plt.figure(figsize=(8, 6))
    sns.boxplot(x="is_win", y="reversal_density", data=plot_df)
    plt.title("Reversal density by outcome (global, all segments)")
    plt.xlabel("is_win")
    plt.ylabel("Reversal density")
    scatter_path = figs_dir / "density_scatter.png"
    plt.savefig(scatter_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Wrote {scatter_path}")

    # 3. Entry timing within segment, by outcome
    plt.figure(figsize=(8, 6))
    timing_df = df.dropna(subset=["entry_pos_in_segment"])
    sns.kdeplot(data=timing_df, x="entry_pos_in_segment", hue="is_win", common_norm=False)
    plt.title("Entry position within segment, by outcome")
    plt.xlabel("Normalized entry position (0=segment start, 1=segment end)")
    timing_path = figs_dir / "entry_timing.png"
    plt.savefig(timing_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Wrote {timing_path}")

    print("Done.")


if __name__ == "__main__":
    main()
