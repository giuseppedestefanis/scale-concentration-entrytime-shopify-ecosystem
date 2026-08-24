"""Step 07 — deprecation deep-dive: evaporation, not redistribution.

Separates event-attributable switching from pre-existing trends around
the Product Reviews shutdown (2024-05-06), and measures how much of the
platform application's lost installed base reappears as competitor
adoption. Also computes leadership turnover across categories as
context for contestability.

Key quantities:
  gross:   competitor install gains over 52w post-shutdown
  excess:  post-window growth minus each app's own 52w pre-window growth
  recovery share: total positive excess / platform app's gross loss

Reads:  data/derived/panel_weekly.parquet, app_master.parquet
Writes: results/07_exit_redistribution.txt
        data/derived/leadership_turnover.parquet
"""
import numpy as np
import pandas as pd

from common import DERIVED, RESULTS

SHUTDOWN = pd.Timestamp("2024-05-06")
WINDOW_W = 52
MIN_LEADER_STREAK = 8   # a new leader must persist >= 8 weeks to count

lines = ["Exit redistribution and leadership turnover (scripts/07_exit_redistribution.py)", ""]


def redistribution(panel):
    pr = panel[panel["category"] == "product reviews"]
    piv = pr.pivot_table(index="week_date", columns="app_handle", values="install_count")

    def nearest(ts):
        return piv.index[piv.index.get_indexer([ts], method="nearest")][0]

    w_pre = nearest(SHUTDOWN - pd.Timedelta(weeks=WINDOW_W))
    w0 = nearest(SHUTDOWN)
    w1 = nearest(SHUTDOWN + pd.Timedelta(weeks=WINDOW_W))
    pre = piv.loc[w0] - piv.loc[w_pre]
    post = piv.loc[w1] - piv.loc[w0]
    both = pd.DataFrame({"pre": pre, "post": post}).dropna()
    both["excess"] = both["post"] - both["pre"]

    shop_loss = float(both.loc["product-reviews", "post"])
    competitors = both.drop(index="product-reviews")
    gross_gains = competitors.loc[competitors["post"] > 0, "post"].sum()
    pos_excess = competitors.loc[competitors["excess"] > 0, "excess"].sum()
    judgeme = both.loc["judgeme"]

    # apps without a full pre-window (listed during it): their post-window
    # gains cannot be trend-adjusted; treat them as fully event-attributable
    # for the upper bound of the recovery share
    post_only = pd.DataFrame({"post": post}).drop(index="product-reviews", errors="ignore")
    newcomers = post_only[~post_only.index.isin(both.index) & (post_only["post"] > 0)]
    new_gains = newcomers["post"].sum()
    lo = pos_excess / abs(shop_loss)
    hi = (pos_excess + new_gains) / abs(shop_loss)

    lines.extend([
        f"Windows: pre {w_pre.date()}->{w0.date()}, post {w0.date()}->{w1.date()}",
        f"platform app change post-shutdown:      {shop_loss:+,.0f}",
        f"competitor GROSS gains post-shutdown (incl. newcomers): {gross_gains + new_gains:+,.0f}",
        f"  of which established competitors:     {gross_gains:+,.0f}",
        f"  of which newly listed during windows: {new_gains:+,.0f}",
        f"established competitors' positive EXCESS over own pre-trend: {pos_excess:+,.0f}",
        f"recovery share: {lo:.1%} (excess only) to {hi:.1%} (excess + all newcomer gains)",
        "",
        f"Judge.me: pre {judgeme['pre']:+,.0f}, post {judgeme['post']:+,.0f}, "
        f"excess {judgeme['excess']:+,.0f} (decelerated across the event)",
        "",
        "Reading: most of the platform application's lost installed base does",
        "NOT reappear as competitor adoption within a year. The leader's gross",
        "gains are dominated by its pre-existing trend. Individual excess",
        "rankings are unstable for apps in decline (own-trend baselines),",
        "so only the aggregate recovery share is reported.",
        "",
    ])


def leadership_turnover(panel):
    cats = panel.dropna(subset=["category"]).groupby("category")["app_handle"].nunique()
    cats = cats[cats >= 20].index
    rows = []
    for c in cats:
        cp = panel[panel["category"] == c]
        lead = (cp.loc[cp.groupby("week_date")["install_count"].idxmax()]
                  .set_index("week_date")["app_handle"].sort_index())
        changes, cur, streak, pending = 0, lead.iloc[0], 0, None
        for h in lead.iloc[1:]:
            if h == cur:
                pending = None
                continue
            streak = streak + 1 if pending == h else 1
            pending = h
            if streak >= MIN_LEADER_STREAK:
                cur, changes, pending, streak = h, changes + 1, None, 0
        rows.append({"category": c, "leader_changes": changes, "weeks": len(lead)})
    t = pd.DataFrame(rows)
    t.to_parquet(DERIVED / "leadership_turnover.parquet", index=False)
    lines.extend([
        f"Leadership turnover ({len(t)} categories with >=20 tracked apps, "
        f"new leader must persist >={MIN_LEADER_STREAK} weeks):",
        f"  categories with at least one leader change: {(t['leader_changes']>0).mean():.1%}",
        f"  median changes per category: {t['leader_changes'].median():.0f}",
        f"  most contested: {t.nlargest(3,'leader_changes')[['category','leader_changes']].values.tolist()}",
        "",
    ])


def main():
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    panel["category"] = panel["app_handle"].map(
        master.set_index("app_handle")["primary_category_snapshot"])
    redistribution(panel)
    leadership_turnover(panel)
    out = RESULTS / "07_exit_redistribution.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
