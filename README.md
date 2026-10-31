# Al Bonian FM Dashboard

Navy-and-white, login-protected dashboard application rebuilt from an imported Power BI snapshot. The 17 source page names are retained. Calculated insights operate without an AI provider or API fees.

## Run with the private delivery package

Requires Python 3.12. Extract the private ZIP, then run `start_windows.bat`, or:

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py --browser.gatherUsageStats=false
```

Open http://localhost:8501. Initial credentials are supplied separately in the private package. Administrators can create, rename, reset, change roles, and disable accounts. Operations users can view dashboards, filters, and records; they cannot manage accounts or import data. Sessions expire after eight hours and are revoked after account changes. Passwords use per-account salted scrypt hashes. Five failed sign-ins lock an account for 15 minutes.

## Run from this GitHub repository

**Business data, account databases, credentials, and PBIX files are deliberately excluded from this public repository.**

```sh
python -m pip install -r requirements.txt
python security.py
python import_pbix.py /private/path/dashboard.pbix
python -m streamlit run app.py --browser.gatherUsageStats=false
```

Set `ABFM_DATA_DIR` to a private durable directory if needed. Keep account and data directories outside any publicly served files. An admin can also upload replacement PBIX files through Administration. Imports exclude the legacy `User_Security` table. Refreshes of the published Power BI service do not automatically propagate; import a refreshed PBIX snapshot.

## Calculation definitions

- BREAKDOWN → Reactive; PPM → PPM; SCHEDULEDTASK → Corrective.
- COMPLETE / HISTORY = completed. ACTIVE / ASSIGNED = open. CANCELLED is separate. Unknown statuses remain separate.
- Completion percentage = completed / (all selected work orders minus cancelled). A zero denominator displays no percentage.
- Overdue open = an open work order with a recorded due date before today's date in Asia/Dubai. Open records without due dates are reported separately.
- Counts are source row counts unless explicitly labelled otherwise. Dataset filters apply to the cards, action highlights, charts, and record table. Multiple source datasets within a page are filtered independently.
- Quotation awarded statuses: Approved Not Started, Work In Progress, Completed Not Invoice, Invoiced.
- Date-based expiry checks identify records whose dates need review; they do not infer renewal or contract status.
- Invalid encoded dates outside years 1–9999 become missing dates and are counted in the private import manifest. Historical calculated columns such as RFQ Delay Days remain snapshot values.

## Conversion scope

The app retains the 17 pages and underlying tables. It provides responsive views, metrics, configurable status/category breakdowns, calculated action highlights, filters, and paginated source records. Source charts with supported single-table numeric aggregations and explicitly translated measures are reconstructed from their PBIX field definitions. The in-app conversion log identifies charts skipped because of missing fields, unsupported DAX, unresolved aliases, multi-dimensional expressions, or unsupported saved visual filters. No field is silently mapped to a different business concept.

This is **not a pixel-for-pixel Power BI renderer**. Custom visuals, bookmarks, drill-through, automatic relationship propagation, and complex DAX require additional explicit conversion. Original saved slicer selections are reset. Some original charts are not yet converted; inspect Definitions & skipped charts in each tab. Native Power BI security roles are not copied: operations users in this application can view all imported dashboard data.

## Hosting

GitHub stores code; GitHub Pages cannot run this Python server or enforce these account permissions. Deploy to a Python-capable server using HTTPS, a private persistent disk for `ABFM_DATA_DIR`, and a single app instance for SQLite. Do not put the data into a public repository or static hosting directory. No paid hosting service is created by this repository.

```sh
docker build -t abfm-dashboard .
docker run --rm -p 8501:8501 -v /private/abfm-data:/app/data abfm-dashboard
```

Bootstrap accounts and import the PBIX before starting production. Back up the private data directory. Docker deliberately excludes imported data and secrets from the image. Put an HTTPS reverse proxy in front of the app for internet access.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests cover task type isolation, cancellation separation, completion denominators, overdue handling, unknown statuses, quotation award rules, operations permission denial, session revocation, administrator protection, and sign-in lockout.
