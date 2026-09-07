import base64
import calendar as py_calendar
from datetime import date, datetime, time
import io
import json
import sqlite3
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

# 預設固定名單與參數
EMPLOYEES = ["伊臻", "美釵", "涵玟", "勝順"]
EVENT_RESPONSIBLES = ["全體", "伊臻", "美釵", "涵玟", "勝順"]
DEVICES = ["平板-1", "平板-2", "平板-3", "投影機"]
DEFAULT_SUPPLIES = [
    "酒精", "漂白水", "衛生紙", "擦手紙", "洗手乳",
    "垃圾袋(小)", "垃圾袋(中)", "垃圾袋(大)", "廁所清潔劑", "洗碗精"
]

LOCAL_DB = "backup_storage.db"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# 初始化本地備援資料庫
def init_local_fallback():
    conn = sqlite3.connect(LOCAL_DB)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS fallback_store (sheet_name TEXT PRIMARY KEY, json_data TEXT)")
    conn.commit()
    conn.close()

init_local_fallback()

def save_local_fallback(sheet_name: str, df: pd.DataFrame):
    try:
        conn = sqlite3.connect(LOCAL_DB)
        c = conn.cursor()
        c.execute("REPLACE INTO fallback_store (sheet_name, json_data) VALUES (?, ?)", 
                  (sheet_name, df.to_json(orient="records", force_ascii=False)))
        conn.commit()
        conn.close()
    except Exception:
        pass

def load_local_fallback(sheet_name: str, default_cols: list) -> pd.DataFrame:
    try:
        conn = sqlite3.connect(LOCAL_DB)
        c = conn.cursor()
        row = c.execute("SELECT json_data FROM fallback_store WHERE sheet_name = ?", (sheet_name,)).fetchone()
        conn.close()
        if row and row[0]:
            return pd.read_json(io.StringIO(row[0])).fillna("").astype(str)
    except Exception:
        pass
    return pd.DataFrame(columns=default_cols)

@st.cache_resource
def get_gspread_client():
    """解析 Secrets 憑證並建立連線"""
    try:
        service_account_info = None
        if "gcp_base64" in st.secrets:
            raw = str(st.secrets["gcp_base64"]).strip().strip('"').strip("'")
            if len(raw) > 20:
                service_account_info = json.loads(base64.b64decode(raw).decode("utf-8"))
        elif "gcp_service_account" in st.secrets:
            service_account_info = dict(st.secrets["gcp_service_account"])
            if "private_key" in service_account_info:
                service_account_info["private_key"] = str(service_account_info["private_key"]).strip().strip("'").strip('"').replace("\\n", "\n")
        elif "gcp_json" in st.secrets:
            raw_str = str(st.secrets["gcp_json"]).strip().strip("'''").strip('"""')
            if len(raw_str) > 20:
                service_account_info = json.loads(raw_str)

        if not service_account_info or "spreadsheet_url" not in st.secrets:
            return None

        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_url(str(st.secrets["spreadsheet_url"]).strip().strip('"').strip("'"))
        return sheet
    except Exception:
        return None

def get_worksheet(sheet_name: str, default_cols: list):
    sh = get_gspread_client()
    if not sh:
        return None
    try:
        return sh.worksheet(sheet_name)
    except Exception:
        try:
            ws = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
            ws.append_row(default_cols)
            return ws
        except Exception:
            return None

def load_data(sheet_name: str, default_cols: list) -> pd.DataFrame:
    """載入資料：雲端 Google 試算表優先，本地備援同步"""
    ws = get_worksheet(sheet_name, default_cols)
    if ws:
        try:
            records = ws.get_all_records()
            if records:
                df = pd.DataFrame(records)
                for col in default_cols:
                    if col not in df.columns:
                        df[col] = ""
                df = df.fillna("").astype(str)
                save_local_fallback(sheet_name, df)
                return df
            else:
                df = pd.DataFrame(columns=default_cols)
                save_local_fallback(sheet_name, df)
                return df
        except Exception:
            pass
    return load_local_fallback(sheet_name, default_cols)

def save_data(sheet_name: str, df: pd.DataFrame):
    """雙重儲存：本地即時寫入 + 雲端同步"""
    save_local_fallback(sheet_name, df)
    ws = get_worksheet(sheet_name, df.columns.tolist())
    if ws:
        try:
            ws.clear()
            header = df.columns.tolist()
            values = df.fillna("").astype(str).values.tolist()
            ws.update(range_name="A1", values=[header] + values)
        except Exception as e:
            st.error(f"雲端試算表同步暫時延遲，本地已安全備份：{e}")

# 初始化物資預設庫存
def ensure_supplies_setup():
    df = load_data("supplies", ["item_name", "stock"])
    if df.empty or len(df) == 0:
        new_df = pd.DataFrame([{"item_name": item, "stock": "0"} for item in DEFAULT_SUPPLIES])
        save_data("supplies", new_df)
    else:
        existing = df["item_name"].astype(str).tolist()
        missing = [item for item in DEFAULT_SUPPLIES if item not in existing]
        if missing:
            add_df = pd.DataFrame([{"item_name": m, "stock": "0"} for m in missing])
            merged = pd.concat([df, add_df], ignore_index=True)
            save_data("supplies", merged)

try:
    ensure_supplies_setup()
except Exception:
    pass

# 頁面配置
st.set_page_config(page_title="內部行政管理系統", layout="wide")
st.title("🏢 公司內部行政管理系統")

# 狀態提示
sh_conn = get_gspread_client()
if sh_conn:
    st.sidebar.success("🟢 雲端 Google 試算表：連線同步中")
else:
    st.sidebar.info("🟠 儲存狀態：本地安全儲存模式 (切換頁面不遺失)")

menu = st.sidebar.radio(
    "系統模組切換",
    [
        "🗓️ 互動月曆視圖",
        "📤 匯出每月綜合報表",
        "📅 班表、排休與調班",
        "📌 工作行事登記",
        "📱 3C產品借用申請",
        "🖨️ 影印輸出登記",
        "📦 物資出入庫管理",
    ],
)

def get_daily_roster(query_date_str):
    q_date = datetime.strptime(query_date_str, "%Y-%m-%d")
    month = q_date.month
    if month % 2 != 0:
        roster = {"伊臻": "A班", "涵玟": "A班", "美釵": "B班", "勝順": "未排班"}
    else:
        roster = {"美釵": "A班", "伊臻": "B班", "涵玟": "B班", "勝順": "未排班"}

    df_swaps = load_data("shift_swaps", ["swap_date", "employee_name", "assigned_shift", "reason"])
    if not df_swaps.empty:
        swaps = df_swaps[df_swaps["swap_date"].astype(str) == str(query_date_str)]
        for _, row in swaps.iterrows():
            roster[str(row["employee_name"])] = str(row["assigned_shift"])
    return roster

# ==================== 模組 0: 互動月曆視圖 ====================
if menu == "🗓️ 互動月曆視圖":
    st.header("🗓️ 整合工作行事與同仁排休月曆")
    st.info("💡 藍色代表【工作事項】，橘色代表【同仁排休】。點擊月曆方塊可在下方查看詳細內容。")

    df_schedules = load_data("schedules", ["id", "employee_name", "leave_type", "start_date", "end_date", "start_datetime", "end_datetime", "note"])
    df_works = load_data("work_events", ["id", "start_date", "end_date", "start_time", "end_time", "title", "person_in_charge", "description"])

    calendar_events = []

    for _, row in df_schedules.iterrows():
        if not str(row["employee_name"]).strip():
            continue
        s_date = str(row["start_date"]).strip() if str(row["start_date"]).strip() else str(date.today())
        e_date = str(row["end_date"]).strip() if str(row["end_date"]).strip() else s_date
        calendar_events.append({
            "title": f"🏖️ {row['employee_name']} [{row['leave_type']}]",
            "start": s_date,
            "end": e_date,
            "backgroundColor": "#FF7A00",
            "borderColor": "#FF7A00",
            "textColor": "#FFFFFF",
            "extendedProps": {
                "類別": "🏖️ 同仁排休",
                "對象": str(row["employee_name"]),
                "項目": f"{row['leave_type']}假",
                "日期區間": f"{s_date} ~ {e_date}",
                "時間明細": f"{row['start_datetime']} 至 {row['end_datetime']}",
                "詳細內容/備註": str(row["note"]) if str(row["note"]).strip() else "無填寫備註",
            },
        })

    for _, row in df_works.iterrows():
        if not str(row["title"]).strip():
            continue
        s_d = str(row["start_date"]).strip() if str(row["start_date"]).strip() else str(date.today())
        e_d = str(row["end_date"]).strip() if str(row["end_date"]).strip() else s_d
        s_t = str(row["start_time"]).strip() if str(row["start_time"]).strip() else "09:00"
        e_t = str(row["end_time"]).strip() if str(row["end_time"]).strip() else "10:00"

        calendar_events.append({
            "title": f"💼 {s_t} [{row['person_in_charge']}] {row['title']}",
            "start": f"{s_d}T{s_t}:00",
            "end": f"{e_d}T{e_t}:00",
            "backgroundColor": "#1E88E5",
            "borderColor": "#1E88E5",
            "textColor": "#FFFFFF",
            "extendedProps": {
                "類別": "💼 工作行事",
                "對象": str(row["person_in_charge"]),
                "項目": str(row["title"]),
                "日期區間": f"{s_d} 至 {e_d}" if s_d != e_d else s_d,
                "時間明細": f"{s_t} ~ {e_t}",
                "詳細內容/備註": str(row["description"]) if str(row["description"]).strip() else "無詳細說明",
            },
        })

    calendar_options = {
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,listMonth",
        },
        "initialView": "dayGridMonth",
        "navLinks": True,
        "selectable": True,
        "editable": False,
        "locale": "zh-tw",
    }

    cal_out = calendar(
        events=calendar_events,
        options=calendar_options,
        custom_css=".fc-event-title { font-weight: 500; font-size: 0.85rem; }",
        key="main_calendar_view",
    )

    if cal_out and "eventClick" in cal_out:
        detail = cal_out["eventClick"]["event"].get("extendedProps", {})
        st.divider()
        st.markdown(f"### 📌 事項詳情：{detail.get('項目', '')}")
        col_c1, col_c2, col_c3 = st.columns(3)
        col_c1.metric("類別與性質", detail.get("類別", ""))
        col_c2.metric("負責人 / 請假同仁", detail.get("對象", ""))
        col_c3.metric("時間時段", detail.get("時間明細", ""))
        st.markdown(f"**🗓️ 活動日期：** `{detail.get('日期區間', '')}`")
        st.markdown("**📝 內容與說明：**")
        st.info(detail.get("詳細內容/備註", "無"))

    st.divider()
    st.subheader("📋 近期實際行事與排休總覽清單")
    list_tab1, list_tab2 = st.tabs(["💼 實際工作行事清單", "🏖️ 同仁排休明細"])
    with list_tab1:
        st.dataframe(df_works.tail(30).iloc[::-1], width="stretch")
    with list_tab2:
        st.dataframe(df_schedules.tail(30).iloc[::-1], width="stretch")

# ==================== 模組 1: 匯出每月綜合報表 (排班+行程+影印) ====================
elif menu == "📤 匯出每月綜合報表":
    st.header("📤 每月工作行程、排班及影印報表輸出")
    st.info("💡 選擇年份與月份，可檢視當月排班、工作行事及影印總計，並支援下載整月份完整 Excel 活頁簿。")

    col_y, col_m = st.columns(2)
    with col_y:
        selected_year = st.selectbox("選擇年份", [2025, 2026, 2027], index=1)
    with col_m:
        selected_month = st.selectbox("選擇月份", list(range(1, 13)), index=datetime.today().month - 1)

    _, num_days = py_calendar.monthrange(selected_year, selected_month)
    month_str = f"{selected_year}-{selected_month:02d}"

    # 1. 班表整理
    df_schedules = load_data("schedules", ["employee_name", "leave_type", "start_date", "end_date"])
    roster_rows = []
    weekdays_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    for day in range(1, num_days + 1):
        cur_d_str = f"{selected_year}-{selected_month:02d}-{day:02d}"
        cur_dt = datetime.strptime(cur_d_str, "%Y-%m-%d")
        w_day = weekdays_zh[cur_dt.weekday()]
        daily_shifts = get_daily_roster(cur_d_str)

        row = {"日期": cur_d_str, "星期": w_day}
        for emp in EMPLOYEES:
            shift_info = daily_shifts.get(emp, "未排班")
            if not df_schedules.empty:
                emp_leaves = df_schedules[
                    (df_schedules["employee_name"].astype(str) == emp) &
                    (df_schedules["start_date"].astype(str) <= cur_d_str) &
                    (df_schedules["end_date"].astype(str) >= cur_d_str)
                ]
                if not emp_leaves.empty:
                    lv_type = emp_leaves.iloc[0]["leave_type"]
                    row[emp] = f"{shift_info} [休:{lv_type}]"
                else:
                    row[emp] = shift_info
            else:
                row[emp] = shift_info
        roster_rows.append(row)

    df_month_roster = pd.DataFrame(roster_rows)

    # 2. 行事整理
    df_works = load_data("work_events", ["start_date", "end_date", "start_time", "end_time", "person_in_charge", "title", "description"])
    if not df_works.empty:
        df_month_events = df_works[
            (df_works["start_date"].astype(str) <= f"{month_str}-{num_days:02d}") &
            (df_works["end_date"].astype(str) >= f"{month_str}-01")
        ]
    else:
        df_month_events = pd.DataFrame()

    # 3. 影印紀錄整理與統計
    df_p_all = load_data("print_logs", ["log_date", "user_name", "pages", "print_type", "purpose"])
    if not df_p_all.empty:
        df_p_all["pages_num"] = pd.to_numeric(df_p_all["pages"], errors="coerce").fillna(0).astype(int)
        df_month_prints = df_p_all[
            (df_p_all["log_date"].astype(str) >= f"{month_str}-01") &
            (df_p_all["log_date"].astype(str) <= f"{month_str}-{num_days:02d}")
        ].copy()
    else:
        df_month_prints = pd.DataFrame()

    tab_exp1, tab_exp2, tab_exp3 = st.tabs(["📅 當月班表總覽", "💼 工作行程一覽", "🖨️ 影印統計與明細"])
    with tab_exp1:
        st.dataframe(df_month_roster, width="stretch")
    with tab_exp2:
        st.dataframe(df_month_events, width="stretch")
    with tab_exp3:
        if not df_month_prints.empty:
            summary_p = df_month_prints.groupby(["user_name", "print_type"])["pages_num"].sum().unstack(fill_value=0)
            for c in ["黑白", "彩色"]:
                if c not in summary_p.columns:
                    summary_p[c] = 0
            summary_p["個人總張數"] = summary_p["黑白"] + summary_p["彩色"]
            summary_p = summary_p.reset_index().rename(columns={"user_name": "登記人"})
            st.markdown("##### 📊 同仁影印用量彙總表")
            st.dataframe(summary_p, width="stretch")

            st.markdown("##### 📝 影印詳細紀錄")
            st.dataframe(df_month_prints[["log_date", "user_name", "pages", "print_type", "purpose"]].rename(
                columns={"log_date": "影印日期", "user_name": "登記人", "pages": "張數", "print_type": "色彩規格", "purpose": "用途"}
            ), width="stretch")
        else:
            st.info("該月份目前尚無影印輸出紀錄。")

    # Excel 綜合輸出
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df_month_roster.to_excel(writer, sheet_name="當月班表總覽", index=False)
        df_month_events.to_excel(writer, sheet_name="當月工作行程", index=False)
        if not df_month_prints.empty:
            summary_p.to_excel(writer, sheet_name="影印統計彙總", index=False)
            df_month_prints[["log_date", "user_name", "pages", "print_type", "purpose"]].to_excel(writer, sheet_name="影印明細流水帳", index=False)

    col_ebtn1, col_ebtn2 = st.columns(2)
    with col_ebtn1:
        st.download_button(
            label=f"📥 下載 {selected_year}年{selected_month}月 完整綜合 Excel 報表",
            data=excel_buffer.getvalue(),
            file_name=f"{selected_year}年{selected_month}月_公司綜合行政報表.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with col_ebtn2:
        st.download_button(
            label=f"📥 下載 {selected_year}年{selected_month}月 班表 CSV",
            data=df_month_roster.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{selected_year}年{selected_month}月_班表.csv",
            mime="text/csv",
        )

# ==================== 模組 2: 班表、排休與調班 ====================
elif menu == "📅 班表、排休與調班":
    st.header("📅 班表排班、排休與調班管理")
    tab_leave, tab_edit_leave, tab_swap, tab_schedule_view = st.tabs(
        ["📝 請假/排休登記", "✏️ 修改排休紀錄", "🔄 臨時調班申請", "👀 當日班表與休假查詢"]
    )

    with tab_leave:
        st.subheader("新增排休 (每日限制請假最多 1 人)")
        col_l1, col_l2 = st.columns(2)
        with col_l1:
            emp = st.selectbox("請假人", EMPLOYEES, key="leave_emp")
            l_type = st.selectbox("假別", ["特休", "補休", "公假", "公出", "事假", "病假"], key="leave_type")
            s_date = st.date_input("開始休假日期", min_value=date.today())
            s_time = st.time_input("開始休假時間", value=time(8, 0))
        with col_l2:
            e_date = st.date_input("結束休假日期", min_value=s_date)
            e_time = st.time_input("結束休假時間", value=time(17, 0))
            l_note = st.text_input("備註原因")

        if st.button("送出排休登記"):
            start_dt_str = f"{s_date} {s_time.strftime('%H:%M')}"
            end_dt_str = f"{e_date} {e_time.strftime('%H:%M')}"

            if datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M") >= datetime.strptime(end_dt_str, "%Y-%m-%d %H:%M"):
                st.error("結束時間必須晚於開始時間！")
            else:
                df_schedules = load_data("schedules", ["id", "employee_name", "leave_type", "start_datetime", "end_datetime", "start_date", "end_date", "note"])
                conflict = False
                if not df_schedules.empty:
                    c_rows = df_schedules[
                        (df_schedules["employee_name"].astype(str) != emp) &
                        ~((df_schedules["end_date"].astype(str) < str(s_date)) | (df_schedules["start_date"].astype(str) > str(e_date)))
                    ]
                    if not c_rows.empty:
                        conflict = True
                        conflict_info = "、".join(c_rows["employee_name"].tolist())
                        st.error(f"⚠️ 無法登記！當日已有同仁排休：{conflict_info}。依規定每日僅限 1 人排休。")

                if not conflict:
                    valid_ids = pd.to_numeric(df_schedules["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    new_row = pd.DataFrame([{
                        "id": str(new_id), "employee_name": emp, "leave_type": l_type,
                        "start_datetime": start_dt_str, "end_datetime": end_dt_str,
                        "start_date": str(s_date), "end_date": str(e_date), "note": str(l_note)
                    }])
                    df_schedules = pd.concat([df_schedules, new_row], ignore_index=True)
                    save_data("schedules", df_schedules)
                    st.success("排休登記成功！已安全儲存。")
                    st.rerun()

    with tab_edit_leave:
        st.subheader("✏️ 修改或更新排休紀錄")
        df_schedules = load_data("schedules", ["id", "employee_name", "leave_type", "start_date", "end_date", "note"])
        if not df_schedules.empty and len(df_schedules) > 0:
            record_options = {
                f"編號 {r['id']} | {r['employee_name']} - {r['leave_type']} ({r['start_date']} ~ {r['end_date']})": str(r['id'])
                for _, r in df_schedules.iterrows() if str(r['employee_name']).strip()
            }
            if record_options:
                selected_label = st.selectbox("請選擇欲修改的排休紀錄", list(record_options.keys()))
                target_id = record_options[selected_label]
                curr = df_schedules[df_schedules["id"].astype(str) == target_id].iloc[0]

                c_e1, c_e2 = st.columns(2)
                with c_e1:
                    edit_emp = st.selectbox("請假同仁", EMPLOYEES, index=EMPLOYEES.index(curr["employee_name"]) if curr["employee_name"] in EMPLOYEES else 0, key="ed_l_emp")
                    edit_type = st.selectbox("假別", ["特休", "補休", "公假", "公出", "事假", "病假"], index=["特休", "補休", "公假", "公出", "事假", "病假"].index(curr["leave_type"]) if curr["leave_type"] in ["特休", "補休", "公假", "公出", "事假", "病假"] else 0, key="ed_l_type")
                    try:
                        cur_sd = datetime.strptime(str(curr["start_date"]).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_sd = date.today()
                    edit_sd = st.date_input("開始休假日期", value=cur_sd, key="ed_l_sd")
                with c_e2:
                    try:
                        cur_ed = datetime.strptime(str(curr["end_date"]).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_ed = edit_sd
                    edit_ed = st.date_input("結束休假日期", value=cur_ed, min_value=edit_sd, key="ed_l_ed")
                    edit_note = st.text_input("原因備註", value=str(curr["note"]), key="ed_l_note")

                if st.button("確認儲存修改", key="btn_save_edit_leave"):
                    s_dt = f"{edit_sd} 08:00"
                    e_dt = f"{edit_ed} 17:00"
                    df_schedules.loc[df_schedules["id"].astype(str) == target_id, ["employee_name", "leave_type", "start_date", "end_date", "start_datetime", "end_datetime", "note"]] = [
                        edit_emp, edit_type, str(edit_sd), str(edit_ed), s_dt, e_dt, str(edit_note)
                    ]
                    save_data("schedules", df_schedules)
                    st.success("排休資料已成功修改！")
                    st.rerun()
            else:
                st.info("目前尚無有效的排休紀錄可供修改。")
        else:
            st.info("目前尚無任何排休紀錄可供修改。")

    with tab_swap:
        st.subheader("臨時調班登記")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            swap_d = st.date_input("調班日期", key="swap_date")
            swap_emp = st.selectbox("調班同仁", EMPLOYEES, key="swap_emp")
        with col_s2:
            new_shift = st.selectbox("變更為班別", ["A班 (08:00-16:30)", "B班 (08:30-17:00)", "休假/不排班"])
            swap_reason = st.text_input("調班事由")

        if st.button("確認調班"):
            df_swaps = load_data("shift_swaps", ["swap_date", "employee_name", "assigned_shift", "reason"])
            if not df_swaps.empty:
                df_swaps = df_swaps[~((df_swaps["swap_date"].astype(str) == str(swap_d)) & (df_swaps["employee_name"].astype(str) == str(swap_emp)))]
            new_swap = pd.DataFrame([{"swap_date": str(swap_d), "employee_name": str(swap_emp), "assigned_shift": str(new_shift.split(" ")[0]), "reason": str(swap_reason)}])
            df_swaps = pd.concat([df_swaps, new_swap], ignore_index=True)
            save_data("shift_swaps", df_swaps)
            st.success(f"{swap_d} {swap_emp} 已成功調整為 {new_shift.split(' ')[0]}！")

    with tab_schedule_view:
        st.subheader("查詢當日實際班表")
        check_date = st.date_input("選擇欲查詢日期", value=date.today())
        daily_shifts = get_daily_roster(str(check_date))

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🅰️ A班 (08:00 - 16:30)")
            a_members = [k for k, v in daily_shifts.items() if "A班" in v]
            st.write("、".join(a_members) if a_members else "無")
        with col_b:
            st.markdown("#### 🅱️ B班 (08:30 - 17:00)")
            b_members = [k for k, v in daily_shifts.items() if "B班" in v]
            st.write("、".join(b_members) if b_members else "無")

# ==================== 模組 3: 工作行事登記 ====================
elif menu == "📌 工作行事登記":
    st.header("📌 工作行事管理")
    tab_act_add, tab_act_edit = st.tabs(["➕ 新增工作行事", "✏️ 修改既有行事"])

    with tab_act_add:
        col_e1, col_e2 = st.columns([1, 2])
        with col_e1:
            event_s_date = st.date_input("開始日期", value=date.today(), key="we_s_date")
            event_e_date = st.date_input("結束日期", min_value=event_s_date, value=event_s_date, key="we_e_date")
            t_col1, t_col2 = st.columns(2)
            with t_col1:
                event_s_time = st.time_input("開始時間", value=time(9, 0), key="we_s_time")
            with t_col2:
                event_e_time = st.time_input("結束時間", value=time(10, 0), key="we_e_time")

            event_title = st.text_input("事項/活動標題", key="we_title")
            event_pic = st.selectbox("負責人", EVENT_RESPONSIBLES, key="we_pic")
            event_desc = st.text_area("內容說明 (選填)", key="we_desc")

            if st.button("新增工作行事"):
                if not event_title.strip():
                    st.warning("請填寫活動/事項標題。")
                elif event_s_date == event_e_date and event_s_time >= event_e_time:
                    st.error("同一天活動的結束時間必須晚於開始時間！")
                else:
                    df_w = load_data("work_events", ["id", "start_date", "end_date", "start_time", "end_time", "title", "person_in_charge", "description"])
                    valid_ids = pd.to_numeric(df_w["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    new_row = pd.DataFrame([{
                        "id": str(new_id), "start_date": str(event_s_date), "end_date": str(event_e_date),
                        "start_time": event_s_time.strftime("%H:%M"), "end_time": event_e_time.strftime("%H:%M"),
                        "title": str(event_title), "person_in_charge": str(event_pic), "description": str(event_desc)
                    }])
                    df_w = pd.concat([df_w, new_row], ignore_index=True)
                    save_data("work_events", df_w)
                    st.success("工作行事已成功登記！")
                    st.rerun()

        with col_e2:
            df_w = load_data("work_events", ["id", "start_date", "end_date", "start_time", "end_time", "person_in_charge", "title", "description"])
            st.dataframe(df_w.tail(30).iloc[::-1], width="stretch")

    with tab_act_edit:
        df_w = load_data("work_events", ["id", "title", "start_date", "end_date", "start_time", "end_time", "person_in_charge", "description"])
        if not df_w.empty and len(df_w) > 0:
            w_options = {
                f"編號 {r['id']} | [{r['person_in_charge']}] {r['title']} ({r['start_date']})": str(r['id'])
                for _, r in df_w.iterrows() if str(r['title']).strip()
            }
            if w_options:
                w_choice = st.selectbox("選擇欲修改的事項", list(w_options.keys()))
                target_w_id = w_options[w_choice]
                w_curr = df_w[df_w["id"].astype(str) == target_w_id].iloc[0]

                ew_col1, ew_col2 = st.columns(2)
                with ew_col1:
                    new_w_title = st.text_input("活動/事項標題", value=str(w_curr["title"]), key="ew_title")
                    new_w_pic = st.selectbox("負責人", EVENT_RESPONSIBLES, index=EVENT_RESPONSIBLES.index(w_curr["person_in_charge"]) if w_curr["person_in_charge"] in EVENT_RESPONSIBLES else 0, key="ew_pic")
                    try:
                        cur_wsd = datetime.strptime(str(w_curr["start_date"]).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_wsd = date.today()
                    new_w_sd = st.date_input("開始日期", value=cur_wsd, key="ew_sd")
                    try:
                        cur_wst = datetime.strptime(str(w_curr["start_time"]).strip(), "%H:%M").time()
                    except Exception:
                        cur_wst = time(9, 0)
                    new_w_st = st.time_input("開始時間", value=cur_wst, key="ew_st")
                with ew_col2:
                    try:
                        cur_wed = datetime.strptime(str(w_curr["end_date"]).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_wed = new_w_sd
                    new_w_ed = st.date_input("結束日期", value=cur_wed, min_value=new_w_sd, key="ew_ed")
                    try:
                        cur_wet = datetime.strptime(str(w_curr["end_time"]).strip(), "%H:%M").time()
                    except Exception:
                        cur_wet = time(10, 0)
                    new_w_et = st.time_input("結束時間", value=cur_wet, key="ew_et")
                    new_w_desc = st.text_area("內容說明", value=str(w_curr["description"]), key="ew_desc")

                if st.button("儲存行事修改", key="btn_save_edit_work"):
                    df_w.loc[df_w["id"].astype(str) == target_w_id, ["title", "person_in_charge", "start_date", "end_date", "start_time", "end_time", "description"]] = [
                        str(new_w_title), str(new_w_pic), str(new_w_sd), str(new_w_ed), new_w_st.strftime("%H:%M"), new_w_et.strftime("%H:%M"), str(new_w_desc)
                    ]
                    save_data("work_events", df_w)
                    st.success("工作行事已成功更新！")
                    st.rerun()
            else:
                st.info("目前尚無有效的工作行事可供修改。")
        else:
            st.info("目前尚無工作行事可供修改。")

# ==================== 模組 4: 3C產品借用申請 ====================
elif menu == "📱 3C產品借用申請":
    st.header("📱 3C產品借用申請與狀況登記")
    tab_dev_add, tab_dev_edit = st.tabs(["➕ 登記借用", "✏️ 修改借用紀錄"])

    with tab_dev_add:
        col_b1, col_b2 = st.columns([1, 2])
        with col_b1:
            borrow_item = st.selectbox("借用申請物品", DEVICES, key="borrow_item")
            borrow_applicant = st.selectbox("申請人", EMPLOYEES, key="borrow_app")
            borrow_date = st.date_input("借用日期", value=date.today(), key="borrow_d")
            col_bt1, col_bt2 = st.columns(2)
            with col_bt1:
                borrow_s_time = st.time_input("借用開始時間", value=time(9, 0), key="borrow_st")
            with col_bt2:
                borrow_e_time = st.time_input("預計結束時間", value=time(17, 0), key="borrow_et")

            borrow_condition = st.radio("借用狀況檢查", ["良好", "故障"], horizontal=True, key="borrow_cond")
            fault_description = st.text_area("⚠️ 故障說明", key="dev_f_desc") if borrow_condition == "故障" else ""

            if st.button("送出借用申請"):
                if borrow_s_time >= borrow_e_time:
                    st.error("結束時間必須晚於開始時間！")
                elif borrow_condition == "故障" and not fault_description.strip():
                    st.warning("狀況為故障時，請務必填寫故障說明！")
                else:
                    df_b = load_data("device_borrows", ["id", "device_name", "applicant", "borrow_date", "start_time", "end_time", "condition", "fault_desc", "created_at"])
                    valid_ids = pd.to_numeric(df_b["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    new_row = pd.DataFrame([{
                        "id": str(new_id), "device_name": borrow_item, "applicant": borrow_applicant,
                        "borrow_date": str(borrow_date), "start_time": borrow_s_time.strftime("%H:%M"),
                        "end_time": borrow_e_time.strftime("%H:%M"), "condition": borrow_condition,
                        "fault_desc": str(fault_description), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }])
                    df_b = pd.concat([df_b, new_row], ignore_index=True)
                    save_data("device_borrows", df_b)
                    st.success(f"{borrow_applicant} 借用 {borrow_item} 登記成功！")
                    st.rerun()

        with col_b2:
            df_b = load_data("device_borrows", ["id", "borrow_date", "applicant", "device_name", "start_time", "end_time", "condition", "fault_desc"])
            st.dataframe(df_b.tail(30).iloc[::-1], width="stretch")

    with tab_dev_edit:
        df_b = load_data("device_borrows", ["id", "device_name", "applicant", "borrow_date", "start_time", "end_time", "condition", "fault_desc"])
        if not df_b.empty and len(df_b) > 0:
            b_opts = {
                f"編號 {r['id']} | {r['applicant']} 借用 {r['device_name']} ({r['borrow_date']})": str(r['id'])
                for _, r in df_b.iterrows() if str(r['applicant']).strip()
            }
            if b_opts:
                b_choice = st.selectbox("請選擇欲修改的借用紀錄", list(b_opts.keys()))
                target_b_id = b_opts[b_choice]
                b_curr = df_b[df_b["id"].astype(str) == target_b_id].iloc[0]

                eb_col1, eb_col2 = st.columns(2)
                with eb_col1:
                    ed_item = st.selectbox("借用物品", DEVICES, index=DEVICES.index(b_curr["device_name"]) if b_curr["device_name"] in DEVICES else 0, key="ed_b_item")
                    ed_app = st.selectbox("申請同仁", EMPLOYEES, index=EMPLOYEES.index(b_curr["applicant"]) if b_curr["applicant"] in EMPLOYEES else 0, key="ed_b_app")
                    try:
                        cur_bd = datetime.strptime(str(b_curr["borrow_date"]).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_bd = date.today()
                    ed_bd = st.date_input("借用日期", value=cur_bd, key="ed_b_date")
                with eb_col2:
                    try:
                        cur_st = datetime.strptime(str(b_curr["start_time"]).strip(), "%H:%M").time()
                    except Exception:
                        cur_st = time(9, 0)
                    ed_st = st.time_input("開始時間", value=cur_st, key="ed_b_st")
                    try:
                        cur_et = datetime.strptime(str(b_curr["end_time"]).strip(), "%H:%M").time()
                    except Exception:
                        cur_et = time(17, 0)
                    ed_et = st.time_input("結束時間", value=cur_et, key="ed_b_et")
                    ed_cond = st.radio("設備狀態", ["良好", "故障"], index=0 if b_curr["condition"] == "良好" else 1, horizontal=True, key="ed_b_cond")
                    ed_fault = st.text_area("故障說明", value=str(b_curr["fault_desc"]), key="ed_b_fault")

                if st.button("儲存借用修改", key="btn_save_edit_device"):
                    df_b.loc[df_b["id"].astype(str) == target_b_id, ["device_name", "applicant", "borrow_date", "start_time", "end_time", "condition", "fault_desc"]] = [
                        ed_item, ed_app, str(ed_bd), ed_st.strftime("%H:%M"), ed_et.strftime("%H:%M"), ed_cond, str(ed_fault)
                    ]
                    save_data("device_borrows", df_b)
                    st.success("3C 借用紀錄已成功更新！")
                    st.rerun()
            else:
                st.info("目前尚無有效的 3C 借用紀錄可供修改。")
        else:
            st.info("目前尚無 3C 借用紀錄可供修改。")

# ==================== 模組 5: 影印輸出登記 (新增每月統計與匯出) ====================
elif menu == "🖨️ 影印輸出登記":
    st.header("🖨️ 影印輸出登記與每月報表")
    tab_p_reg, tab_p_month = st.tabs(["📝 影印登記", "📊 每月影印統計與輸出"])

    with tab_p_reg:
        with st.form("print_form"):
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                p_user = st.selectbox("登記人姓名", EMPLOYEES)
                p_date = st.date_input("影印日期", value=date.today())
                p_pages = st.number_input("輸出張數", min_value=1, value=1, step=1)
            with col_p2:
                p_type = st.selectbox("色彩規格", ["黑白", "彩色"])
                p_purpose = st.text_input("輸出用途 (例如：會議簡報、評鑑資料)")
            submit_print = st.form_submit_button("登記輸出")

            if submit_print:
                df_p = load_data("print_logs", ["log_date", "user_name", "pages", "print_type", "purpose"])
                new_p = pd.DataFrame([{"log_date": str(p_date), "user_name": str(p_user), "pages": str(p_pages), "print_type": str(p_type), "purpose": str(p_purpose)}])
                df_p = pd.concat([df_p, new_p], ignore_index=True)
                save_data("print_logs", df_p)
                st.success("影印紀錄已送出！切換頁面資料將安全留存。")
                st.rerun()

        st.divider()
        st.subheader("📋 最新影印登記明細 (最近 20 筆)")
        df_p = load_data("print_logs", ["log_date", "user_name", "pages", "print_type", "purpose"])
        st.dataframe(df_p.tail(20).iloc[::-1], width="stretch")

    with tab_p_month:
        st.subheader("📊 依月份查詢與匯出影印報表")
        col_py, col_pm = st.columns(2)
        with col_py:
            p_year = st.selectbox("選擇年份", [2025, 2026, 2027], index=1, key="py_sel")
        with col_pm:
            p_month = st.selectbox("選擇月份", list(range(1, 13)), index=datetime.today().month - 1, key="pm_sel")

        _, p_num_days = py_calendar.monthrange(p_year, p_month)
        p_month_str = f"{p_year}-{p_month:02d}"

        df_all_prints = load_data("print_logs", ["log_date", "user_name", "pages", "print_type", "purpose"])
        if not df_all_prints.empty:
            df_all_prints["pages_num"] = pd.to_numeric(df_all_prints["pages"], errors="coerce").fillna(0).astype(int)
            cur_month_df = df_all_prints[
                (df_all_prints["log_date"].astype(str) >= f"{p_month_str}-01") &
                (df_all_prints["log_date"].astype(str) <= f"{p_month_str}-{p_num_days:02d}")
            ].copy()
        else:
            cur_month_df = pd.DataFrame()

        if not cur_month_df.empty:
            # 統計總張數與黑白彩色分佈
            summary = cur_month_df.groupby(["user_name", "print_type"])["pages_num"].sum().unstack(fill_value=0)
            for c in ["黑白", "彩色"]:
                if c not in summary.columns:
                    summary[c] = 0
            summary["個人合計"] = summary["黑白"] + summary["彩色"]
            summary = summary.reset_index().rename(columns={"user_name": "同仁姓名"})

            total_bw = summary["黑白"].sum()
            total_color = summary["彩色"].sum()
            total_all = summary["個人合計"].sum()

            m1, m2, m3 = st.columns(3)
            m1.metric("當月全體黑白總量", f"{total_bw} 張")
            m2.metric("當月全體彩色總量", f"{total_color} 張")
            m3.metric("當月總輸出張數", f"{total_all} 張")

            st.markdown("#### 👤 同仁個別印量總計")
            st.dataframe(summary, width="stretch")

            st.markdown("#### 📄 當月影印明細流水帳")
            disp_detail = cur_month_df[["log_date", "user_name", "pages", "print_type", "purpose"]].rename(
                columns={"log_date": "影印日期", "user_name": "登記人", "pages": "張數", "print_type": "色彩規格", "purpose": "用途"}
            )
            st.dataframe(disp_detail, width="stretch")

            # Excel 匯出 (統計 + 明細 雙分頁)
            p_excel_buf = io.BytesIO()
            with pd.ExcelWriter(p_excel_buf, engine="openpyxl") as p_writer:
                summary.to_excel(p_writer, sheet_name="同仁影印統計彙總", index=False)
                disp_detail.to_excel(p_writer, sheet_name="當月影印明細", index=False)

            col_pd1, col_pd2 = st.columns(2)
            with col_pd1:
                st.download_button(
                    label=f"📥 下載 {p_year}年{p_month}月 影印報表 (Excel 檔)",
                    data=p_excel_buf.getvalue(),
                    file_name=f"{p_year}年{p_month}月_公司影印使用報表.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col_pd2:
                st.download_button(
                    label=f"📥 下載 {p_year}年{p_month}月 影印統計 (CSV 檔)",
                    data=summary.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{p_year}年{p_month}月_影印統計.csv",
                    mime="text/csv",
                )
        else:
            st.info(f"{p_year} 年 {p_month} 月 目前尚無任何影印登記紀錄。")

# ==================== 模組 6: 物資出入庫管理 ====================
elif menu == "📦 物資出入庫管理":
    st.header("📦 物資申請出庫與採購入庫")
    df_supplies = load_data("supplies", ["item_name", "stock"])
    items = [x for x in df_supplies["item_name"].astype(str).tolist() if x.strip()]

    tab_out, tab_in = st.tabs(["📤 領用出庫", "📥 採購入庫"])

    with tab_out:
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            applicant = st.selectbox("領用人", EMPLOYEES, key="mat_out_user")
            out_item = st.selectbox("物資品項", items if items else DEFAULT_SUPPLIES, key="mat_out_item")
        with col_o2:
            out_qty = st.number_input("領用數量", min_value=1, value=1, step=1, key="mat_out_q")
            out_d = st.date_input("領用日期", value=date.today())

        if st.button("確認出庫", key="btn_mat_out"):
            cur_rows = df_supplies[df_supplies["item_name"].astype(str) == out_item]
            cur_stock = int(cur_rows["stock"].values[0]) if not cur_rows.empty and str(cur_rows["stock"].values[0]).isdigit() else 0
            if cur_stock >= out_qty:
                df_supplies.loc[df_supplies["item_name"].astype(str) == out_item, "stock"] = str(cur_stock - out_qty)
                save_data("supplies", df_supplies)

                df_logs = load_data("inventory_logs", ["log_date", "log_type", "handler", "item_name", "quantity"])
                new_log = pd.DataFrame([{"log_date": str(out_d), "log_type": "領用出庫", "handler": str(applicant), "item_name": str(out_item), "quantity": str(out_qty)}])
                df_logs = pd.concat([df_logs, new_log], ignore_index=True)
                save_data("inventory_logs", df_logs)

                st.success(f"出庫成功！{out_item} 剩餘庫存：{cur_stock - out_qty}")
                st.rerun()
            else:
                st.error(f"庫存不足！{out_item} 目前僅剩 {cur_stock}")

    with tab_in:
        col_i1, col_i2 = st.columns(2)
        with col_i1:
            buyer = st.selectbox("入庫人", EMPLOYEES, key="mat_in_user")
            in_item = st.selectbox("入庫品項", items if items else DEFAULT_SUPPLIES, key="mat_in_item")
        with col_i2:
            in_qty = st.number_input("採購進貨數量", min_value=1, value=1, step=1, key="mat_in_q")
            in_d = st.date_input("入庫日期", value=date.today())

        if st.button("確認入庫", key="btn_mat_in"):
            cur_rows = df_supplies[df_supplies["item_name"].astype(str) == in_item]
            cur_stock = int(cur_rows["stock"].values[0]) if not cur_rows.empty and str(cur_rows["stock"].values[0]).isdigit() else 0
            df_supplies.loc[df_supplies["item_name"].astype(str) == in_item, "stock"] = str(cur_stock + in_qty)
            save_data("supplies", df_supplies)

            df_logs = load_data("inventory_logs", ["log_date", "log_type", "handler", "item_name", "quantity"])
            new_log = pd.DataFrame([{"log_date": str(in_d), "log_type": "採購入庫", "handler": str(buyer), "item_name": str(in_item), "quantity": str(in_qty)}])
            df_logs = pd.concat([df_logs, new_log], ignore_index=True)
            save_data("inventory_logs", df_logs)

            st.success(f"入庫成功！{in_item} 增加 {in_qty}")
            st.rerun()

    st.divider()
    c_st1, c_st2 = st.columns(2)
    with c_st1:
        st.subheader("現有庫存總覽")
        st.dataframe(df_supplies, width="stretch")
    with c_st2:
        st.subheader("最近出入庫歷程")
        df_logs = load_data("inventory_logs", ["log_date", "log_type", "handler", "item_name", "quantity"])
        st.dataframe(df_logs.tail(10).iloc[::-1], width="stretch")
