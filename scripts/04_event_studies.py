"""Step 04 — platform governance event studies (RQ4).

Four governance events, weekly resolution:
  E1  Product Reviews app deprecation (platform EXIT from a category):
      delisted 2023-09-05, shut down 2024-05-06.
  E2  Shopify Inbox relaunch (platform entry into chat): 2021-07-01.
  E3  Shopify Forms launch (platform entry into email capture): 2022-11-01.
  E4  Revenue-share change (0% below $1M): effective 2021-08-01.

Event dates from public announcements:
  E1: delisted 2023-09-05, shut down 2024-05-06 —
      https://junip.co/blog/what-to-do-about-shopify-product-reviews-app-being-deprecated-in-may-2024/
      https://maythegrowthbewithyou.com/shopify-product-review-app-to-be-retired-may-6th-2024/
  E2: Ping+Chat relaunched as Inbox, July 2021 (changelog post dated
      2021-07-15; windows anchor at 2021-07-01 — insensitive: Tidio
      pre/post +212/+43 at a 07-15 anchor vs +193/+48 at 07-01) —
      https://changelog.shopify.com/posts/shopify-ping-and-shopify-chat-have-relaunched-as-shopify-inbox
  E3: Shopify Forms listing launched 2022-11-01 (announced on the
      Shopify changelog 2022-11-08; snapshot creation date agrees) —
      https://apps.shopify.com/shopify-forms
      https://changelog.shopify.com/posts/grow-your-marketing-list-for-free-with-shopify-forms
  E4: announced 2021-06-29 at Unite, effective 2021-08-01 —
      https://techcrunch.com/2021/06/29/shopify-drops-its-app-store-commissions-to-0-on-developers-first-million-in-revenue/
Categories are the base paper's snapshot definitions via linkage.

Descriptive event studies in this step: trajectories, pre/post growth,
demand redistribution, HHI around events. (Formal matched-control
inference can be layered on later if reviewers ask.)

Reads:  data/derived/panel_weekly.parquet, app_master.parquet, raw snapshot
Writes: figures/F3_inbox_entry.{pdf,png}
        figures/F4_deprecation_exit.{pdf,png}
        figures/F5_revenue_share_entries.{pdf,png}
        results/04_event_studies.txt
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import plotstyle as ps
from common import DERIVED, FIGURES, RESULTS, load_snapshot_apps

E1_DELIST = pd.Timestamp("2023-09-05")
E1_SHUTDOWN = pd.Timestamp("2024-05-06")
E2_INBOX = pd.Timestamp("2021-07-01")
E3_FORMS = pd.Timestamp("2022-11-01")
E4_REVSHARE = pd.Timestamp("2021-08-01")

lines = ["Governance event studies (scripts/04_event_studies.py)", ""]


def load():
    panel = pd.read_parquet(DERIVED / "panel_weekly.parquet")
    master = pd.read_parquet(DERIVED / "app_master.parquet")
    panel["category"] = panel["app_handle"].map(
        master.set_index("app_handle")["primary_category_snapshot"])
    return panel, master


def cat_top_apps(panel, category, at, n=5):
    """Top n apps of a category by installs at (nearest week before) `at`."""
    c = panel[(panel["category"] == category) & (panel["week_date"] <= at)]
    last = c[c["week_date"] == c["week_date"].max()]
    return last.nlargest(n, "install_count")["app_handle"].tolist()


def weekly_hhi(panel, category):
    c = panel[panel["category"] == category]
    def h(g):
        s = g["install_count"] / g["install_count"].sum()
        return float((s ** 2).sum())
    return c.groupby("week_date").apply(h, include_groups=False)


def growth_pre_post(panel, handles, event, weeks=26):
    """Median weekly install delta per app, +/- `weeks` around event."""
    out = {}
    for hdl in handles:
        s = panel[panel["app_handle"] == hdl]
        pre = s[(s["week_date"] >= event - pd.Timedelta(weeks=weeks)) & (s["week_date"] < event)]
        post = s[(s["week_date"] > event) & (s["week_date"] <= event + pd.Timedelta(weeks=weeks))]
        out[hdl] = (pre["install_delta_weekly"].median(), post["install_delta_weekly"].median())
    return out


def plot_trajectories(ax, panel, handles, labels=None, unit=1000.0):
    labels = labels or handles
    for i, (hdl, lab) in enumerate(zip(handles, labels)):
        s = panel[panel["app_handle"] == hdl]
        ax.plot(s["week_date"], s["install_count"] / unit,
                color=ps.PALETTE[i % 8], label=lab)


# ======================= E1: deprecation (exit) =========================
def e1(panel):
    cat = "product reviews"
    focus = ["product-reviews", "judgeme", "loox", "air-reviews", "rivyo"]
    top = cat_top_apps(panel, cat, E1_DELIST, n=6)
    handles = list(dict.fromkeys([h for h in focus if h in set(panel["app_handle"])]
                                 + [h for h in top if h != "product-reviews"]))[:6]

    fig, axes = plt.subplots(1, 2, figsize=(ps.W_FULL, 2.7),
                             gridspec_kw={"wspace": 0.28})
    ax = axes[0]
    plot_trajectories(ax, panel[panel["week_date"] >= "2022-01-01"], handles)
    for d, style in [(E1_DELIST, ":"), (E1_SHUTDOWN, "--")]:
        ax.axvline(d, color=ps.INK2, linestyle=style, linewidth=0.9)
    ax.set_title("Installations (thousands)", loc="left")
    ax.set_ylim(0, 720)   # headroom so the legend clears the curves
    ax.legend(loc="upper left", ncol=2)
    ax.tick_params(axis="x", rotation=45)

    ax = axes[1]
    hh = weekly_hhi(panel, cat)
    hh = hh[hh.index >= "2022-01-01"]
    ax.plot(hh.index, hh.values, color=ps.PALETTE[0])
    for d, style in [(E1_DELIST, ":"), (E1_SHUTDOWN, "--")]:
        ax.axvline(d, color=ps.INK2, linestyle=style, linewidth=0.9)
    ax.set_title("Category HHI", loc="left")
    ax.tick_params(axis="x", rotation=45)
    fig.suptitle("Shopify exits product reviews: delisting (dotted) and shutdown (dashed)",
                 x=0.01, y=1.06, ha="left", fontsize=8.5, color=ps.INK)
    ps.save_fig(fig, FIGURES, "F4_deprecation_exit")
    plt.close(fig)

    # demand redistribution: install changes shutdown -> +52w
    w0 = panel[panel["week_date"] <= E1_SHUTDOWN]["week_date"].max()
    w1 = panel[panel["week_date"] <= E1_SHUTDOWN + pd.Timedelta(weeks=52)]["week_date"].max()
    catp = panel[panel["category"] == cat]
    a = catp[catp["week_date"] == w0].set_index("app_handle")["install_count"]
    b = catp[catp["week_date"] == w1].set_index("app_handle")["install_count"]
    delta = (b - a).dropna().sort_values(ascending=False)
    shopify_loss = float(delta.get("product-reviews", np.nan))
    gains = delta[delta > 0]
    lines.extend([
        f"E1 Product Reviews deprecation ({cat}):",
        f"  window {w0.date()} -> {w1.date()} (52 weeks after shutdown)",
        f"  Shopify product-reviews change: {shopify_loss:+,.0f} installs",
        f"  total gains across competitors: {gains.sum():+,.0f}",
        f"  top gainers: {[(h, int(v)) for h, v in gains.head(5).items()]}",
        f"  Judge.me share of all gains: {float(gains.get('judgeme', 0)) / gains.sum():.1%}",
        f"  category HHI {hh.loc[:E1_DELIST].iloc[-1]:.3f} (delist) -> {hh.iloc[-1]:.3f} (panel end)",
        f"  post-shutdown HHI trough {hh[hh.index >= E1_SHUTDOWN].min():.3f} at "
        f"{hh[hh.index >= E1_SHUTDOWN].idxmin().date()} "
        f"({(hh[hh.index >= E1_SHUTDOWN].idxmin() - E1_SHUTDOWN).days // 7} weeks after shutdown)",
        "",
    ])


# ======================= E2: Inbox entry (chat) ==========================
def e2(panel):
    cat = "chat"
    top = cat_top_apps(panel, cat, E2_INBOX + pd.Timedelta(weeks=52), n=5)
    handles = list(dict.fromkeys(["inbox"] + top))[:6]
    handles = [h for h in handles if h in set(panel["app_handle"])]

    fig, axes = plt.subplots(1, 2, figsize=(ps.W_FULL, 2.7),
                             gridspec_kw={"wspace": 0.28})
    ax = axes[0]
    plot_trajectories(ax, panel, handles)
    ax.axvline(E2_INBOX, color=ps.INK2, linestyle="--", linewidth=0.9)
    ax.set_title("Installations (thousands)", loc="left")
    ax.legend(loc="upper left", ncol=2)
    ax.tick_params(axis="x", rotation=45)

    ax = axes[1]
    hh = weekly_hhi(panel, cat)
    ax.plot(hh.index, hh.values, color=ps.PALETTE[0])
    ax.axvline(E2_INBOX, color=ps.INK2, linestyle="--", linewidth=0.9)
    ax.set_title("Category HHI", loc="left")
    ax.tick_params(axis="x", rotation=45)
    fig.suptitle("Shopify enters chat: Inbox relaunch (dashed)",
                 x=0.01, y=1.06, ha="left", fontsize=8.5, color=ps.INK)
    ps.save_fig(fig, FIGURES, "F3_inbox_entry")
    plt.close(fig)

    gp = growth_pre_post(panel, [h for h in handles if h != "inbox"], E2_INBOX)
    lines.extend([
        "E2 Shopify Inbox relaunch (chat):",
        f"  incumbent median weekly install delta, 26w pre -> 26w post:",
    ] + [f"    {h}: {pre:+.1f} -> {post:+.1f}" for h, (pre, post) in gp.items()] + [
        f"  chat HHI at relaunch {hh.asof(E2_INBOX):.3f} -> panel end {hh.iloc[-1]:.3f}",
        f"  chat HHI series start {hh.index.min().date()}: {hh.iloc[0]:.3f}; "
        f"post-relaunch peak {hh[hh.index >= E2_INBOX].max():.3f} at "
        f"{hh[hh.index >= E2_INBOX].idxmax().date()}",
    ])
    inbox = (panel[panel["app_handle"] == "inbox"]
             .set_index("week_date")["install_count"].sort_index())
    at3y = float(inbox.asof(E2_INBOX + pd.Timedelta(weeks=156)))
    lines.extend([
        f"  inbox installs at +3y: {at3y:,.0f}; all-time peak "
        f"{inbox.max():,.0f} at {inbox.idxmax().date()}",
        "",
    ])


# ======================= E3: Forms entry ================================
def e3(panel):
    cat = "email marketing"
    top = cat_top_apps(panel, cat, E3_FORMS, n=5)
    handles = list(dict.fromkeys(["shopify-forms"] + top))
    handles = [h for h in handles if h in set(panel["app_handle"])][:6]
    gp = growth_pre_post(panel, [h for h in handles if h != "shopify-forms"], E3_FORMS)
    first_seen = panel.loc[panel["app_handle"] == "shopify-forms", "week_date"].min()
    lines.extend([
        "E3 Shopify Forms launch (email marketing/email capture):",
        f"  NOTE: panel first tracks shopify-forms {first_seen.date()} (tracking lag",
        "  vs launch 2022-11-01); incumbent pre/post uses the launch date.",
        "  incumbent median weekly install delta, 26w pre -> 26w post:",
    ] + [f"    {h}: {pre:+.1f} -> {post:+.1f}" for h, (pre, post) in gp.items()] + [""])


# ======================= E4: revenue share (entry rates) =================
def e4():
    apps = load_snapshot_apps()
    created = pd.to_datetime(apps["created"], format="%Y/%m/%d", errors="coerce").dropna()
    monthly = created.groupby(created.dt.to_period("M")).size()
    m = monthly.loc["2019-01":"2023-12"]
    x = m.index.to_timestamp()

    fig, ax = plt.subplots(figsize=(ps.W_FULL, 2.4))
    ax.bar(x, m.values, width=24, color=ps.PALETTE[0], linewidth=0)
    ax.axvline(E4_REVSHARE, color=ps.INK2, linestyle="--", linewidth=0.9)
    ax.set_title("New app listings per month, 2019-2023 (snapshot creation dates); "
                 "dashed: 0% revenue share effective", loc="left")
    ax.set_ylabel("apps created")
    ps.save_fig(fig, FIGURES, "F5_revenue_share_entries")
    plt.close(fig)

    pre = m.loc["2020-08":"2021-07"].mean()
    post = m.loc["2021-08":"2022-07"].mean()
    lines.extend([
        "E4 revenue-share change (0% below $1M, effective 2021-08-01):",
        f"  mean monthly new listings 12m pre:  {pre:.1f}",
        f"  mean monthly new listings 12m post: {post:.1f}  ({(post/pre-1):+.1%})",
        "  (full-universe snapshot creation dates, immune to panel tracking bias)",
        "",
    ])

    # retention response: monthly tracking-exit hazard among tracked apps
    mast = pd.read_parquet(DERIVED / "app_master.parquet").dropna(
        subset=["first_week", "last_week"])
    haz = {}
    for m0 in pd.date_range("2020-08-01", "2022-07-01", freq="MS"):
        m1 = m0 + pd.offsets.MonthEnd(1)
        at_risk = mast[(mast["first_week"] <= m1) & (mast["last_week"] >= m0)]
        ex = at_risk[at_risk["exited"] & at_risk["last_week"].between(m0, m1)]
        haz[m0] = len(ex) / len(at_risk)
    haz = pd.Series(haz)
    lines.extend([
        "  retention: monthly tracking-exit hazard among tracked apps",
        f"    12m pre:  mean {haz.loc[:E4_REVSHARE - pd.Timedelta(days=1)].mean():.2%}",
        f"    12m post: mean {haz.loc[E4_REVSHARE:].mean():.2%}",
        "    (in line with the secular rise in exit rates across cohorts;",
        "    entry-observed cohort comparison across the boundary not",
        "    informative: tracking onboarded few new apps in 2021-22)",
        "",
    ])


# ============== E1 follow-on: category penetration ======================
def penetration(panel):
    """Product reviews' share of all tracked installations over time,
    with and without the platform application (timing observation
    around the exit; no causal claim)."""
    tot = panel.groupby("week_date")["install_count"].sum()
    pr = panel[panel["category"] == "product reviews"]
    incl = (pr.groupby("week_date")["install_count"].sum() / tot * 100).dropna()
    excl = (pr[pr["app_handle"] != "product-reviews"]
            .groupby("week_date")["install_count"].sum() / tot * 100).dropna()
    lines.append("E1 follow-on: product reviews share of tracked installations:")
    lines.append(f"  peak incl. platform app: {incl.max():.1f}% at "
                 f"{incl.idxmax().date()} (delisting {E1_DELIST.date()})")
    for label, d in [("delisting", E1_DELIST), ("shutdown", E1_SHUTDOWN),
                     ("panel end", incl.index.max())]:
        w = incl.index[incl.index <= d].max()
        lines.append(f"  {label:10s} incl {incl.loc[w]:5.2f}%  "
                     f"excl {excl.loc[w]:5.2f}%")
    # third-party concentration: leader share of tracked category installs
    cat_tot = pr.groupby("week_date")["install_count"].sum()
    jm = (pr[pr["app_handle"] == "judgeme"]
          .set_index("week_date")["install_count"].sort_index())
    lines.append("  leader (judgeme) share of tracked category installs:")
    for label, d in [("panel start", cat_tot.index.min()),
                     ("delisting", E1_DELIST), ("shutdown", E1_SHUTDOWN),
                     ("panel end", cat_tot.index.max())]:
        w = cat_tot.index[cat_tot.index <= d].max()
        lines.append(f"    {label:11s} {float(jm.asof(w)) / cat_tot.loc[w]:.1%}")
    ann = jm.resample("YS").last().diff().dropna()
    lines.append("  leader detected installs added per calendar year: "
                 + ", ".join(f"{k.year}: {v:+,.0f}" for k, v in ann.items()))
    lines.append("")


# ============== detection-lapse screen for event-study series ==========
def lapse_screen(panel):
    """Screen the key E1/E2 series for detection lapses: a temporary
    drop in detected installs that later recovers to its pre-drop
    level (Store Leads can lose an app while a changed storefront
    signature is re-attributed). Only lapses near a window endpoint
    can bias the endpoint-difference numbers, so flag those; also
    report review accumulation over the E1 post window as an
    independent activity signal."""
    e1_apps = list(dict.fromkeys(
        ["product-reviews", "judgeme", "loox", "air-reviews", "rivyo"]
        + cat_top_apps(panel, "product reviews", E1_DELIST, n=6)))
    e2_apps = list(dict.fromkeys(
        ["inbox"] + cat_top_apps(panel, "chat",
                                 E2_INBOX + pd.Timedelta(weeks=52), n=5)))
    endpoints = {
        "E1": [E1_SHUTDOWN - pd.Timedelta(weeks=52), E1_DELIST,
               E1_SHUTDOWN, E1_SHUTDOWN + pd.Timedelta(weeks=52)],
        "E2": [E2_INBOX - pd.Timedelta(weeks=26), E2_INBOX,
               E2_INBOX + pd.Timedelta(weeks=26)],
    }

    def episodes(s):
        """Temporary drawdowns that fully recover: (start, end, depth)."""
        eps, start, peak = [], None, None
        runmax = s.cummax()
        for t in s.index:
            if start is None:
                if runmax[t] - s[t] >= max(0.05 * runmax[t], 200):
                    start, peak = t, runmax[t]
            elif s[t] >= peak:
                eps.append((start, t, float(peak - s[s.index.slice_locs(start, t)[0]:
                                                     s.index.slice_locs(start, t)[1]].min())))
                start = None
        return eps

    lines.append("Detection-lapse screen (drop >= max(5%, 200) below running "
                 "max, later fully recovered):")
    flagged = 0
    for tag, apps in [("E1", e1_apps), ("E2", e2_apps)]:
        for hdl in apps:
            s = (panel[panel["app_handle"] == hdl]
                 .set_index("week_date")["install_count"].sort_index())
            if s.empty:
                continue
            eps = episodes(s)
            near = [e for e in eps
                    if any(abs((x - d).days) <= 56
                           for d in endpoints[tag] for x in (e[0], e[1]))]
            flagged += len(near)
            if eps or near:
                lines.append(f"  [{tag}] {hdl}: {len(eps)} recovered episode(s)"
                             + (f"; NEAR ENDPOINT: "
                                + "; ".join(f"{a.date()}->{b.date()} depth {d:,.0f}"
                                            for a, b, d in near)
                                if near else "; none near endpoints"))
    if flagged == 0:
        lines.append("  no recovered drawdown episode within 8 weeks of any "
                     "event-window endpoint")
    w0 = panel[panel["week_date"] <= E1_SHUTDOWN]["week_date"].max()
    w1 = panel[panel["week_date"] <= E1_SHUTDOWN
               + pd.Timedelta(weeks=52)]["week_date"].max()
    lines.append("  review accumulation over the E1 post window "
                 f"({w0.date()} -> {w1.date()}), archival series:")
    for hdl in e1_apps:
        s = (panel[panel["app_handle"] == hdl]
             .set_index("week_date")["review_count"].sort_index().dropna())
        if s.empty or s.index.min() > w0 or s.index.max() < w1:
            continue
        r0, r1 = float(s.asof(w0)), float(s.asof(w1))
        lines.append(f"    {hdl}: {r0:,.0f} -> {r1:,.0f} ({r1 - r0:+,.0f})")
    lines.append("")


def main():
    ps.apply()
    panel, master = load()
    e1(panel)
    e2(panel)
    e3(panel)
    e4()
    penetration(panel)
    lapse_screen(panel)
    out = RESULTS / "04_event_studies.txt"
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
