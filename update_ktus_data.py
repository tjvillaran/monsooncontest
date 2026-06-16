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
- Fetches latest Extended Range GFS MOS (MEX) from the official MDL source
  (https://www.weather.gov/source/mdl/MOS/GFSMEX.t00z) and extracts dewpoint
  guidance for the near-term outlook (shown as a dashed line on the graph).
Uses only regular hourly METARs (no SPECI) so p01i values represent clean hourly buckets
without double-dipping from special obs precip groups.
Updates:
  data.js          -> monsoonData.actual[]  (°F) + gfs_mex[] (dashed forecast) + gfs_mex_label
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
import time

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

    # Retry a few times on transient server errors (503 etc. are common from IEM)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                txt = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("Rate limited by IEM (429). Try again later (they ask for hourly max requests).")
                return None, 0.0
            if e.code in (500, 502, 503, 504) and attempt < 2:
                print(f"IEM temporarily unavailable (HTTP {e.code}), retrying ({attempt+1}/3)...")
                time.sleep(5 * (attempt + 1))
                continue
            print(f"HTTP error {e.code} fetching from IEM. Skipping today's update.")
            return None, 0.0
        except Exception as e:
            if attempt < 2:
                print(f"Network error fetching from IEM: {e}. Retrying ({attempt+1}/3)...")
                time.sleep(5 * (attempt + 1))
                continue
            print(f"Failed to fetch from IEM after retries: {e}. Skipping.")
            return None, 0.0
    else:
        print("Failed to fetch from IEM after 3 attempts. Skipping.")
        return None, 0.0

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


def fetch_latest_mex():
    """Fetch the latest Extended Range GFS MOS (MEX) from the official MDL source.
    Tries 00Z first (available in morning when the scheduled run happens), then 12Z.
    Returns (content, cycle, date_str) or raises.
    """
    import urllib.request
    # Primary source provided by user - always the latest for that cycle
    urls = [
        ("https://www.weather.gov/source/mdl/MOS/GFSMEX.t00z", "00"),
        ("https://www.weather.gov/source/mdl/MOS/GFSMEX.t12z", "12"),
    ]
    for url, cyc in urls:
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                return content, cyc, "latest"
        except Exception:
            continue
    raise RuntimeError("No recent GFS MEX file found")


def parse_mex_dewpoints(content, target_year=2026):
    """Parse the new-style GFSX MOS (GFSMEX) bulletin for TUS and return
    dict of 'Jun 13': dew_f .

    The file uses a table format with:
      - Header line containing the run time and date
      - Day label line (e.g. FRI 12| SAT 13| SUN 14| ...)
      - DPT line with dewpoint guidance aligned to those columns
    We take the first numeric value from each column group as the daily dewpoint.
    """
    import re
    from datetime import datetime

    forecast = {}
    lines = content.splitlines()

    for i, line in enumerate(lines):
        if "TUS" in line and "GFSX MOS GUIDANCE" in line:
            # Parse the issuance date from the header, e.g. 6/12/2026
            date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
            if date_match:
                try:
                    base_dt = datetime.strptime(date_match.group(1), "%m/%d/%Y")
                    month = base_dt.month
                    year = base_dt.year
                except:
                    month = 6
                    year = target_year
            else:
                month = 6
                year = target_year

            # Look ahead for the day-label line (must contain weekday abbr like SAT 13)
            day_numbers = []
            for k in range(1, 10):
                if i + k >= len(lines):
                    break
                dline = lines[i + k]
                # Require a weekday abbreviation to avoid matching the FHR hour line
                if re.search(r"\b(FRI|SAT|SUN|MON|TUE|WED|THU)\s*\d", dline, re.I):
                    matches = re.findall(r"(?:[A-Z]{3}\s*)?(\d{1,2})", dline)
                    if len(matches) >= 4:
                        day_numbers = [int(m) for m in matches[:8]]
                        break

            if not day_numbers:
                continue

            # Find the DPT line
            for m in range(k + 1, k + 8):
                if i + m >= len(lines):
                    break
                dpt_line = lines[i + m].strip()
                if dpt_line.startswith("DPT"):
                    # Split by column separator |
                    parts = dpt_line.split("|")
                    dpts = []
                    for p in parts:
                        nums = re.findall(r"-?\d+", p)
                        if nums:
                            try:
                                val = int(nums[0])
                                if -20 < val < 100:
                                    dpts.append(val)
                            except:
                                pass

                    # Map day numbers to dewpoints
                    for j, day in enumerate(day_numbers[:len(dpts)]):
                        try:
                            # Handle simple month rollover if days go backwards
                            if j > 0 and day < day_numbers[j-1]:
                                month += 1
                                if month > 12:
                                    month = 1
                                    year += 1
                            dt = datetime(year, month, day)
                            label = dt.strftime("%b ") + str(dt.day)
                            forecast[label] = dpts[j]
                        except Exception:
                            pass
                    break
            break

    return forecast


def update_mex_forecast():
    """Fetch latest MEX, parse dewpoints for the season, and update data.js with gfs_mex array and label."""
    import re
    labels = None
    try:
        data = parse_js_object(DATA_JS)
        labels = data.get("labels", [])
        if not labels:
            print("  No labels in data.js for MEX update")
            return False
        content, cyc, _ = fetch_latest_mex()
        mex_map = parse_mex_dewpoints(content)

        # Try to extract a nice run date from the first TUS header in the file
        run_label = f"GFS MEX {cyc}Z"
        header_match = re.search(r"TUS\s+GFSX MOS GUIDANCE\s+([0-9/]+)\s+(\d{4})\s+UTC", content)
        if header_match:
            try:
                raw = header_match.group(1)  # e.g. 6/12/2026
                # Make it a bit friendlier for the legend
                run_label = f"GFS MEX {cyc}Z {raw}"
            except:
                pass

        gfs_mex = [None] * len(labels)
        for idx, lab in enumerate(labels):
            if lab in mex_map:
                gfs_mex[idx] = mex_map[lab]

        # Make forecast line extend from the last actual point (for visual continuity)
        actuals = data.get("actual", [])
        for ii in range(len(actuals) - 1, -1, -1):
            if actuals[ii] is not None:
                if ii + 1 < len(gfs_mex) and gfs_mex[ii + 1] is not None:
                    gfs_mex[ii] = actuals[ii]
                break

        data["gfs_mex"] = gfs_mex
        data["gfs_mex_label"] = run_label
        save_js_object(DATA_JS, "monsoonData", data)
        filled = sum(1 for x in gfs_mex if x is not None)
        print(f"  data.js: updated gfs_mex ({filled} days) label='{data['gfs_mex_label']}'")
        return True
    except Exception as e:
        print(f"  Could not update MEX forecast: {e}")
        # ensure keys exist so the chart doesn't break
        if labels:
            try:
                data = parse_js_object(DATA_JS)
                if "gfs_mex" not in data:
                    data["gfs_mex"] = [None] * len(labels)
                    data["gfs_mex_label"] = "GFS MEX (unavailable)"
                    save_js_object(DATA_JS, "monsoonData", data)
            except:
                pass
        return False


def main():
    parser = argparse.ArgumentParser(description="Update KTUS dew/precip actuals from IEM + GFS MEX extended forecast.")
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

    # Always attempt to refresh the GFS MEX forecast (independent of the daily actual date)
    if not args.dry_run:
        if update_mex_forecast():
            changed = True

    if changed:
        print("Done. Files updated. Commit & push (or let the GitHub Action do it).")
    else:
        print("No changes made.")

if __name__ == "__main__":
    main()
