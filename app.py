import base64
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import time

import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import PAGE_TABLES, TASK_TYPES, aggregate, dates, field_reference, resolve, summarize, task_frame, source_filters
from brand import LOGO
from security import authenticate, connect, current_user, save_user, rename_user
from measures import measure

ROOT = Path(os.environ.get("ABFM_DATA_DIR", Path(__file__).parent / "data"))
st.set_page_config(page_title="Al Bonian FM | Operations Intelligence", page_icon="🏢", layout="wide")
st.markdown("""<style>
:root { --navy:#102b4e; }
.stApp {background:#f4f6fa;color:#102b4e}
[data-testid="stSidebar"] {background:#102b4e}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3 {color:white!important}
[data-testid="stMetric"] {background:white;border:1px solid #e1e6ee;border-radius:12px;padding:18px}
h1,h2,h3 {color:#102b4e}
.brand {background:#102b4e;padding:22px;border-radius:12px;margin-bottom:18px}
.brand img {max-width:280px;width:100%}
</style>""", unsafe_allow_html=True)


def brand():
    st.markdown(f'<div class="brand"><img src="data:image/png;base64,{LOGO}" alt="Al Bonian FM"></div>', unsafe_allow_html=True)


def login():
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        brand()
        st.title("Operations intelligence")
        st.caption("Al Bonian Facilities Management • Secure access")
        with st.form("login"):
            username = st.text_input("Username", max_chars=100)
            password = st.text_input("Password", type="password", max_chars=256)
            submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            user = authenticate(ROOT, username, password)
            if user:
                st.session_state.identity = dict(user, expires=time.time() + 8 * 3600)
                st.rerun()
            st.error("Invalid credentials or account temporarily locked. After five failed attempts, wait 15 minutes.")
        with connect(ROOT) as db:
            if not db.execute("SELECT count(*) FROM users").fetchone()[0]:
                st.info("Account setup is required on the server before sign-in is available.")


@st.cache_resource(show_spinner=False, max_entries=1)
def read_table(path, modified):
    # Arrow-backed strings avoid expanding the largest table into hundreds of MB.
    # Callers create their own selection before changing any data.
    return pd.read_parquet(path, dtype_backend="pyarrow")


def snapshot():
    pointer = ROOT / "current.txt"
    if not pointer.exists():
        return None, None
    name = pointer.read_text().strip()
    if Path(name).name != name:
        raise ValueError("Invalid snapshot pointer")
    directory = ROOT / name
    return directory, json.loads((directory / "manifest.json").read_text())


def admin_panel(user):
    st.header("Administration")
    st.caption("Operations users can view dashboards only. Account and import controls require administrator access.")
    with connect(ROOT) as db:
        users = pd.read_sql_query("SELECT username,role,active FROM users ORDER BY username", db)
    st.dataframe(users, hide_index=True, use_container_width=True)
    with st.form("accounts", clear_on_submit=True):
        username = st.text_input("Username to create or update", max_chars=100)
        role = st.selectbox("Role", ["operations", "admin"])
        password = st.text_input("New password (12+ characters; leave blank to retain existing password)", type="password", max_chars=256)
        active = st.checkbox("Account active", value=True)
        if st.form_submit_button("Save account"):
            try:
                save_user(ROOT, user["username"], username, role, password or None, active)
                st.success("Account saved. Existing sessions for this account will be invalidated.")
            except (ValueError, PermissionError) as e:
                st.error(str(e))
    with st.form("rename_account", clear_on_submit=True):
        old = st.selectbox("Account to rename", list(users.username))
        new = st.text_input("Replacement username", max_chars=100)
        if st.form_submit_button("Rename account"):
            try:
                rename_user(ROOT, user["username"], old, new)
                st.success("Username changed. Existing sessions for this account have been invalidated.")
            except (ValueError, PermissionError) as e:
                st.error(str(e))
    st.subheader("Refresh dashboard snapshot")
    st.caption("Imports replace the current snapshot. Power BI service refreshes do not automatically update this application. Legacy User_Security credentials are excluded.")
    uploaded = st.file_uploader("Replacement Power BI file", type=["pbix"])
    if uploaded and st.button("Import replacement data", type="primary"):
        from import_pbix import extract
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pbix"
            source.write_bytes(uploaded.getbuffer())
            try:
                with st.spinner("Extracting and validating snapshot…"):
                    result = extract(source, ROOT)
                st.cache_resource.clear()
                st.success(f"Imported {result['tables']} tables and {result['pages']} pages.")
            except Exception:
                st.error("Import failed. The current snapshot was retained. Check that the PBIX contains an embedded data model.")
    with st.expander("Account audit log"):
        with connect(ROOT) as db:
            audit = pd.read_sql_query("SELECT datetime(at,'unixepoch') AS utc_time, actor,action,target FROM audit ORDER BY at DESC LIMIT 100", db)
        st.dataframe(audit, hide_index=True, use_container_width=True)


def filters(frame, table):
    result = frame
    with st.expander("Filter this dataset", expanded=False):
        available = [c for c in frame.columns if not c.startswith("_")]
        selected = st.multiselect("Choose filter fields", available, key=f"fields_{table}")
        for col in selected:
            series = frame[col]
            if pd.api.types.is_datetime64_any_dtype(series) or str(series.dtype).startswith("timestamp["):
                valid = dates(series).dropna()
                if len(valid):
                    lower, upper = valid.min().date(), valid.max().date()
                    span = st.date_input(col, value=(lower, upper), min_value=lower, max_value=upper, key=f"date_{table}_{col}")
                    if len(span) == 2:
                        values = dates(result[col])
                        result = result.loc[values.ge(pd.Timestamp(span[0])) & values.lt(pd.Timestamp(span[1]) + pd.Timedelta(days=1))]
            elif series.nunique() <= 500:
                choices = sorted(series.dropna().astype(str).unique())
                chosen = st.multiselect(col, choices, key=f"filter_{table}_{col}")
                if chosen:
                    result = result.loc[result[col].astype(str).isin(chosen)]
            else:
                text = st.text_input(f"{col} contains", key=f"search_{table}_{col}")
                if text:
                    result = result.loc[result[col].astype(str).str.contains(text, case=False, regex=False, na=False)]
        st.caption("All metrics, action highlights, charts, and the record table below use this selection. Filters apply to this dataset only.")
    return result


def metric_cards(metrics):
    entries = list(metrics.items())
    for offset in range(0, len(entries), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, entries[offset:offset + 4]):
            display = "—" if value is None else f"{value:,.1f}%" if "%" in label else f"{value:,.2f}" if "AED" in label else f"{value:,.0f}"
            column.metric(label, display)


def source_charts(page, frame, table):
    supported = {"barChart", "clusteredBarChart", "stackedBarChart", "columnChart", "clusteredColumnChart", "stackedColumnChart", "hundredPercentStackedBarChart", "hundredPercentStackedColumnChart", "lineChart", "areaChart", "pieChart", "donutChart", "card", "cardVisual"}
    skips, rendered = [], 0
    for container in sorted(page["visuals"], key=lambda x: (x.get("position", {}).get("y", 0), x.get("position", {}).get("x", 0))):
        visual = container.get("visual", {})
        kind = visual.get("visualType")
        if kind not in supported:
            continue
        state = visual.get("query", {}).get("queryState", {})
        refs = []
        try:
            chart_frame, source_filter_labels = source_filters(frame, container, table)
            for role, specification in state.items():
                for projection in specification.get("projections", []):
                    ref = field_reference(projection["field"])
                    if ref["table"] != table and not ref.get("measure"):
                        raise ValueError("Cross-table or measure calculation requires conversion")
                    if ref.get("measure"):
                        measure(ref["column"], frame, table)
                    else:
                        ref["column"] = resolve(frame, ref["column"])
                    refs.append(dict(ref, role=role))
            if not refs:
                continue
            dimensions = [r for r in refs if "aggregation" not in r and not r.get("measure")]
            values = [r for r in refs if "aggregation" in r or r.get("measure")]
            if not values:
                raise ValueError("No explicit numerical aggregation")
            if len(dimensions) > 1:
                raise ValueError("Multi-series chart requires conversion")
            value = values[0]
            def evaluate(data, value):
                return measure(value["column"], data, table) if value.get("measure") else aggregate(data[value["column"]], value["aggregation"])
            title = " / ".join(v['column'] for v in values) + (f" by {dimensions[0]['column']}" if dimensions else "")
            if source_filter_labels:
                st.caption("Source visual filters: " + " · ".join(source_filter_labels))
            if not dimensions:
                for value in values:
                    result = evaluate(chart_frame, value)
                    st.metric(value['column'], f"{result:,.1%}" if "%" in value["column"] else f"{result:,.2f}")
            else:
                dim = dimensions[0]["column"]
                grouped = pd.DataFrame([{dim: key, "Value": evaluate(group, value), "Series": value['column']} for key, group in chart_frame.groupby(dim, dropna=False) for value in values])
                if grouped.empty:
                    continue
                grouped[dim] = grouped[dim].fillna("(Blank)").astype(str)
                grouped = grouped.sort_values("Value", ascending=False)
                if grouped[dim].nunique() > 25:
                    chosen = grouped.groupby(dim).Value.sum().nlargest(25).index
                    grouped = grouped.loc[grouped[dim].isin(chosen)]
                    title += " • top 25 categories"
                st.subheader(title)
                if kind in {"pieChart", "donutChart"} and len(values) == 1 and grouped.Value.ge(0).all():
                    figure = px.pie(grouped, names=dim, values="Value", hole=.55 if kind == "donutChart" else 0, color_discrete_sequence=px.colors.sequential.Blues_r)
                elif kind in {"lineChart", "areaChart"}:
                    figure = px.line(grouped.sort_values(dim), x=dim, y="Value", color="Series", markers=True, color_discrete_sequence=px.colors.sequential.Blues_r)
                else:
                    if kind.startswith('hundredPercent'):
                        denominators = grouped.groupby(dim).Value.transform('sum')
                        grouped['Value'] = grouped.Value.div(denominators.where(denominators.ne(0))) * 100
                    figure = px.bar(grouped, x="Value", y=dim, color="Series", orientation="h", barmode="group" if kind.startswith('clustered') else 'relative', text_auto=".3s", color_discrete_sequence=["#173c68", "#6889ac", "#b7c9dd"])
                figure.update_layout(paper_bgcolor="white", plot_bgcolor="white", margin=dict(l=10, r=10, t=20, b=10), height=max(300, min(700, 32 * len(grouped))))
                st.plotly_chart(figure, use_container_width=True, key=f"native_{container['name']}")
            rendered += 1
        except (KeyError, ValueError, TypeError) as e:
            skips.append({"Visual": container.get("name"), "Type": kind, "Reason": str(e)})
    st.caption(f"{rendered} source visual(s) converted for this dataset. Original saved slicer selections are reset; use the application's filters.")
    return skips


def run():
    user = current_user(ROOT, st.session_state.get("identity"))
    if not user:
        st.session_state.pop("identity", None)
        login()
        return
    with st.sidebar:
        brand()
        st.caption(f"{user['username']} · {user['role']}")
        st.title("FM Dashboard")
        directory, manifest = snapshot()
        names = [p["name"] for p in manifest["pages"]] if manifest else []
        options = names + (["Administration"] if user["role"] == "admin" else [])
        page_name = st.radio("Workspace", options) if options else None
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        st.caption("Calculated insights • No external AI")
    if page_name == "Administration":
        admin_panel(user)
        return
    if not manifest:
        st.info("No data snapshot has been imported. An administrator can import the Power BI file.")
        return
    page = next(p for p in manifest["pages"] if p["name"] == page_name)
    st.caption("AL BONIAN FM / OPERATIONS INTELLIGENCE")
    st.title(page_name)
    st.caption(f"Snapshot imported {manifest['imported_at'][:19]} UTC • No automatic connection to Power BI service")
    tables = [t for t in PAGE_TABLES.get(page_name, []) if t in manifest["tables"]]
    if not tables:
        st.warning("The source tables for this page are unavailable. Charts have been skipped.")
        return
    table = st.selectbox("Dataset", tables) if len(tables) > 1 else tables[0]
    info = manifest["tables"][table]
    path = directory / info["file"]
    frame = task_frame(read_table(str(path), path.stat().st_mtime_ns), page_name)
    selected = filters(frame, table)
    st.caption(f"{len(selected):,} selected records / {len(frame):,} records in this tab • Dataset: {table}")
    metrics, insights = summarize(selected, table, page_name)
    metric_cards(metrics)
    with st.container(border=True):
        st.subheader("Action highlights")
        st.caption("Calculated rules from the selected data. No generative AI or external data transfer.")
        for insight in insights:
            st.write("• " + insight)
    overview, converted, records, methodology = st.tabs(["Overview", "Source charts", "Records", "Definitions & skipped charts"])
    with overview:
        candidates = [c for c in selected.columns if c.strip().casefold() in {"status", "category", "priority", "project name", "contract title", "contract in charge", "building", "trade", "segmet", "vendor_category", "mr status", "categories"}]
        if candidates and len(selected):
            grouping = st.selectbox("Breakdown by", candidates, key=f"breakdown_{page_name}")
            distribution = selected[grouping].fillna("(Blank)").astype(str).replace("", "(Blank)").value_counts().rename_axis(grouping).reset_index(name="Records")
            figure = px.bar(distribution.head(25), x="Records", y=grouping, orientation="h", text="Records", color_discrete_sequence=["#173c68"])
            figure.update_layout(yaxis={"categoryorder": "total ascending"}, paper_bgcolor="white", plot_bgcolor="white", height=max(350, min(800, len(distribution.head(25)) * 32)))
            st.plotly_chart(figure, use_container_width=True)
            st.caption("Top 25 categories displayed. Counts represent source rows, not automatically distinct jobs or people.")
        else:
            st.info("No configured categorical breakdown is available for this dataset.")
    with converted:
        skipped = source_charts(page, selected, table)
    with records:
        visible = st.multiselect("Visible columns", list(selected.columns), default=list(selected.columns[:10]), key=f"columns_{page_name}_{table}")
        page_size = 100
        page_count = max(1, (len(selected) + page_size - 1) // page_size)
        page_number = st.number_input("Record page", min_value=1, max_value=page_count, value=1, key=f"record_page_{page_name}_{len(selected)}")
        start = (int(page_number) - 1) * page_size
        st.dataframe(selected.iloc[start:start + page_size][visible], hide_index=True, use_container_width=True)
        st.caption(f"Page {page_number:,} of {page_count:,}. View-only data; no record editing is enabled.")
    with methodology:
        st.write("Work orders: BREAKDOWN → Reactive; PPM → PPM; SCHEDULEDTASK → Corrective. COMPLETE and HISTORY are completed; ACTIVE and ASSIGNED are open; CANCELLED is separate. Completion percentage = completed ÷ (total − cancelled). Unknown statuses remain separate.")
        st.write("Dates are evaluated against today's date in Dubai. Imported calculated columns retain their snapshot values. Figures count rows unless labelled otherwise. Each dataset is filtered independently; no cross-table relationship or DAX result is inferred.")
        st.write("The original page names and source tables are retained. The application uses a responsive layout; Power BI custom visuals, bookmarks, drill-through, complex saved visual conditions, and unsupported DAX calculations are not reproduced automatically. Missing-field charts are skipped.")
        if manifest.get("import_warnings"):
            st.write("Import exceptions: invalid source dates were retained as missing values.")
            st.dataframe(pd.DataFrame(manifest["import_warnings"]), hide_index=True)
        if skipped:
            st.dataframe(pd.DataFrame(skipped), hide_index=True, use_container_width=True)
        else:
            st.caption("No skipped supported chart types were recorded for this dataset. Custom visual types and decorative objects are outside automatic conversion.")


run()
