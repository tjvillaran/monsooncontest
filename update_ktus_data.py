#!/usr/bin/env python3
"""
update_ktus_data.py
Fetches previous calendar day's weather for KTUS (Tucson) from IEM ASOS archive.
- Computes daily average dewpoint (°F) from hourly METAR observations (report_type=3 only)
  aligned to Tucson local calendar day (MST, UTC-7, no DST).
  Values are stored directly in °F to match the existing data in data.js, all the
  chart labels, tooltips, axis, threshold (54), mean/std arrays, and every reference
  in the HTML pages ("54°F rule").
- Computes daily total precip (inches) by summing p01i over the same 24h window.
Uses only regular hourly METARs (no SPECI) so p01i values represent clean hourly buckets
without double-dipping from special obs precip groups.
Updates:
  data.js          -> monsoonData.actual[]  (°F)
  precip_data.js   -> precipData.actual_2026[] (cumulative from Jun 15 season start, inches)
Intended to run ~7am local each day to fill the *previous* day's values.
Run with --date YYYY-MM-DD to backfill/test a specific local date.
"""

from datetime import datetime, timedelta, timezone
import urllib.request
import csv
import json
import re
import sys
import argparse
import os

ASOS_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
DATA_JS = "data.js"
PRECIP_JS = "precip_data.js"

def parse_js_object(path):
    """Load the const Foo = {...}; file into a python dict."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    # Expect exactly one top level: const name = <json>;
    if not content.endswith(";"):
        content += ";"
    # Split off "const xxx = " and trailing ;
    try:
        json_part = content.split("=", 1)[1].rsplit(";", 1)[0].strip()
        data = json.loads(json_part)
        return data
    except Exception as e:
        raise RuntimeError(f"Failed to parse {path} as JS data object: {e}")

def save_js_object(path, var_name, data):
    """Write back in compact single-line form matching original style."""
    compact = json.dumps(data, separators=(",", ":"))
    out = f"const {var_name} = {compact};"
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    # Also write a .bak of previous (simple, non-versioned)
    bak = path + ".bak"
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as src, open(bak, "w", encoding="utf-8") as dst:
                dst.write(src.read())
    except Exception:
        pass  # non-fatal

def get_label(d):
    """Return 'Jun 15' style label used in the data files."""
    # %b is locale 'Jun', day without leading zero
    return f"{d.strftime('%b')} {d.day}"

def fetch_and_compute(local_date):
    """
    Return (avg_dew_f or None, daily_precip_in or 0.0)
    Fetches hourly METAR data (dwpf already in °F), averages dewpoint over the
    local Tucson MST calendar day.
    Values stored directly as °F (consistent with the rest of data.js and the UI).
    Precip uses summed p01i (regular reports only).
    Uses strict local calendar day window in MST.
    Only regular METAR (report_type=3) to avoid SPECI double-count issues.
    """
    mst = timezone(timedelta(hours=-7))
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=mst)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)

    # Request a little wider to be safe
    start_req = local_date - timedelta(days=1)
    end_req = local_date + timedelta(days=1)

    q = (
        "station=KTUS"
        "&data=valid&data=dwpf&data=p01i"
        f"&year1={start_req.year}&month1={start_req.month}&day1={start_req.day}"
        f"&year2={end_req.year}&month2={end_req.month}&day2={end_req.day}"
        "&tz=Etc/UTC&format=comma&missing=M&trace=T&report_type=3"
    )
    url = f"{ASOS_URL}?{q}"

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("Rate limited by IEM (429). Try again later (they ask for hourly max requests).")
        raise

    # Remove comment / debug lines
    lines = [ln for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]
    if len(lines) < 2:
        print("No data returned for range.")
        return None, 0.0

    reader = csv.DictReader(lines)
    dewps = []
    precips = []
    for row in reader:
        try:
            vdt = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (utc_start <= vdt < utc_end):
            continue
        # dewpoint
        d = row.get("dwpf")
        if d not in (None, "", "M"):
            try:
                dewps.append(float(d))
            except ValueError:
                pass
        # precip - p01i on these reports is the hourly amount for the preceding ~hour
        p = row.get("p01i")
        if p == "T":
            precips.append(0.0001)
        elif p not in (None, "", "M"):
            try:
                precips.append(float(p))
            except ValueError:
                pass

    avg_dew_f = None
    if dewps:
        avg_dew_f = round(sum(dewps) / len(dewps), 2)

    daily_precip = round(sum(precips), 2)

    print(f"  {local_date} | obs_in_window={len(dewps)+len(precips)} (dew n={len(dewps)}) | "
          f"avg_dew_f={avg_dew_f} | precip_in={daily_precip}")
    return avg_dew_f, daily_precip

def update_data_js(local_date, avg_dew_f):
    if avg_dew_f is None:
        print("  No dewpoint data, skipping data.js update.")
        return False
    try:
        data = parse_js_object(DATA_JS)
    except Exception as e:
        print(f"ERROR reading {DATA_JS}: {e}")
        return False

    label = get_label(local_date)
    if "labels" not in data or "actual" not in data:
        print("  data.js missing expected keys.")
        return False
    try:
        idx = data["labels"].index(label)
    except ValueError:
        print(f"  Label {label} not present in data.js labels (Jun 1 - Sep 30 range).")
        return False

    data["actual"][idx] = avg_dew_f
    save_js_object(DATA_JS, "monsoonData", data)
    print(f"  data.js: set actual[{idx}] ({label}) = {avg_dew_f}")
    return True

def update_precip_js(local_date, daily_precip):
    """Update cumulative actual_2026. Safe if previous day already populated."""
    if daily_precip is None:
        daily_precip = 0.0
    try:
        data = parse_js_object(PRECIP_JS)
    except Exception as e:
        print(f"ERROR reading {PRECIP_JS}: {e}")
        return False

    label = get_label(local_date)
    labels = data.get("labels", [])
    if not labels or "actual_2026" not in data:
        print("  precip_data.js missing labels or actual_2026.")
        return False
    try:
        idx = labels.index(label)
    except ValueError:
        # e.g. before Jun 15 or after Sep 30
        print(f"  Label {label} not in precip season (Jun 15-Sep 30). Skipping precip update.")
        return False

    arr = data["actual_2026"]
    prev_cum = 0.0
    if idx > 0:
        prev = arr[idx - 1]
        if prev is not None:
            prev_cum = float(prev)
        else:
            # Look for last known cumul before this (gap case)
            known = [v for v in arr[:idx] if v is not None]
            if known:
                prev_cum = float(known[-1])
                print(f"  WARNING: gap before {label}; using last known cumul {prev_cum} + today's precip (missed days treated as 0).")
            else:
                prev_cum = 0.0

    new_cum = round(prev_cum + daily_precip, 2)
    arr[idx] = new_cum
    save_js_object(PRECIP_JS, "precipData", data)
    print(f"  precip_data.js: set actual_2026[{idx}] ({label}) = {new_cum} (added {daily_precip})")
    return True

def main():
    parser = argparse.ArgumentParser(description="Update KTUS dew/precip actuals from IEM.")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Local MST date to update (defaults to yesterday)")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not write files")
    parser.add_argument("--force", action="store_true", help="Write even if data looks missing")
    args = parser.parse_args()

    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("Bad --date, use YYYY-MM-DD")
            sys.exit(2)
    else:
        mst = timezone(timedelta(hours=-7))
        target = (datetime.now(mst) - timedelta(days=1)).date()

    print(f"Target local date (Tucson MST): {target}")
    avg_dew, precip = fetch_and_compute(target)

    if avg_dew is None and not args.force:
        print("No dew data computed; not updating (use --force to override).")
    if args.dry_run:
        print("DRY RUN: not writing files.")
        # Still show what would happen
        label = get_label(target)
        print(f"Would target label: {label}")
        return

    changed = False
    if avg_dew is not None or args.force:
        if update_data_js(target, avg_dew):
            changed = True
    if update_precip_js(target, precip or 0.0):
        changed = True

    if changed:
        print("Done. Files updated. Commit & push (or let the GitHub Action do it).")
    else:
        print("No changes made.")

if __name__ == "__main__":
    main()
