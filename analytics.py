"""Deterministic calculations over the imported snapshot. No external AI calls."""
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

TASK_TYPES = {"Reactive": "BREAKDOWN", "PPM": "PPM", "Corrective": "SCHEDULEDTASK", "Pro-active": "PROACTIVE"}
PAGE_TABLES = {
    "Asset Details": ["Asset Details", "Assets Tasks"],
    "KPI & MR": ["KPI", "Monthly Reports"],
    "Reactive": ["Reactive"], "PPM": ["Reactive"], "Corrective": ["Reactive"], "Pro-active": ["Reactive"],
    "Quotations": ["Quotation"], "Unbilled Revenue": ["Unbilled"],
    "Customer Satisfaction": ["2023 Survey Details"], "Cheque Requests": ["Cheque Requests"],
    "MR tracker": ["MR Main Sheet"], "RFQ": ["RFQ"], "QHSE": ["QHSE"],
    "Vendor Database": ["Vendor Database"], "AMC": ["AMC"],
    "Manpower": ["Site Staff List", "Allocation"],
    "Manpower Management": ["Staff List Management", "Allocation Management"],
    "SubContractors Agrements": ["Subcontractor AMC"],
}
COMPLETED = {"COMPLETE", "HISTORY"}
OPEN = {"ACTIVE", "ASSIGNED"}


def resolve(frame, name):
    if name in frame.columns:
        return name
    matches = [c for c in frame.columns if c.strip().casefold() == name.strip().casefold()]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(name)


def numbers(frame, name):
    return pd.to_numeric(frame[resolve(frame, name)], errors="coerce")


def normalized(series):
    # Keep strings variable-width. NumPy's astype(str) can allocate the longest
    # RFQ scope length for every row and consume hundreds of MB.
    return series.astype('string[pyarrow]').fillna("").str.strip().str.upper()


def dates(series):
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert("Asia/Dubai").dt.tz_localize(None)


def today():
    return pd.Timestamp(datetime.now(ZoneInfo("Asia/Dubai")).date())


def task_frame(frame, page):
    if page in TASK_TYPES:
        return frame.loc[normalized(frame[resolve(frame, "Task Type")]) == TASK_TYPES[page]].copy()
    return frame.copy()


def work_order_metrics(frame):
    status = normalized(frame[resolve(frame, "Status")])
    completed = int(status.isin(COMPLETED).sum())
    cancelled = int(status.eq("CANCELLED").sum())
    opened = int(status.isin(OPEN).sum())
    other = len(frame) - completed - cancelled - opened
    denominator = len(frame) - cancelled
    metrics = {"Total work orders": len(frame), "Completed": completed, "Open": opened,
               "Cancelled": cancelled, "Other status": other,
               "Completion % (excl. cancelled)": 100 * completed / denominator if denominator else None}
    try:
        due = dates(frame[resolve(frame, "Task Due Date")])
        metrics["Overdue open"] = int((status.isin(OPEN) & due.lt(today())).sum())
        metrics["Open without due date"] = int((status.isin(OPEN) & due.isna()).sum())
    except KeyError:
        pass
    return metrics


def summarize(frame, table, page):
    metrics = {"Records": len(frame)}
    insights = []
    if table == "Reactive":
        metrics = work_order_metrics(frame)
        if metrics["Open"]:
            insights.append(f"Allocate owners to {metrics['Open']:,} open work orders; prioritise overdue and critical tasks.")
        if metrics.get("Overdue open", 0):
            insights.append(f"{metrics['Overdue open']:,} open work orders have a due date before today. Review recovery dates and resource allocation.")
        if metrics.get("Open without due date", 0):
            insights.append(f"Set due dates for {metrics['Open without due date']:,} open work orders before reporting overdue compliance.")
        if metrics["Cancelled"]:
            insights.append(f"Review reasons for {metrics['Cancelled']:,} cancellations. These are excluded from completed counts and the completion-rate denominator.")
    def add_sum(column, label):
        try:
            series = numbers(frame, column)
            if series.notna().any():
                metrics[label] = float(series.sum())
        except KeyError:
            pass
    if table == "Quotation":
        add_sum("Total Value", "Total quotation value (AED)")
        status = normalized(frame[resolve(frame, "Status")])
        # Exact statuses present in this source; no won/lost inference from free text.
        awarded = status.isin({"APPROVED NOT STARTED", "WORK IN PROGRESS", "COMPLETED NOT INVOICE", "INVOICED"})
        metrics["Awarded quotations"] = int(awarded.sum())
        metrics["Awarded value (AED)"] = float(numbers(frame, "Total Value").loc[awarded].sum())
        count = int(status.eq("COMPLETED NOT INVOICE").sum())
        if count:
            insights.append(f"Review {count:,} completed, uninvoiced quotations with Finance and collect supporting completion documents.")
    elif table == "Unbilled":
        add_sum("Balance Value", "Balance value (AED)")
        add_sum("Unbilled", "Unbilled value (AED)")
        add_sum("Non collectable Amounts", "Non-collectable value (AED)")
        if len(frame):
            insights.append("Prioritise collection by balance and completion date; confirm invoice blockers and assign an owner to each outstanding item.")
    elif table == "Asset Details":
        add_sum("Replacement Cost", "Replacement cost (AED)")
        try:
            expired = int(dates(frame[resolve(frame, "Date Warranty Expires")]).lt(today()).sum())
            metrics["Warranty expired (dated records)"] = expired
            if expired:
                insights.append(f"Review maintenance and replacement plans for {expired:,} assets with a recorded warranty expiry before today.")
        except KeyError:
            pass
    elif table in {"AMC", "Subcontractor AMC"}:
        end = "Ending Date" if table == "AMC" else "End Date "
        try:
            remaining = (dates(frame[resolve(frame, end)]) - today()).dt.days
            metrics["Past end date"] = int(remaining.lt(0).sum())
            metrics["Ending within 30 days"] = int(remaining.between(0, 30).sum())
            if metrics["Past end date"] or metrics["Ending within 30 days"]:
                insights.append(f"Validate {metrics['Past end date']:,} past-end-date records and prepare renewals for {metrics['Ending within 30 days']:,} contracts ending within 30 days. Dates alone do not establish current contract status.")
        except KeyError:
            pass
        add_sum("Annual Value (Excl. 5%)" if table == "AMC" else "Monthly Cost", "Annual value (AED)" if table == "AMC" else "Monthly cost (AED)")
    elif table == "RFQ":
        try:
            delayed = numbers(frame, "Delay Days").gt(0)
            metrics["Delayed RFQs (source field)"] = int(delayed.sum())
            if delayed.any():
                insights.append(f"Check current status and escalate {int(delayed.sum()):,} RFQs whose stored Delay Days is positive. This imported field may include historical delays.")
        except KeyError:
            pass
    elif table == "Cheque Requests":
        add_sum("Cheque Req Amount - AED", "Requested cheque value (AED)")
        try:
            missing = dates(frame[resolve(frame, "Cheque Release Date")]).isna()
            metrics["No recorded release date"] = int(missing.sum())
            if missing.any():
                insights.append(f"Reconcile {int(missing.sum()):,} cheque requests without a release date against their status before treating them as outstanding payments.")
        except KeyError:
            pass
    elif table == "KPI":
        try:
            actual, target = numbers(frame, "KPI Mark"), numbers(frame, "Target")
            valid = actual.notna() & target.notna()
            below = int((valid & actual.lt(target)).sum())
            metrics["Records below KPI target"] = below
            if below:
                insights.append(f"Review {below:,} records where KPI Mark is below Target; assign corrective actions against the affected contracts.")
        except KeyError:
            pass
    elif table in {"Allocation", "Allocation Management"}:
        try:
            difference = numbers(frame, "Manpower on sites") - numbers(frame, "Manpower as per contract")
            metrics["Records below contract staffing"] = int(difference.lt(0).sum())
            metrics["Summed staffing shortfall"] = float((-difference.clip(upper=0)).sum())
            insights.append("Review staffing shortfalls by contract and reporting period. Do not sum weekly or monthly snapshots as unique employees.")
        except KeyError:
            pass
    elif table == "QHSE":
        add_sum("Number Of LTI", "Recorded lost-time injuries")
        try:
            raised, closed = numbers(frame, "No Of HSE Observation Raised"), numbers(frame, "No of HSE Observation Closed")
            metrics["Observations raised minus closed"] = float(raised.sum() - closed.sum())
            insights.append("Reconcile HSE observations raised versus closed and investigate every recorded lost-time injury. Period totals do not necessarily represent unique open cases.")
        except KeyError:
            pass
    checks = {
        "Vendor Database": ["EMAIL ID", "MOBILE NO"],
        "MR Main Sheet": ["ITEM DESCRIPTION", "QUANTITY", "PROJECT NAME"],
        "RFQ": ["Project Details:", "Detailed Scope of Work – (Needs to include details of material, services and manpower clearly)", "Quotation  Expected  Date:"],
        "Site Staff List": ["HR", "Premises", "Designation"],
        "Staff List Management": ["HR", "Premises", "Designation"],
        "2023 Survey Details": ["Overall satisfaction with Al Bonian Facilities Management"],
        "Monthly Reports": ["Status", "Submission"],
    }
    for column in checks.get(table, []):
        try:
            missing = normalized(frame[resolve(frame, column)]).eq("")
            if missing.any():
                insights.append(f"Complete '{column}' for {int(missing.sum()):,} records ({100 * missing.mean():.1f}% of this selection).")
        except KeyError:
            continue
    if not insights:
        insights.append("No configured exception was detected for this selection. Review the status breakdown and underlying records; this is not a certification that all records are correct.")
    return metrics, insights


def field_reference(field):
    """Decode a native Power BI field without guessing a table or column."""
    if "Aggregation" in field:
        agg = field["Aggregation"]
        value = field_reference(agg["Expression"])
        return dict(value, aggregation=agg["Function"])
    for kind in ("Column", "Measure"):
        if kind in field:
            value = field[kind]
            entity = value.get("Expression", {}).get("SourceRef", {}).get("Entity")
            if not entity:
                raise ValueError("Unresolved source alias")
            return {"table": entity, "column": value["Property"], "measure": kind == "Measure"}
    raise ValueError("Date hierarchy or expression needs explicit conversion")


def aggregate(series, function):
    if function == 2:
        return int(series.nunique())
    if function == 5:
        return int(series.count())
    numeric = pd.to_numeric(series, errors="coerce")
    if function == 0:
        return float(numeric.sum(min_count=1))
    if function == 1:
        return float(numeric.mean())
    if function == 3:
        return series.min()
    if function == 4:
        return series.max()
    raise ValueError("Unsupported aggregation")


def source_filters(frame, container, table):
    """Apply only fully understood native categorical conditions."""
    result, labels = frame, []
    for entry in container.get("filterConfig", {}).get("filters", []):
        payload = entry.get("filter")
        if not payload:
            continue
        aliases = {x["Name"]: x["Entity"] for x in payload.get("From", [])}
        def predicate(condition):
            if "Not" in condition:
                previous = len(labels)
                inverted = ~predicate(condition["Not"]["Expression"])
                for index in range(previous, len(labels)):
                    labels[index] = "Exclude " + labels[index]
                return inverted
            if "And" in condition or "Or" in condition:
                key = "And" if "And" in condition else "Or"
                value = condition[key]
                left, right = predicate(value["Left"]), predicate(value["Right"])
                return left & right if key == "And" else left | right
            if "In" not in condition:
                raise ValueError("Unsupported saved filter condition")
            value = condition["In"]
            if len(value["Expressions"]) != 1:
                raise ValueError("Multi-column saved filter")
            field = value["Expressions"][0].get("Column")
            if not field:
                raise ValueError("Saved filter expression requires conversion")
            source = field["Expression"]["SourceRef"]
            if source.get("Entity", aliases.get(source.get("Source"))) != table:
                raise ValueError("Cross-table saved filter")
            column = resolve(result, field["Property"])
            literals = []
            for row in value["Values"]:
                literal = row[0]["Literal"]["Value"]
                if literal == "null":
                    literals.append(None)
                elif literal.startswith("'") and literal.endswith("'"):
                    literals.append(literal[1:-1].replace("''", "'"))
                else:
                    raise ValueError("Non-text saved filter requires conversion")
            series = normalized(result[column])
            mask = series.isin([v.strip().upper() for v in literals if v is not None])
            if None in literals:
                mask |= result[column].isna()
            labels.append(column + ": " + ", ".join("(Blank)" if v is None else v for v in literals))
            return mask
        for clause in payload.get("Where", []):
            result = result.loc[predicate(clause["Condition"])]
    return result, labels
