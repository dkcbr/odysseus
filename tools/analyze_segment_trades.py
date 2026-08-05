#!/usr/bin/env python3
"""
Analyze trades with attached segment features and produce a short Markdown report.

Inputs:
- Parquet file produced by map_segment_features.py
- --threshold: which threshold row to analyze (e.g. 1.0)

Outputs:
- Markdown report with per-segment table, contingency, bootstrap CIs,
  Spearman correlation, optional logistic regression, and Mann-Whitney test.

scipy and statsmodels are optional; the script falls back gracefully if absent.
"""

import argparse
import pandas as pd
import numpy as np
from pathlib import Path


def fisher_exact_table(a, b, c, d):
    try:
        from scipy.stats import fisher_exact
        oddsratio, p = fisher_exact([[a, b], [c, d]])
        return p, oddsratio
    except Exception:
        return None, None


def bootstrap_diff_proportion_from_rows(df_low, df_high, n_iter=5000, seed=42):
    """
    Resample actual trade rows with replacement.
    Returns 95% CI and median for (win_rate_low - win_rate_high).
    """
    rng = np.random.default_rng(seed)
    diffs = []
    n_low = len(df_low)
    n_high = len(df_high)
    for _ in range(n_iter):
        s_low = df_low.sample(n=n_low, replace=True,
                              random_state=int(rng.integers(0, 2**31 - 1)))
        s_high = df_high.sample(n=n_high, replace=True,
                                random_state=int(rng.integers(0, 2**31 - 1)))
        p_low = s_low['is_win'].mean() if n_low > 0 else 0.0
        p_high = s_high['is_win'].mean() if n_high > 0 else 0.0
        diffs.append(p_low - p_high)
    arr = np.array(diffs)
    return np.percentile(arr, [2.5, 50, 97.5])


def mann_whitney_u(a_vals, b_vals):
    try:
        from scipy.stats import mannwhitneyu
        stat, p = mannwhitneyu(a_vals, b_vals, alternative='two-sided')
        return p, stat
    except Exception:
        return None, None


def spearman_corr(x, y):
    try:
        from scipy.stats import spearmanr
        r, p = spearmanr(x, y, nan_policy='omit')
        return r, p
    except Exception:
        return None, None


def logistic_regression(df):
    """
    Returns (model_or_None, status_string).
    status: 'ok', 'perfect_separation', 'failed', 'unavailable'
    """
    try:
        import statsmodels.api as sm
        from statsmodels.tools.sm_exceptions import PerfectSeparationError
        X = df[['reversal_density']].fillna(0)
        X = sm.add_constant(X)
        y = df['is_win']
        try:
            model = sm.Logit(y, X).fit(disp=0)
            return model, 'ok'
        except PerfectSeparationError:
            return None, 'perfect_separation'
        except Exception:
            return None, 'failed'
    except ImportError:
        return None, 'unavailable'


def produce_report(df, threshold, outpath):
    df_thr = df[df['threshold'] == threshold].copy()
    df_thr = df_thr[~df_thr['segment_id'].isna()]

    if len(df_thr) == 0:
        raise RuntimeError(f"No trades found for threshold={threshold}. "
                           f"Available thresholds: {sorted(df['threshold'].unique())}")

    # Resolve win/loss column
    if 'outcome' in df_thr.columns:
        df_thr['is_win'] = df_thr['outcome'].apply(
            lambda x: 1 if str(x).lower().startswith('w') else 0)
    elif 'pnl' in df_thr.columns:
        df_thr['is_win'] = (df_thr['pnl'] > 0).astype(int)
    else:
        raise RuntimeError("No 'outcome' or 'pnl' column found in the features file.")

    # Per-segment summary (no ternary in agg)
    seg_summary = df_thr.groupby('segment_id').agg(
        reversal_density=('reversal_density', 'first'),
        trades_count=('is_win', 'count'),
        wins=('is_win', 'sum'),
    ).reset_index()

    if 'pnl' in df_thr.columns:
        mean_pnl = df_thr.groupby('segment_id')['pnl'].mean().reset_index(name='mean_pnl')
        seg_summary = seg_summary.merge(mean_pnl, on='segment_id', how='left')
    else:
        seg_summary['mean_pnl'] = np.nan

    # Median split on reversal_density
    median_density = seg_summary['reversal_density'].median()
    df_thr['density_group'] = df_thr['reversal_density'].apply(
        lambda x: 'low' if x <= median_density else 'high')

    low = df_thr[df_thr['density_group'] == 'low']
    high = df_thr[df_thr['density_group'] == 'high']
    a = int(low['is_win'].sum())
    b = int(len(low) - a)
    c = int(high['is_win'].sum())
    d = int(len(high) - c)

    fisher_p, odds = fisher_exact_table(a, b, c, d)
    boot_ci = bootstrap_diff_proportion_from_rows(low, high, n_iter=5000)

    if 'pnl' in df_thr.columns:
        p_mw, _ = mann_whitney_u(low['pnl'].values, high['pnl'].values)
    else:
        p_mw = None

    r_spear, p_spear = spearman_corr(df_thr['reversal_density'], df_thr['is_win'])
    logit_model, logit_status = logistic_regression(df_thr)

    # Write report
    with open(outpath, 'w') as f:
        f.write("# Intra-Segment Trade Analysis\n\n")
        f.write(f"**Reversal threshold analyzed**: {threshold}%  \n")
        f.write(f"**Median split density cutoff**: {median_density:.4f}  \n")
        f.write(f"**Total trades in segments**: {len(df_thr)}\n\n")

        f.write("## Per-segment summary\n\n")
        f.write("| segment_id | reversal_density | trades | wins | win_rate | mean_pnl |\n")
        f.write("|---|---:|---:|---:|---:|---:|\n")
        for _, row in seg_summary.iterrows():
            wr = row['wins'] / row['trades_count'] if row['trades_count'] > 0 else float('nan')
            mpnl = f"{row['mean_pnl']:.6g}" if not pd.isna(row['mean_pnl']) else "NA"
            f.write(f"| {int(row['segment_id'])} | {row['reversal_density']:.4f} | "
                    f"{int(row['trades_count'])} | {int(row['wins'])} | {wr:.3f} | {mpnl} |\n")
        f.write("\n")

        f.write("## Median-split contingency table\n\n")
        f.write(f"- **Low-density** trades (density ≤ {median_density:.4f}): "
                f"{len(low)} total, {a} wins ({a/len(low):.1%} win rate)\n")
        f.write(f"- **High-density** trades (density > {median_density:.4f}): "
                f"{len(high)} total, {c} wins ({c/len(high):.1%} win rate)\n\n")
        f.write("| group | wins | losses | total |\n")
        f.write("|---|---:|---:|---:|\n")
        f.write(f"| low density  | {a} | {b} | {len(low)} |\n")
        f.write(f"| high density | {c} | {d} | {len(high)} |\n\n")

        if fisher_p is not None:
            f.write(f"Fisher exact p-value: **{fisher_p:.4g}**, odds ratio: {odds:.3g}\n\n")
        else:
            f.write("Fisher exact test unavailable (scipy not installed). See bootstrap below.\n\n")

        f.write("## Bootstrap win-rate difference (low − high)\n\n")
        f.write(f"95% CI: [{boot_ci[0]:.4f}, {boot_ci[2]:.4f}]; "
                f"median diff = {boot_ci[1]:.4f}\n\n")

        f.write("## Continuous analysis\n\n")
        if r_spear is not None:
            f.write(f"Spearman ρ (reversal_density vs is_win): "
                    f"r = {r_spear:.4f}, p = {p_spear:.4g}\n\n")
        else:
            f.write("Spearman correlation unavailable (scipy not installed).\n\n")

        if logit_model is not None:
            f.write("### Logistic regression: is_win ~ reversal_density\n\n")
            f.write("```\n")
            f.write(str(logit_model.summary()))
            f.write("\n```\n\n")
        elif logit_status == 'perfect_separation':
            f.write("Logistic regression: **perfect separation detected** — "
                    "the predictor perfectly predicts outcomes in this sample. "
                    "Coefficients are unreliable; interpret bootstrap and Fisher results instead.\n\n")
        elif logit_status == 'unavailable':
            f.write("Logistic regression unavailable (statsmodels not installed).\n\n")
        else:
            f.write(f"Logistic regression failed (status: {logit_status}).\n\n")

        if p_mw is not None:
            f.write(f"Mann-Whitney U (pnl distributions, low vs high): p = {p_mw:.4g}\n\n")
        else:
            f.write("Mann-Whitney test unavailable (scipy not installed or no pnl column).\n\n")

        f.write("## Notes and caveats\n\n")
        f.write("- Small sample sizes per segment; treat all results as descriptive "
                "and hypothesis-generating, not confirmatory.\n")
        f.write("- Median split with n=5 segments puts 2 segments in one group and 3 in the other "
                "(or similar). Consider continuous analysis (Spearman, logistic) as primary.\n")
        f.write("- Re-run across multiple thresholds (0.5%, 1.0%, 2.0%) and compare stability.\n")
        f.write("- Segment boundaries and per-segment densities are recorded above for reproducibility.\n")

    print(f"Wrote report to {outpath}")


def main():
    p = argparse.ArgumentParser(description="Analyze segment-attached trades and produce a report.")
    p.add_argument('--features', required=True, help="Parquet from map_segment_features.py")
    p.add_argument('--threshold', type=float, required=True,
                   help="Reversal threshold to analyze (e.g. 1.0)")
    p.add_argument('--out', required=True, help="Output Markdown report path")
    args = p.parse_args()

    df = pd.read_parquet(args.features)
    produce_report(df, args.threshold, args.out)


if __name__ == '__main__':
    main()
