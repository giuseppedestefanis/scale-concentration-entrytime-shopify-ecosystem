"""Step 00 — validate raw inputs.

Checks that the two raw data sources are present and match the shapes and
headline counts reported in the base paper and in the panel profile.
Fails loudly if anything differs, so no downstream script runs on wrong data.

Writes: results/00_input_validation.txt
"""
import sys

from common import (EXPECTED, PANEL_CSV, RESULTS, SNAPSHOT_APPS_CSV,
                    load_panel, load_snapshot_apps, panel_handle_key)

checks = []


def check(name, got, want):
    ok = got == want
    checks.append((name, got, want, ok))
    print(f"{'OK ' if ok else 'FAIL'} {name}: got {got}, expected {want}")
    return ok


def main():
    print(f"panel:    {PANEL_CSV}")
    print(f"snapshot: {SNAPSHOT_APPS_CSV}")

    panel = load_panel()
    check("panel rows", len(panel), EXPECTED["panel_rows"])
    check("panel cols", panel.shape[1], EXPECTED["panel_cols"])
    check("panel distinct apps", panel["app_handle"].nunique(), EXPECTED["panel_apps"])
    check("panel distinct weeks", panel["week_date"].nunique(), EXPECTED["panel_weeks"])
    check("panel first week", str(panel["week_date"].min().date()), EXPECTED["panel_first_week"])
    check("panel last week", str(panel["week_date"].max().date()), EXPECTED["panel_last_week"])

    apps = load_snapshot_apps()
    check("snapshot apps", len(apps), EXPECTED["snapshot_apps"])
    check("snapshot active", int((apps["status"] == "Active").sum()), EXPECTED["snapshot_active"])
    active_installed = int(((apps["status"] == "Active") & (apps["installs"].fillna(0) > 0)).sum())
    check("snapshot active with installs>0", active_installed, EXPECTED["snapshot_active_installed"])

    # linkage sanity: share of panel handles matching snapshot tokens
    ph = panel_handle_key(panel["app_handle"].drop_duplicates())
    tokens = set(apps["token"].astype(str).str.lower())
    match_rate = ph.isin(tokens).mean()
    ok_link = match_rate > 0.9
    checks.append(("panel->snapshot token match rate > 0.9", round(match_rate, 3), "> 0.9", ok_link))
    print(f"{'OK ' if ok_link else 'FAIL'} panel->snapshot token match rate: {match_rate:.1%}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "00_input_validation.txt"
    with open(out, "w") as f:
        f.write("Input validation (scripts/00_validate_inputs.py)\n\n")
        for name, got, want, ok in checks:
            f.write(f"[{'OK' if ok else 'FAIL'}] {name}: got {got}, expected {want}\n")
    print(f"wrote {out}")

    if not all(c[3] for c in checks):
        sys.exit("input validation FAILED — do not run downstream scripts")
    print("all input checks passed")


if __name__ == "__main__":
    main()
