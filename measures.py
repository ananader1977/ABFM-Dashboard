"""Explicitly translated source measures; unsupported measures raise, never guess."""
import pandas as pd
from analytics import dates, normalized, numbers, resolve, today, work_order_metrics


def measure(name, frame, table):
    def col(n):
        return frame[resolve(frame, n)]
    def total(n):
        return float(numbers(frame, n).sum())
    def count_status(value):
        return int(normalized(col("Status")).eq(value.upper()).sum())
    def ratio(a, b):
        return a / b if b else float("nan")
    if table == "Reactive":
        for label, code in (("Reactive", "BREAKDOWN"), ("PPM", "PPM"), ("Corrective", "SCHEDULEDTASK")):
            selected = frame.loc[normalized(col("Task Type")).eq(code)]
            m = work_order_metrics(selected)
            key = {f"Total {label}": "Total work orders", f"Completed {label}": "Completed", f"Pending {label}": "Open", f"Opened {label}": "Open", f"Cancelled {label}": "Cancelled"}.get(name)
            if key:
                return m[key]
            if name == label + " %":
                return ratio(m["Completed"], m["Total work orders"] - m["Cancelled"])
        if name == "Completed more than 1 documents":
            return int(numbers(frame, "Documents").gt(1).sum())
        if name == "Completed more than 1 Resources":
            return int(numbers(frame, "Resources").gt(0).sum())
    if table == "KPI":
        if name == "Actual KPI":
            return float(numbers(frame, "KPI Mark").mean())
        if name == "Target KPI":
            return float(numbers(frame, "Target").mean())
        if name == "Results":
            return ratio(measure("Actual KPI", frame, table), measure("Target KPI", frame, table))
    if table == "2023 Survey Details":
        if name == "Total Survey":
            return int((col("Task ID").notna() & normalized(col("Task ID")).ne("XX")).sum())
        if name == "Survey Response":
            return int(col("Reported").count())
        for label, field in {"Quality": "Quality of our work", "Response": "Our response time", "Courtesy": "Courtesy and professionalism of our personnel", "Overall": "Overall satisfaction with Al Bonian Facilities Management"}.items():
            value = normalized(col(field))
            satisfied = int(value.isin({"SATISFIED", "VERY SATISFIED"}).sum())
            unsatisfied = int(value.eq("NOT SATISFIED").sum())
            if name == "Satisfied " + label:
                return satisfied
            if name == "NotSatisfied " + label:
                return unsatisfied
            if name == "Satisfied " + label + " %":
                return ratio(satisfied, satisfied + unsatisfied)
    if table == "Cheque Requests":
        if name == "Total Cheques":
            return total("Cheque Req Amount - AED")
        if name in {"PDC", "CDC", "Open Credit"}:
            selected = normalized(col("Payment Type CDC & Advance PDC against Security Cheque")).eq(name.upper())
            return float(numbers(frame, "Cheque Req Amount - AED").loc[selected].sum())
        if name in {"Budget CDC", "Budget PDC"}:
            # DAX DISTINCTCOUNT includes a blank category.
            return int(col("cheque request date1").nunique(dropna=False)) * (400000 if name == "Budget CDC" else 600000)
        if name == "CDC %":
            return ratio(measure("CDC", frame, table), measure("Total Cheques", frame, table))
    if table == "Assets Tasks":
        if name in {"Asset PPM", "Asset Reactive", "Asset Scheduled"}:
            code = {"Asset PPM": "PPM", "Asset Reactive": "BREAKDOWN", "Asset Scheduled": "SCHEDULEDTASK"}[name]
            return int(col("Asset Code").loc[normalized(col("Task Type")).eq(code)].count())
        if name == "Manpower Costs":
            return total("Manpower costs")
        if name == "One Off Jobs Costs":
            return total("Material Costs")
        if name == "Assets Actual Costs":
            return total("Manpower costs") + total("Material Costs")
    if table == "Asset Details":
        for key, field, value in (("Valid Warranty", "Warranty Validity Col", "VALID"), ("In-Life", "Asset Valid", "IN-LIFE"), ("Extended-Life", "Asset Valid", "EXTENDED-LIFE")):
            if name == key:
                return int(normalized(col(field)).eq(value).sum())
    if table == "AMC":
        if name in {"Status Signed", "Status Closed"}:
            return count_status(name.split()[1])
        if name == "Total Status":
            return int(col("Status").count())
        if name in {"Signed %", "Closed %"}:
            return ratio(count_status(name.split()[0]), int(col("Status").count()))
        if name in {"Red Status Count", "Orange Status Count", "Yellow Status Count"}:
            days = (dates(col("Ending Date")) - today()).dt.days
            condition = days.lt(0) if name.startswith("Red") else days.between(0, 30) if name.startswith("Orange") else days.between(31, 60)
            return int(condition.sum())
    if table == "RFQ":
        if name == "RFQ Closed %":
            statuses = normalized(col("Status"))
            return ratio(int(statuses.isin({"COMPLETED", "CANCELLED", "QUOTATION RECEIVED"}).sum()), int(col("Status").count()))
        for priority, days in (("Emergency", 2), ("Urgent", 3), ("Normal", 5)):
            if name == priority + " Delayed Count":
                mask = normalized(col("Priority:")).eq(f"{priority.upper()} ({days} DAYS)") & numbers(frame, "Delay Days").gt(days) & ~normalized(col("Status")).isin({"CANCELLED", "COMPLETED", "QUOTATION RECEIVED"})
                return int(mask.sum())
    if table == "Site Staff List":
        total_staff = int(col("Name").nunique(dropna=False))
        if name == "Staff total":
            return total_staff
        for key, status in (("Staff On Work", "WORK"), ("Staff On Leave", "LEAVE")):
            if name == key:
                return int(col("Name").loc[normalized(col("Status")).eq(status)].count())
        if name == "manpower%":
            return ratio(measure("Staff On Work", frame, table), total_staff)
        if name == "manpower on leave%":
            return ratio(measure("Staff On Leave", frame, table), total_staff)
    if table == "Staff List Management" and name in {"Leave in 15 Days", "Leave in 30 Days", "Leave_Today"}:
        active = normalized(col("IsActive")).isin({"1", "1.0"})
        starts = dates(col("AL Starts"))
        if name == "Leave_Today":
            condition = active & normalized(col("Status")).eq("LEAVE") & starts.le(today()) & dates(col("AL Ends")).ge(today())
        else:
            condition = active & starts.between(today(), today() + pd.Timedelta(days=15 if "15" in name else 30))
        return int(col("HR").loc[condition].nunique(dropna=False))
    raise ValueError(f"Unconverted measure '{name}' for dataset '{table}'")
