import base64
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import time
import gc

import pandas as pd
import streamlit as st

from analytics import PAGE_TABLES, TASK_TYPES, aggregate, dates, field_reference, resolve, summarize, task_frame, source_filters
from brand import LOGO
from security import authenticate, connect, current_user, save_user, rename_user
from interactive import linked_charts, quick_filters, records_panel
from work_orders import install_replacement
from measures import measure
from data_access import load_table, load_rows, display_columns, source_visuals, SOURCE_VISUAL_TYPES
from visuals import CSS, page_header, polish, overview
from executive import PAGE as EXECUTIVE_PAGE, render as executive_summary, scope_frame, clear_scope

ROOT = Path(os.environ.get("ABFM_DATA_DIR", Path(__file__).parent / "data"))
REMOVED_PAGES = {"Asset Details", "KPI & MR", "Quotations", "Cheque Request", "Cheque Requests", "QHSE", "Manpower", "Manpower Management"}
st.set_page_config(page_title="Al Bonian FM | Operations Intelligence", page_icon="🏢", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


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


def read_table(path, modified, task_type=None, columns=None):
    # Filter inside the file scanner, before allocating a DataFrame. Retaining a
    # previous full-table cache while loading the next tab can exceed 512 MB.
    return load_table(path, task_type=task_type, columns=columns)


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
    st.subheader("Replace work-order data")
    st.caption("Makes this workbook the only active dataset using the approved fields and status rules. Previous snapshots are retained for recovery. Upload the prepared Parquet version of the workbook.")
    replacement = st.file_uploader("Prepared work-order data", type=["parquet"], key="replacement_work_order_upload")
    if replacement and st.button("Apply replacement work orders"):
        try:
            with st.spinner("Validating and saving work orders…"):
                report = install_replacement(replacement, ROOT)
            st.cache_data.clear()
            st.success(f"Replaced work orders: {report['rows']:,} records. Only workbook data is active.")
        except (ValueError, KeyError) as error:
            st.error(str(error))
    with st.expander("Snapshot recovery"):
        st.caption("Previous snapshots are retained as a recoverable backup and are excluded from the active dashboard.")
        current_name = (ROOT / 'current.txt').read_text().strip() if (ROOT / 'current.txt').exists() else ''
        backups = sorted([p.name for p in ROOT.glob('snapshot-*') if (p / 'manifest.json').exists() and p.name != current_name])
        if backups:
            recover = st.selectbox('Previous snapshot', backups)
            if st.button('Restore selected snapshot'):
                from work_orders import restore_snapshot
                restore_snapshot(ROOT, recover)
                st.cache_data.clear()
                st.rerun()
    with st.expander("Account audit log"):
        with connect(ROOT) as db:
            audit = pd.read_sql_query("SELECT datetime(at,'unixepoch') AS utc_time, actor,action,target FROM audit ORDER BY at DESC LIMIT 100", db)
        st.dataframe(audit, hide_index=True, use_container_width=True)


def filters(frame, table, source_columns=None):
    result = frame
    with st.expander("Filter this dataset", expanded=False):
        available = [c for c in (source_columns if source_columns is not None else frame.columns) if not c.startswith("_")]
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
                choices = sorted(series.dropna().astype('string[pyarrow]').unique())
                chosen = st.multiselect(col, choices, key=f"filter_{table}_{col}")
                if chosen:
                    result = result.loc[result[col].astype('string[pyarrow]').isin(chosen)]
            else:
                text = st.text_input(f"{col} contains", key=f"search_{table}_{col}")
                if text:
                    result = result.loc[result[col].astype('string[pyarrow]').str.contains(text, case=False, regex=False, na=False)]
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
    supported = SOURCE_VISUAL_TYPES
    skips, rendered = [], 0
    containers = source_visuals(page)
    if not containers:
        st.info("No supported source visuals are available for this dataset.")
        return skips
    labels = {}
    for c in containers:
        queries = c.get("visual", {}).get("query", {}).get("queryState", {})
        fields = [p.get("nativeQueryRef", p.get("queryRef", "")) for spec in queries.values() for p in spec.get("projections", [])]
        labels[c['name']] = " / ".join(fields)[:150] or c.get("visual", {}).get("visualType", "Chart")
    chosen = st.selectbox("Source chart", [c['name'] for c in containers], format_func=lambda name: labels[name], key=f"source_chart_{page['name']}_{table}")
    st.caption("Charts load individually to keep memory use within the server limit.")
    for container in containers:
        if container['name'] != chosen:
            continue
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
                grouped[dim] = grouped[dim].fillna("(Blank)").astype('string[pyarrow]')
                grouped = grouped.sort_values("Value", ascending=False)
                if grouped[dim].nunique() > 25:
                    chosen = grouped.groupby(dim).Value.sum().nlargest(25).index
                    grouped = grouped.loc[grouped[dim].isin(chosen)]
                    title += " • top 25 categories"
                st.subheader(title)
                import plotly.express as px
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
                st.plotly_chart(polish(figure, height=max(300, min(700, 32 * len(grouped)))), width='stretch', key=f"native_{container['name']}")
            rendered += 1
        except (KeyError, ValueError, TypeError) as e:
            skips.append({"Visual": container.get("name"), "Type": kind, "Reason": str(e)})
    st.caption(f"{rendered} selected source visual(s) converted. Original saved slicer selections are reset; use the application's filters.")
    return skips


def run():
    # Plotly figures contain reference cycles. Reclaim prior views before reading
    # the next dataset so repeated chart changes do not retain their buffers.
    gc.collect()
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
        available_pages = [p["name"] for p in manifest["pages"] if p["name"] not in REMOVED_PAGES and p['name'] != EXECUTIVE_PAGE] if manifest else []
        names = [EXECUTIVE_PAGE] + available_pages if manifest else []
        options = names + (["Administration"] if user["role"] == "admin" else [])
        page_name = st.radio("Workspace", options, key='workspace_page') if options else None
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
    if page_name is None:
        st.info("No dashboard tabs are available in this snapshot.")
        return
    if page_name == EXECUTIVE_PAGE:
        executive_summary(directory,manifest,available_pages)
        return
    page = next(p for p in manifest["pages"] if p["name"] == page_name)
    page_header(page_name, manifest.get('work_order_source', {}).get('imported_at', manifest['imported_at']) if page_name in TASK_TYPES else manifest['imported_at'])
    if page_name in TASK_TYPES and manifest.get('work_order_source'):
        st.caption('Work-order source: Anas - All Work Orders.xlsx · Assigned Resource supplies technician. Scheduled, In Progress and Unscheduled are open; Completed is completed; Canceled is separate. This source has no verified due date, so overdue charts are skipped.')
    tables = [t for t in PAGE_TABLES.get(page_name, []) if t in manifest["tables"]]
    if not tables:
        st.warning("The source tables for this page are unavailable. Charts have been skipped.")
        return
    table = st.selectbox("Dataset", tables, key=f"dataset_{page_name}") if len(tables) > 1 else tables[0]
    info = manifest["tables"][table]
    path = directory / info["file"]
    view_requested = st.session_state.get(f"view_{page_name}", "Overview")
    choices = source_visuals(page)
    chosen = st.session_state.get(f"source_chart_{page_name}_{table}")
    visual = next((c for c in choices if c['name'] == chosen), choices[0] if choices else None)
    projection = display_columns(info['columns'], table, view_requested, st.session_state.get(f"fields_{table}", []), visual)
    frame = read_table(str(path), path.stat().st_mtime_ns, TASK_TYPES.get(page_name), projection)
    tab_total = len(frame)
    scope = st.session_state.get('executive_scope')
    if scope and scope.get('page') == page_name:
        st.caption('Executive Summary selection · showing matching action records')
        st.button('Show all records in this workspace',key='clear_executive_selection',on_click=clear_scope)
        try:
            frame = scope_frame(frame,scope['rule'])
        except KeyError:
            st.warning('This snapshot no longer contains the fields for that executive rule. Clear the selection to view all records.')
            st.stop()
    if table == 'Reactive' and 'Technician · Assigned Resource' not in frame:
        st.info('This older snapshot has no verified Assigned Resource or Contract Manager fields. No names have been inferred.')
    frame = quick_filters(frame, page_name)
    selected = filters(frame, table, list(frame.columns))
    selected = linked_charts(selected, table, page_name)
    st.caption(f"{len(selected):,} selected records / {tab_total:,} records in this tab • Dataset: {table}")
    metrics, insights = summarize(selected, table, page_name)
    metric_cards(metrics)
    with st.expander(f"Action highlights · {len(insights)} review point(s)", expanded=False):
        st.caption("Calculated from the current selection.")
        for insight in insights:
            st.write("• " + insight)
    # Streamlit tabs eagerly execute every panel, even when hidden. Render only
    # the chosen view so login does not build all source figures at once.
    view = st.radio("View", ["Overview", "Source charts", "Records", "Definitions & skipped charts"], horizontal=True, key=f"view_{page_name}")
    if view == "Overview":
        st.caption("The compact charts above filter all metrics and the records below.")
    elif view == "Source charts":
        skipped = source_charts(page, selected, table)
        if skipped:
            st.dataframe(pd.DataFrame(skipped), hide_index=True, use_container_width=True)
    elif view == "Records":
        visible = st.multiselect("Visible columns", info['columns'], default=info['columns'][:10], key=f"columns_{page_name}_{table}")
        page_size = 100
        page_count = max(1, (len(selected) + page_size - 1) // page_size)
        page_number = st.number_input("Record page", min_value=1, max_value=page_count, value=1, key=f"record_page_{page_name}_{len(selected)}")
        start = (int(page_number) - 1) * page_size
        if visible:
            displayed = load_rows(str(path), TASK_TYPES.get(page_name), visible, selected.index[start:start + page_size])
            st.dataframe(displayed, hide_index=True, use_container_width=True)
        else:
            st.info("Choose at least one visible column.")
        st.caption(f"Page {page_number:,} of {page_count:,}. View-only data; no record editing is enabled.")
    elif view == "Definitions & skipped charts":
        st.write("Work orders: BREAKDOWN → Reactive; PPM → PPM; SCHEDULEDTASK → Corrective. COMPLETE and HISTORY are completed; ACTIVE and ASSIGNED are open; CANCELLED is separate. Completion percentage = completed ÷ (total − cancelled). Replacement workbook: Reactive → BREAKDOWN; PPM → PPM; Corrective → SCHEDULEDTASK; Pro-active → PROACTIVE. Scheduled and Unscheduled → ACTIVE; In Progress → ASSIGNED; Completed → COMPLETE; Canceled → CANCELLED. Unknown statuses remain separate.")
        st.write("Dates are evaluated against today's date in Dubai. Imported calculated columns retain their snapshot values. Figures count rows unless labelled otherwise. Each dataset is filtered independently; no cross-table relationship or DAX result is inferred.")
        st.write("The original page names and source tables are retained. The application uses a responsive layout; Power BI custom visuals, bookmarks, drill-through, complex saved visual conditions, and unsupported DAX calculations are not reproduced automatically. Missing-field charts are skipped.")
        if manifest.get("import_warnings"):
            st.write("Import exceptions: invalid source dates were retained as missing values.")
            st.dataframe(pd.DataFrame(manifest["import_warnings"]), hide_index=True)
        st.caption("Select a visual under Source charts to see its conversion result or the reason it was skipped. Custom visual types and decorative objects are outside automatic conversion.")
    records_panel(selected, path, table, page_name, info['columns'], TASK_TYPES.get(page_name))


run()
