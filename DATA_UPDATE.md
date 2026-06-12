# KTUS Data Auto-Update (dewpoint + precip)

This repo now includes automation to keep `data.js` (daily avg dewpoint in °F) and `precip_data.js` (`actual_2026` cumulative precip) up-to-date from the official Iowa Environmental Mesonet (IEM) ASOS archive for station KTUS (Tucson Intl). Values are stored directly in °F to stay consistent with the existing data, the 54°F monsoon onset threshold, all the mean/std arrays, and every label/tooltip/axis in the HTML pages.

## What gets updated each day
- **~7:00 AM Tucson time (MST, always UTC-7)**: the *previous calendar day's* values.
  - Dewpoint: average of hourly dwpf observations for the local calendar day (00:00–24:00 MST). Stored directly in °F (the IEM `dwpf` field is already in Fahrenheit; this matches the rest of the data file and all the °F labeling + the 54°F rule used throughout the site).
  - Precip: sum of p01i (one-hour precip) over the exact same local-day window using *only regular METAR reports* (report_type=3). This prevents double-counting any SPECI special observations that may contain extra precip groups.
  - GFS MEX Forecast: The script also fetches the latest Extended Range GFS MOS (MEX bulletin, 00Z or 12Z cycle) and extracts the DPT (dewpoint) guidance. This populates a short ~7-8 day dashed orange forecast line in the MonsoonTracker.html chart, starting from the last actual observation for visual continuity. The legend shows the exact model run (e.g. "GFS MEX 12Z Jun 12").
- Time zone handling: window calculated as UTC 07:00 previous → 07:00 current for the Tucson calendar day.
- Precip 2026 is stored as a **running cumulative total** (starting from 0.0 on Jun 15) so the charts show season-to-date accumulation like the historical years.

## Files
- `update_ktus_data.py` – the fetch + compute + edit script (pure stdlib, no pip installs needed).
- `.github/workflows/update-weather-data.yml` – GitHub Action that runs the script on a schedule + on manual trigger, then commits any changes.

## Easy setup steps (you don't need to be a git expert)

1. **Make sure the files are in your repo** (they now are):
   - `update_ktus_data.py`
   - `.github/workflows/update-weather-data.yml`
   - (The two data .js files will be modified by the automation.)

2. **Push everything to GitHub** (one-time):
   - If you are on your machine with this folder:
     ```
     git add update_ktus_data.py .github/workflows/update-weather-data.yml DATA_UPDATE.md
     git commit -m "Add automated KTUS data updater + GitHub Action"
     git push
     ```
   - Or use the GitHub web UI: upload the new/changed files to the main branch.

3. **(Recommended) Enable Actions write permission** (one time, in case your repo settings are strict):
   - Go to your repo on GitHub → **Settings** → **Actions** → **General**.
   - Under "Workflow permissions", choose **Read and write permissions**.
   - Check **Allow GitHub Actions to create and approve pull requests** (if present).
   - Save.

4. **That's it.** The Action will now run automatically every day shortly after 7 AM Tucson time and push the updated numbers into `data.js` + `precip_data.js`.

## Manual / test runs (optional but useful)

### Locally (on your Windows machine)
You already have Python. From PowerShell in this folder:

```powershell
# Dry run first (see what it would do for yesterday)
python update_ktus_data.py --dry-run

# Update a specific past day (e.g. to backfill or re-pull once full archive is available)
python update_ktus_data.py --date 2026-06-15

# Normal "fill yesterday" (what the Action does)
python update_ktus_data.py
```

After a local run, `git status` will show the two .js files changed. Commit & push them the usual way:
```powershell
git add data.js precip_data.js
git commit -m "Update KTUS actuals for Jun 15"
git push
```

### From the GitHub website (no local machine needed)
- Go to your repo → **Actions** tab.
- Click the workflow "Update KTUS Monsoon Data".
- Click **Run workflow** (top right) → choose branch `main` → **Run workflow**.
- It will check out, run the Python updater for the previous day, and commit if anything changed.

You can also look at the logs of any run to see the numbers it pulled (`avg_dew_f=...`, `precip_in=...`).

## Rate limits / missing data
- IEM has a polite IP-based rate limit. The script only calls it once per run. If you see 429 errors, just wait an hour and re-run (or let the scheduled Action try tomorrow).
- If a day has very few observations the script will still write the average it can compute (or skip dew if completely empty). You can always re-run the same `--date` later once the archive is more complete.
- First time you run for Jun 15 it will set the cumulative to that day's precip (usually small/zero).

## Troubleshooting
- The produced `data.js` / `precip_data.js` stay as a single long line `const X = {...};` so the existing HTML pages continue to load them with no changes.
- If the Action run shows "No data changes to commit", nothing new was available or the day was already filled.
- Backups: every write also creates `data.js.bak` and `precip_data.js.bak` (simple last-version copy).

## What the script does NOT touch
- `ts_data.js` (thunderstorm dates)
- Any prediction data or labels
- Historical actual_2020 / actual_2021 (those stay as-is)

Run `python update_ktus_data.py --help` for options.

Questions? The source is small and commented—open `update_ktus_data.py`.
