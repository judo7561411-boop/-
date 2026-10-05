import base64
import calendar as py_calendar
from datetime import date, datetime, time, timezone, timedelta
import io
import json
import sqlite3
import urllib.parse
import re
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

# 具備防呆保護的 Google 日曆 API 套件匯入
try:
    from googleapiclient.discovery import build
    HAS_CALENDAR_LIB = True
except ModuleNotFoundError:
    build = None
    HAS_CALENDAR_LIB = False

# 系統預設固定名單與參數
DEFAULT_EMPLOYEES = ["伊臻", "美釵", "涵玟", "勝順"]
DEVICES = ["平板-1", "平板-2", "平板-3", "投影機"]
DEFAULT_SUPPLIES = [
    "酒精", "漂白水", "衛生紙", "擦手紙", "洗手乳",
    "垃圾袋(小)", "垃圾袋(中)", "垃圾袋(大)", "廁所清潔劑", "洗碗精"
]

LOCAL_DB = "backup_storage.db"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar"
]

# 台灣標準時區 (UTC+8)
TAIWAN_TZ = timezone(timedelta(hours=8))

# 資料表欄位標準定義
EMPLOYEE_COLS = ["id", "name", "created_at"]
WORK_EVENT_COLS = ["id", "start_date", "end_date", "start_time", "end_time", "title", "person_in_charge", "description", "google_event_id"]
SCHEDULE_COLS = ["id", "employee_name", "leave_type", "start_datetime", "end_datetime", "start_date", "end_date", "note", "google_event_id"]
SWAP_COLS = ["id", "swap_date", "employee_name", "assigned_shift", "reason", "google_event_id"]

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
            df = pd.read_json(io.StringIO(row[0])).fillna("").astype(str)
            for col in default_cols:
                if col not in df.columns:
                    df[col] = ""
            return df
    except Exception:
        pass
    return pd.DataFrame(columns=default_cols)

def get_service_account_dict():
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
    return service_account_info

def get_credentials():
    info = get_service_account_dict()
    if not info:
        return None
    return Credentials.from_service_account_info(info, scopes=SCOPES)

@st.cache_resource
def get_gspread_client():
    try:
        creds = get_credentials()
        if not creds or "spreadsheet_url" not in st.secrets:
            return None
        client = gspread.authorize(creds)
        sheet = client.open_by_url(str(st.secrets["spreadsheet_url"]).strip().strip('"').strip("'"))
        return sheet
    except Exception:
        return None

def parse_and_normalize_time(t_input, default="09:00"):
    """相容並解析各種時間輸入格式"""
    if t_input is None:
        return default
    t_str = str(t_input).strip()
    if not t_str or t_str.lower() in ["nan", "none", ""]:
        return default

    match = re.search(r"(\d{1,2})[:：](\d{1,2})", t_str)
    if match:
        h = int(match.group(1))
        m = int(match.group(2))
        if ("下午" in t_str or "pm" in t_str.lower()) and h < 12:
            h += 12
        elif ("上午" in t_str or "am" in t_str.lower()) and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"

    match_zh = re.search(r"(\d{1,2})\s*點\s*(\d{1,2})?", t_str)
    if match_zh:
        h = int(match_zh.group(1))
        m = int(match_zh.group(2)) if match_zh.group(2) else 0
        if ("下午" in t_str or "pm" in t_str.lower()) and h < 12:
            h += 12
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"

    return default

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

# 動態人員載入
def get_current_employees():
    df_emp = load_data("employees", EMPLOYEE_COLS)
    if df_emp.empty or len(df_emp) == 0:
        now_time = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        init_rows = [{"id": str(i + 1), "name": name, "created_at": now_time} for i, name in enumerate(DEFAULT_EMPLOYEES)]
        df_emp = pd.DataFrame(init_rows)
        save_data("employees", df_emp)
        return DEFAULT_EMPLOYEES
    
    names = [str(x).strip() for x in df_emp["name"].tolist() if str(x).strip()]
    return names if names else DEFAULT_EMPLOYEES

CURRENT_EMPLOYEES = get_current_employees()
EVENT_RESPONSIBLES = ["全體"] + CURRENT_EMPLOYEES

# Google 日曆同步核心函式 (新增 / 修改)
def sync_event_to_google_calendar(summary, description, s_date, e_date, s_time, e_time, existing_cal_id=""):
    if not HAS_CALENDAR_LIB:
        return False, "", "缺少日曆套件"

    try:
        calendar_id = st.secrets.get("calendar_id", "").strip().strip('"').strip("'")
        if not calendar_id:
            return False, "", "未設定 calendar_id"

        creds = get_credentials()
        if not creds:
            return False, "", "無法取得憑證"

        service = build("calendar", "v3", credentials=creds)

        s_time_norm = parse_and_normalize_time(s_time, "09:00")
        e_time_norm = parse_and_normalize_time(e_time, "10:00")

        start_rfc = f"{s_date}T{s_time_norm}:00+08:00"
        end_rfc = f"{e_date}T{e_time_norm}:00+08:00"

        event_body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_rfc, "timeZone": "Asia/Taipei"},
            "end": {"dateTime": end_rfc, "timeZone": "Asia/Taipei"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 15},
                    {"method": "popup", "minutes": 720}
                ],
            },
        }

        cal_event_id = str(existing_cal_id).strip()

        if cal_event_id:
            try:
                updated_event = service.events().update(
                    calendarId=calendar_id,
                    eventId=cal_event_id,
                    body=event_body
                ).execute()
                return True, updated_event.get("id", cal_event_id), "日曆活動已更新"
            except Exception:
                pass

        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return True, created_event.get("id", ""), "日曆活動已建立"
    except Exception as e:
        return False, "", f"日曆同步失敗：{e}"

# Google 日曆連動刪除函式
def delete_google_calendar_event(cal_event_id):
    if not HAS_CALENDAR_LIB or not str(cal_event_id).strip():
        return False, "無日曆事件 ID 或缺少套件"

    try:
        calendar_id = st.secrets.get("calendar_id", "").strip().strip('"').strip("'")
        if not calendar_id:
            return False, "未設定 calendar_id"

        creds = get_credentials()
        if not creds:
            return False, "無法取得憑證"

        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId=calendar_id, eventId=str(cal_event_id).strip()).execute()
        return True, "手機 Google 日曆行程已同步移除！"
    except Exception as e:
        if "404" in str(e) or "410" in str(e):
            return True, "日曆活動原已不存在，已同步清除。"
        return False, f"日曆刪除失敗：{e}"

def batch_sync_all_to_google_calendar():
    calendar_id = st.secrets.get("calendar_id", "").strip().strip('"').strip("'")
    if not calendar_id:
        return 0, 0, "未在 Secrets 中設定 calendar_id"

    success_cnt = 0
    fail_cnt = 0

    df_w = load_data("work_events", WORK_EVENT_COLS)
    for idx, r in df_w.iterrows():
        title = str(r.get("title", "")).strip()
        if not title:
            continue
        s_d = str(r.get("start_date", "")).strip()
        e_d = str(r.get("end_date", "")).strip() if str(r.get("end_date", "")).strip() else s_d
        s_t = parse_and_normalize_time(r.get("start_time", "09:00"))
        e_t = parse_and_normalize_time(r.get("end_time", "10:00"))
        desc = f"負責人：{r.get('person_in_charge', '全體')}\n說明：{r.get('description', '')}"
        ok, new_id, _ = sync_event_to_google_calendar(f"💼 [{r.get('person_in_charge', '全體')}] {title}", desc, s_d, e_d, s_t, e_t, r.get("google_event_id", ""))
        if ok:
            df_w.at[idx, "google_event_id"] = new_id
            success_cnt += 1
        else:
            fail_cnt += 1
    save_data("work_events", df_w)

    df_sch = load_data("schedules", SCHEDULE_COLS)
    for idx, r in df_sch.iterrows():
        emp = str(r.get("employee_name", "")).strip()
        if not emp:
            continue
        s_d = str(r.get("start_date", "")).strip()
        e_d = str(r.get("end_date", "")).strip() if str(r.get("end_date", "")).strip() else s_d
        desc = f"請假同仁：{emp}\n假別：{r.get('leave_type', '')}\n原因備註：{r.get('note', '')}"
        ok, new_id, _ = sync_event_to_google_calendar(f"🏖️ [排休-{r.get('leave_type', '')}] {emp}", desc, s_d, e_d, "08:00", "17:00", r.get("google_event_id", ""))
        if ok:
            df_sch.at[idx, "google_event_id"] = new_id
            success_cnt += 1
        else:
            fail_cnt += 1
    save_data("schedules", df_sch)

    df_swaps = load_data("shift_swaps", SWAP_COLS)
    for idx, r in df_swaps.iterrows():
        emp = str(r.get("employee_name", "")).strip()
        if not emp:
            continue
        s_d = str(r.get("swap_date", "")).strip()
        shift = str(r.get("assigned_shift", ""))
        s_t = "08:00" if "A" in shift else "08:30"
        e_t = "16:30" if "A" in shift else "17:00"
        desc = f"調班同仁：{emp}\n調整班別：{shift}\n事由：{r.get('reason', '')}"
        ok, new_id, _ = sync_event_to_google_calendar(f"🔄 [調班] {emp} -> {shift}", desc, s_d, s_d, s_t, e_t, r.get("google_event_id", ""))
        if ok:
            df_swaps.at[idx, "google_event_id"] = new_id
            success_cnt += 1
        else:
            fail_cnt += 1
    save_data("shift_swaps", df_swaps)

    return success_cnt, fail_cnt, "全面同步完成"

# 頁面配置
st.set_page_config(page_title="內部行政管理系統", layout="wide")
st.title("🏢 公司內部行政管理系統")

sh_conn = get_gspread_client()
if sh_conn:
    st.sidebar.success("🟢 雲端 Google 試算表：連線同步中")
else:
    st.sidebar.info("🟠 儲存狀態：本地安全儲存模式 (切換頁面不遺失)")

if "calendar_id" in st.secrets and HAS_CALENDAR_LIB:
    st.sidebar.success(f"📲 Google 日曆雙向同步：已啟用 ({st.secrets['calendar_id']})")
elif not HAS_CALENDAR_LIB:
    st.sidebar.warning("⚠️ 系統正在載入日曆套件，請稍候重整")
else:
    st.sidebar.warning("⚠️ Google 日曆同步：未在 Secrets 設定 calendar_id")

menu = st.sidebar.radio(
    "系統模組切換",
    [
        "🗓️ 互動月曆視圖",
        "👥 人員名單管理",
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
    roster = {}
    for emp in CURRENT_EMPLOYEES:
        if emp == "勝順":
            roster[emp] = "未排班"
        elif month % 2 != 0:
            roster[emp] = "A班" if emp in ["伊臻", "涵玟"] else "B班"
        else:
            roster[emp] = "A班" if emp == "美釵" else "B班"

    df_swaps = load_data("shift_swaps", SWAP_COLS)
    if not df_swaps.empty:
        swaps = df_swaps[df_swaps["swap_date"].astype(str) == str(query_date_str)]
        for _, row in swaps.iterrows():
            roster[str(row["employee_name"])] = str(row["assigned_shift"])
    return roster

# ==================== 模組 0: 互動月曆視圖 ====================
if menu == "🗓️ 互動月曆視圖":
    st.header("🗓️ 整合工作行事、排休與調班月曆")
    
    df_schedules = load_data("schedules", SCHEDULE_COLS)
    df_works = load_data("work_events", WORK_EVENT_COLS)
    df_swaps = load_data("shift_swaps", SWAP_COLS)

    c_m_top1, c_m_top2 = st.columns([4, 1])
    with c_m_top1:
        st.info("💡 藍色色塊代表【工作行事】，橘色色塊代表【同仁排休】，紫色色塊代表【臨時調班】。點擊事件可查看詳細說明。")
    with c_m_top2:
        if st.button("🔄 重新整理月曆資料"):
            st.rerun()

    st.caption(f"📊 目前資料庫中載入項目統計：💼 工作行事 {len(df_works)} 筆 ｜ 🏖️ 同仁排休 {len(df_schedules)} 筆 ｜ 🔄 臨時調班 {len(df_swaps)} 筆")

    calendar_events = []

    # 1. 組裝排休事件
    for _, row in df_schedules.iterrows():
        emp = str(row.get("employee_name", "")).strip()
        s_date_raw = str(row.get("start_date", "")).strip()
        if not emp or not s_date_raw:
            continue
        e_date_raw = str(row.get("end_date", "")).strip() if str(row.get("end_date", "")).strip() else s_date_raw

        try:
            e_dt_obj = datetime.strptime(e_date_raw, "%Y-%m-%d").date()
            cal_end_date = (e_dt_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            cal_end_date = e_date_raw

        calendar_events.append({
            "title": f"🏖️ {emp} [{row.get('leave_type', '排休')}]",
            "start": s_date_raw,
            "end": cal_end_date,
            "allDay": True,
            "backgroundColor": "#FF7A00",
            "borderColor": "#FF7A00",
            "textColor": "#FFFFFF",
            "extendedProps": {
                "類別": "🏖️ 同仁排休",
                "對象": emp,
                "項目": f"{row.get('leave_type', '休假')}假",
                "日期區間": f"{s_date_raw} ~ {e_date_raw}" if s_date_raw != e_date_raw else s_date_raw,
                "時間明細": f"{row.get('start_datetime', '')} 至 {row.get('end_datetime', '')}",
                "詳細內容/備註": str(row.get("note", "")) if str(row.get("note", "")).strip() else "無填寫備註",
            },
        })

    # 2. 組裝調班事件
    for _, row in df_swaps.iterrows():
        emp = str(row.get("employee_name", "")).strip()
        sw_d = str(row.get("swap_date", "")).strip()
        if not emp or not sw_d:
            continue
        try:
            sw_dt_obj = datetime.strptime(sw_d, "%Y-%m-%d").date()
            cal_sw_end = (sw_dt_obj + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            cal_sw_end = sw_d

        shift_title = str(row.get("assigned_shift", "調班"))
        calendar_events.append({
            "title": f"🔄 {emp} -> {shift_title}",
            "start": sw_d,
            "end": cal_sw_end,
            "allDay": True,
            "backgroundColor": "#8E24AA",
            "borderColor": "#8E24AA",
            "textColor": "#FFFFFF",
            "extendedProps": {
                "類別": "🔄 臨時調班",
                "對象": emp,
                "項目": f"變更班別為 {shift_title}",
                "日期區間": sw_d,
                "時間明細": "全日班表調整",
                "詳細內容/備註": str(row.get("reason", "")) if str(row.get("reason", "")).strip() else "無填寫調班事由",
            },
        })

    # 3. 組裝工作行事事件 (塊狀乾淨顯示)
    for _, row in df_works.iterrows():
        title = str(row.get("title", "")).strip()
        s_d = str(row.get("start_date", "")).strip()
        if not title or not s_d:
            continue
        e_d = str(row.get("end_date", "")).strip() if str(row.get("end_date", "")).strip() else s_d
        
        real_start_time = parse_and_normalize_time(row.get("start_time"), default="09:00")
        real_end_time = parse_and_normalize_time(row.get("end_time"), default="10:00")
        pic = str(row.get("person_in_charge", "全體")).strip()

        calendar_events.append({
            "title": f"[{pic}] {title}",
            "start": f"{s_d}T{real_start_time}:00",
            "end": f"{e_d}T{real_end_time}:00",
            "allDay": False,
            "backgroundColor": "#1E88E5",
            "borderColor": "#1E88E5",
            "textColor": "#FFFFFF",
            "extendedProps": {
                "類別": "💼 工作行事",
                "對象": pic,
                "項目": title,
                "日期區間": f"{s_d} 至 {e_d}" if s_d != e_d else s_d,
                "時間明細": f"{real_start_time} ~ {real_end_time}",
                "詳細內容/備註": str(row.get("description", "")) if str(row.get("description", "")).strip() else "無詳細說明",
            },
        })

    calendar_options = {
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,listMonth",
        },
        "buttonText": {
            "today": "今天",
            "month": "月曆",
            "week": "週檢視",
            "list": "清單",
        },
        "initialView": "dayGridMonth",
        "navLinks": True,
        "selectable": True,
        "editable": False,
        "locale": "zh-tw",
        "eventDisplay": "block",
        "displayEventTime": True,
        "displayEventEnd": True,
        "eventTimeFormat": {
            "hour": "2-digit",
            "minute": "2-digit",
            "hour12": False,
            "meridiem": False
        },
    }

    custom_css = """
        .fc-daygrid-day-frame {
            min-height: 120px !important;
        }
        .fc-event {
            padding: 3px 5px !important;
            margin: 2px 1px !important;
            border-radius: 4px !important;
            border: none !important;
            box-shadow: 0 1px 2px rgba(0,0,0,0.12) !important;
            cursor: pointer !important;
        }
        .fc-event-time {
            font-size: 0.75rem !important;
            font-weight: 700 !important;
            color: #FFFFFF !important;
            margin-right: 4px !important;
            display: inline-block !important;
        }
        .fc-event-title {
            font-size: 0.82rem !important;
            font-weight: 500 !important;
            color: #FFFFFF !important;
            white-space: normal !important;
            line-height: 1.3 !important;
        }
        .fc-button {
            background-color: #1E88E5 !important;
            border-color: #1E88E5 !important;
            font-size: 0.85rem !important;
        }
        .fc-button-active {
            background-color: #1565C0 !important;
            border-color: #1565C0 !important;
        }
    """

    cal_out = calendar(
        events=calendar_events,
        options=calendar_options,
        custom_css=custom_css,
        key="main_calendar_view",
    )

    if cal_out and "eventClick" in cal_out:
        detail = cal_out["eventClick"]["event"].get("extendedProps", {})
        st.divider()
        st.markdown(f"### 📌 事項詳情：{detail.get('項目', '')}")
        col_c1, col_c2, col_c3 = st.columns(3)
        col_c1.metric("類別與性質", detail.get("類別", ""))
        col_c2.metric("對象 / 負責同仁", detail.get("對象", ""))
        col_c3.metric("時間時段", detail.get("時間明細", ""))
        st.markdown(f"**🗓️ 日期區間：** `{detail.get('日期區間', '')}`")
        st.markdown("**📝 內容與備註說明：**")
        st.info(detail.get("詳細內容/備註", "無"))

    st.divider()
    st.subheader("📋 最新排休、調班與行事明細清單")
    list_tab1, list_tab2, list_tab3 = st.tabs(["💼 實際工作行事清單", "🏖️ 同仁排休明細", "🔄 臨時調班紀錄"])
    with list_tab1:
        if not df_works.empty:
            st.dataframe(df_works[["id", "start_date", "end_date", "start_time", "end_time", "person_in_charge", "title", "description"]].tail(30).iloc[::-1], width="stretch")
        else:
            st.info("目前尚無任何工作行事紀錄。")
    with list_tab2:
        if not df_schedules.empty:
            st.dataframe(df_schedules[["id", "employee_name", "leave_type", "start_date", "end_date", "start_datetime", "end_datetime", "note"]].tail(30).iloc[::-1], width="stretch")
        else:
            st.info("目前尚無任何同仁排休紀錄。")
    with list_tab3:
        if not df_swaps.empty:
            st.dataframe(df_swaps[["id", "swap_date", "employee_name", "assigned_shift", "reason"]].tail(30).iloc[::-1], width="stretch")
        else:
            st.info("目前尚無任何臨時調班紀錄。")

# ==================== 模組 1: 人員名單管理 ====================
elif menu == "👥 人員名單管理":
    st.header("👥 公司在職人員管理")
    st.info("💡 在此進行的人員【新增】、【編輯修改】或【刪減移除】，將會全自動即時連動至全系統所有下拉選單。")

    tab_add_emp, tab_edit_emp, tab_del_emp, tab_list_emp = st.tabs(
        ["➕ 新增同仁名單", "✏️ 編輯同仁姓名", "🗑️ 刪減同仁名單", "📋 在職員工總覽"]
    )

    with tab_add_emp:
        col_ne1, col_ne2 = st.columns([1, 1])
        with col_ne1:
            st.subheader("➕ 輸入新進同仁姓名")
            new_emp_name = st.text_input("同仁真實姓名", placeholder="例如：冠宇、志明", key="input_new_emp")
            
            if st.button("確認新增人員", key="btn_add_emp"):
                clean_name = str(new_emp_name).strip()
                if not clean_name:
                    st.warning("請填寫同仁姓名！")
                elif clean_name in CURRENT_EMPLOYEES:
                    st.error(f"⚠️ 同仁「{clean_name}」已經存在於系統名單中，請勿重複新增。")
                else:
                    df_emp = load_data("employees", EMPLOYEE_COLS)
                    valid_ids = pd.to_numeric(df_emp["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    now_str = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

                    new_row = pd.DataFrame([{
                        "id": str(new_id),
                        "name": clean_name,
                        "created_at": now_str
                    }])
                    df_emp = pd.concat([df_emp, new_row], ignore_index=True)
                    save_data("employees", df_emp)

                    st.success(f"🎉 成功新增同仁「{clean_name}」！系統選單已即時同步更新。")
                    st.rerun()

        with col_ne2:
            st.subheader("💡 使用提示")
            st.write("1. 新增後，所有同仁在請假排班、行事曆選擇負責人、登記影印與借用設備時，皆能立即選擇該同仁。")
            st.write("2. 資料會即刻同步至雲端 Google 試算表 `employees` 表單與本地備援資料庫，確保永久留存。")

    with tab_edit_emp:
        col_ee1, col_ee2 = st.columns([1, 1])
        with col_ee1:
            st.subheader("✏️ 編輯修改現有同仁姓名")
            target_edit_name = st.selectbox("請選擇欲修改的同仁", CURRENT_EMPLOYEES, key="sel_edit_emp")
            updated_name = st.text_input("請輸入修改後的新姓名", value=target_edit_name, key="input_edit_name")

            if st.button("確認儲存修改", key="btn_edit_emp"):
                clean_up_name = str(updated_name).strip()
                if not clean_up_name:
                    st.warning("同仁姓名不能為空白！")
                elif clean_up_name == target_edit_name:
                    st.info("姓名未作任何更動。")
                elif clean_up_name in CURRENT_EMPLOYEES:
                    st.error(f"⚠️ 名稱「{clean_up_name}」已存在於其他同仁名單中，請使用其他名稱！")
                else:
                    df_emp = load_data("employees", EMPLOYEE_COLS)
                    df_emp.loc[df_emp["name"].astype(str) == target_edit_name, "name"] = clean_up_name
                    save_data("employees", df_emp)

                    st.success(f"✅ 已成功將「{target_edit_name}」更改為「{clean_up_name}」！全系統選單已同步更新。")
                    st.rerun()

        with col_ee2:
            st.subheader("💡 編輯注意事項")
            st.write("• 修改同仁姓名後，系統內的在職名單將立即更新。")
            st.write("• 過去已登記的歷史紀錄會保留，若歷史紀錄也需修改可至該模組編輯。")

    with tab_del_emp:
        col_de1, col_de2 = st.columns([1, 1])
        with col_de1:
            st.subheader("🗑️ 刪減移除離職同仁")
            target_del_name = st.selectbox("請選擇欲移除的同仁", CURRENT_EMPLOYEES, key="sel_del_emp")
            confirm_del = st.checkbox(f"我確認要從在職人員名單中移除「{target_del_name}」", key="chk_confirm_del")

            if st.button("確認刪除同仁", key="btn_del_emp"):
                if not confirm_del:
                    st.warning("⚠️ 為防止誤刪，請先勾選上方的確認方框！")
                elif len(CURRENT_EMPLOYEES) <= 1:
                    st.error("⚠️ 系統至少需保留 1 位在職人員，無法全數刪除！")
                else:
                    df_emp = load_data("employees", EMPLOYEE_COLS)
                    df_emp = df_emp[df_emp["name"].astype(str) != target_del_name]
                    save_data("employees", df_emp)

                    st.success(f"🗑️ 已成功自名單中移除同仁「{target_del_name}」！全系統名單已同步更新。")
                    st.rerun()

        with col_de2:
            st.subheader("⚠️ 刪減安全說明")
            st.write("1. 刪減同仁會將其自未來的排班、請假與工作行事負責人選單中除名。")
            st.write("2. 過去該同仁已留存之排班紀錄、行事曆歷史與影印數據**不會被刪除**，資料依然完整保留供日後備查。")

    with tab_list_emp:
        st.subheader("目前在職同仁名單")
        df_emp_disp = load_data("employees", EMPLOYEE_COLS)
        st.dataframe(df_emp_disp.rename(columns={"id": "系統編號", "name": "同仁姓名", "created_at": "建立時間"}), width="stretch")

# ==================== 模組 2: 匯出每月綜合報表 ====================
elif menu == "📤 匯出每月綜合報表":
    st.header("📤 每月工作行程、排班及影印報表輸出")
    col_y, col_m = st.columns(2)
    with col_y:
        selected_year = st.selectbox("選擇年份", [2025, 2026, 2027], index=1)
    with col_m:
        selected_month = st.selectbox("選擇月份", list(range(1, 13)), index=datetime.today().month - 1)

    _, num_days = py_calendar.monthrange(selected_year, selected_month)
    month_str = f"{selected_year}-{selected_month:02d}"

    df_schedules = load_data("schedules", SCHEDULE_COLS)
    roster_rows = []
    weekdays_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]

    for day in range(1, num_days + 1):
        cur_d_str = f"{selected_year}-{selected_month:02d}-{day:02d}"
        cur_dt = datetime.strptime(cur_d_str, "%Y-%m-%d")
        w_day = weekdays_zh[cur_dt.weekday()]
        daily_shifts = get_daily_roster(cur_d_str)

        row = {"日期": cur_d_str, "星期": w_day}
        for emp in CURRENT_EMPLOYEES:
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

    df_works = load_data("work_events", WORK_EVENT_COLS)
    if not df_works.empty:
        df_month_events = df_works[
            (df_works["start_date"].astype(str) <= f"{month_str}-{num_days:02d}") &
            (df_works["end_date"].astype(str) >= f"{month_str}-01")
        ].copy()
    else:
        df_month_events = pd.DataFrame()

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
            st.dataframe(summary_p, width="stretch")
            st.dataframe(df_month_prints[["log_date", "user_name", "pages", "print_type", "purpose"]].rename(
                columns={"log_date": "影印日期", "user_name": "登記人", "pages": "張數", "print_type": "色彩規格", "purpose": "用途"}
            ), width="stretch")
        else:
            st.info("該月份目前尚無影印輸出紀錄。")

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

# ==================== 模組 3: 班表、排休與調班 ====================
elif menu == "📅 班表、排休與調班":
    st.header("📅 班表排班、排休與調班 (支援即時自動連動 Google 日曆與提醒)")

    tab_leave, tab_edit_leave, tab_swap, tab_schedule_view = st.tabs(
        ["📝 請假/排休登記", "✏️ 修改排休紀錄", "🔄 臨時調班申請", "👀 當日班表與休假查詢"]
    )

    with tab_leave:
        st.subheader("新增排休 (每日限制最多 1 人，登記自動寫入手機日曆)")
        col_l1, col_l2 = st.columns(2)
        with col_l1:
            emp = st.selectbox("請假同仁", CURRENT_EMPLOYEES, key="leave_emp")
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
                df_schedules = load_data("schedules", SCHEDULE_COLS)
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
                    summary = f"🏖️ [排休-{l_type}] {emp}"
                    desc = f"請假同仁：{emp}\n假別：{l_type}\n時間：{start_dt_str} 至 {end_dt_str}\n原因備註：{l_note}"
                    synced, cal_id, sync_msg = sync_event_to_google_calendar(
                        summary, desc, str(s_date), str(e_date), s_time.strftime("%H:%M"), e_time.strftime("%H:%M")
                    )

                    valid_ids = pd.to_numeric(df_schedules["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    new_row = pd.DataFrame([{
                        "id": str(new_id), "employee_name": emp, "leave_type": l_type,
                        "start_datetime": start_dt_str, "end_datetime": end_dt_str,
                        "start_date": str(s_date), "end_date": str(e_date), "note": str(l_note),
                        "google_event_id": str(cal_id)
                    }])
                    df_schedules = pd.concat([df_schedules, new_row], ignore_index=True)
                    save_data("schedules", df_schedules)

                    if synced:
                        st.success("✅ 排休登記成功！已全自動同步至手機 Google 日曆並排定提醒！")
                    else:
                        st.success("✅ 排休登記成功！(日曆同步已留存備援)")
                    st.rerun()

    with tab_edit_leave:
        st.subheader("✏️ 修改排休紀錄 (連動更新 Google 日曆)")
        df_schedules = load_data("schedules", SCHEDULE_COLS)
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
                    edit_emp = st.selectbox("請假同仁", CURRENT_EMPLOYEES, index=CURRENT_EMPLOYEES.index(curr["employee_name"]) if curr["employee_name"] in CURRENT_EMPLOYEES else 0, key="ed_l_emp")
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

                if st.button("確認儲存修改並更新日曆", key="btn_save_edit_leave"):
                    s_dt = f"{edit_sd} 08:00"
                    e_dt = f"{edit_ed} 17:00"
                    existing_cal_id = str(curr.get("google_event_id", "")).strip()

                    summary = f"🏖️ [排休-{edit_type}] {edit_emp}"
                    desc = f"請假同仁：{edit_emp}\n假別：{edit_type}\n時間：{s_dt} 至 {e_dt}\n原因備註：{edit_note}"
                    synced, final_cal_id, _ = sync_event_to_google_calendar(
                        summary, desc, str(edit_sd), str(edit_ed), "08:00", "17:00", existing_cal_id=existing_cal_id
                    )

                    df_schedules.loc[df_schedules["id"].astype(str) == target_id, ["employee_name", "leave_type", "start_date", "end_date", "start_datetime", "end_datetime", "note", "google_event_id"]] = [
                        edit_emp, edit_type, str(edit_sd), str(edit_ed), s_dt, e_dt, str(edit_note), str(final_cal_id)
                    ]
                    save_data("schedules", df_schedules)
                    st.success("排休資料已成功修改，且 Google 日曆已即時連動更新！")
                    st.rerun()
            else:
                st.info("目前尚無有效的排休紀錄可供修改。")
        else:
            st.info("目前尚無任何排休紀錄可供修改。")

    with tab_swap:
        st.subheader("臨時調班登記 (自動連動至 Google 日曆並提醒)")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            swap_d = st.date_input("調班日期", key="swap_date")
            swap_emp = st.selectbox("調班同仁", CURRENT_EMPLOYEES, key="swap_emp")
        with col_s2:
            new_shift = st.selectbox("變更為班別", ["A班 (08:00-16:30)", "B班 (08:30-17:00)", "休假/不排班"])
            swap_reason = st.text_input("調班事由")

        if st.button("確認調班並同步日曆"):
            assigned = str(new_shift.split(" ")[0])
            s_t = "08:00" if "A" in assigned else "08:30"
            e_t = "16:30" if "A" in assigned else "17:00"

            summary = f"🔄 [調班] {swap_emp} -> {assigned}"
            desc = f"調班同仁：{swap_emp}\n變更為：{assigned}\n日期：{swap_d}\n事由：{swap_reason}"
            synced, cal_id, _ = sync_event_to_google_calendar(
                summary, desc, str(swap_d), str(swap_d), s_t, e_t
            )

            df_swaps = load_data("shift_swaps", SWAP_COLS)
            if not df_swaps.empty:
                df_swaps = df_swaps[~((df_swaps["swap_date"].astype(str) == str(swap_d)) & (df_swaps["employee_name"].astype(str) == str(swap_emp)))]
            valid_ids = pd.to_numeric(df_swaps["id"], errors="coerce").dropna()
            new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
            new_row = pd.DataFrame([{
                "id": str(new_id), "swap_date": str(swap_d), "employee_name": str(swap_emp),
                "assigned_shift": assigned, "reason": str(swap_reason),
                "google_event_id": str(cal_id)
            }])
            df_swaps = pd.concat([df_swaps, new_row], ignore_index=True)
            save_data("shift_swaps", df_swaps)
            st.success(f"✅ {swap_d} {swap_emp} 已成功調整為 {assigned}，並已同步至 Google 日曆！")
            st.rerun()

    with tab_schedule_view:
        st.subheader("查詢當日實際班表與提醒")
        check_date = st.date_input("選擇欲查詢日期", value=date.today())
        daily_shifts = get_daily_roster(str(check_date))

        df_sch_check = load_data("schedules", SCHEDULE_COLS)
        today_leaves = df_sch_check[
            (df_sch_check["start_date"].astype(str) <= str(check_date)) &
            (df_sch_check["end_date"].astype(str) >= str(check_date))
        ]
        if not today_leaves.empty:
            for _, lv in today_leaves.iterrows():
                st.warning(f"🏖️ 休假提醒：**{lv['employee_name']}** 今日請【{lv['leave_type']}假】 (原因: {lv['note']})")

        df_sw_check = load_data("shift_swaps", SWAP_COLS)
        today_swaps = df_sw_check[df_sw_check["swap_date"].astype(str) == str(check_date)]
        if not today_swaps.empty:
            for _, sw in today_swaps.iterrows():
                st.info(f"🔄 調班提醒：**{sw['employee_name']}** 今日臨時調整為【{sw['assigned_shift']}】 (原因: {sw['reason']})")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🅰️ A班 (08:00 - 16:30)")
            a_members = [k for k, v in daily_shifts.items() if "A班" in v]
            st.write("、".join(a_members) if a_members else "無")
        with col_b:
            st.markdown("#### 🅱️ B班 (08:30 - 17:00)")
            b_members = [k for k, v in daily_shifts.items() if "B班" in v]
            st.write("、".join(b_members) if b_members else "無")

# ==================== 模組 4: 工作行事登記 (過期自動隱藏 + 切換自動帶入正確數據) ====================
elif menu == "📌 工作行事登記":
    st.header("📌 工作行事管理 (新增/修改/刪除 均即時全自動連動 Google 日曆)")

    with st.expander("🔄 批次將現存所有「行事+排休+調班」同步至 Google 日曆 (點此展開)"):
        st.write("點擊下方按鈕可一次性將系統內的所有工作行程、同仁排休與臨時調班全面同步至 Google 日曆並開啟手機提醒：")
        col_b1, col_b2 = st.columns([1, 2])
        with col_b1:
            if st.button("🚀 立即全面批次同步至 Google 日曆"):
                with st.spinner("正在逐筆寫入 Google 日曆中，請稍候..."):
                    succ, fail, b_msg = batch_sync_all_to_google_calendar()
                if succ > 0:
                    st.success(f"🎉 全面同步成功！共完成 {succ} 筆行程/排休/調班至 Google 日曆 (失敗 {fail} 筆)。")
                else:
                    st.warning(f"提示：{b_msg} (成功 {succ} 筆, 失敗 {fail} 筆)")
        with col_b2:
            st.caption("註：系統會將所有項目自動設定在台灣時間 (UTC+8)，並預設活動前 15 分鐘與 12 小時發出手機提醒。")

    tab_act_add, tab_act_manage = st.tabs(["➕ 新增工作行事", "✏️ 編輯與刪除既有行事"])

    # 1. 新增工作行事
    with tab_act_add:
        col_e1, col_e2 = st.columns([1, 2])
        with col_e1:
            st.subheader("填寫工作行事")
            today_tw = datetime.now(TAIWAN_TZ).date()
            event_s_date = st.date_input("開始日期", value=today_tw, key="we_s_date")
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
                if not str(event_title).strip():
                    st.warning("請填寫活動/事項標題。")
                elif event_s_date == event_e_date and event_s_time >= event_e_time:
                    st.error("同一天活動的結束時間必須晚於開始時間！")
                else:
                    s_t_str = event_s_time.strftime("%H:%M")
                    e_t_str = event_e_time.strftime("%H:%M")

                    summary = f"💼 [{event_pic}] {event_title.strip()}"
                    desc = f"負責人：{event_pic}\n內容說明：{event_desc.strip()}"
                    synced, cal_id, sync_msg = sync_event_to_google_calendar(
                        summary, desc, str(event_s_date), str(event_e_date),
                        s_t_str, e_t_str
                    )

                    df_w = load_data("work_events", WORK_EVENT_COLS)
                    valid_ids = pd.to_numeric(df_w["id"], errors="coerce").dropna()
                    new_id = int(valid_ids.max() + 1) if not valid_ids.empty else 1
                    new_row = pd.DataFrame([{
                        "id": str(new_id), "start_date": str(event_s_date), "end_date": str(event_e_date),
                        "start_time": s_t_str, "end_time": e_t_str,
                        "title": str(event_title).strip(), "person_in_charge": str(event_pic), "description": str(event_desc).strip(),
                        "google_event_id": str(cal_id)
                    }])
                    df_w = pd.concat([df_w, new_row], ignore_index=True)
                    save_data("work_events", df_w)

                    if synced:
                        st.success(f"✅ 工作行事登記成功！時間：{s_t_str}~{e_t_str}，已全自動同步至手機 Google 日曆！")
                    else:
                        st.warning(f"⚠️ 行事已登記，但日曆自動同步提示：{sync_msg}")
                    st.rerun()

        with col_e2:
            st.subheader("📋 最新工作行事清單")
            df_w = load_data("work_events", WORK_EVENT_COLS)
            disp_cols = ["id", "start_date", "end_date", "start_time", "end_time", "person_in_charge", "title", "description"]
            if not df_w.empty:
                st.dataframe(df_w[disp_cols].tail(30).iloc[::-1], width="stretch")
            else:
                st.info("目前尚無任何工作行事紀錄。")

    # 2. 編輯與刪除既有行事 (解決已過期顯示與選取後資料不帶入問題)
    with tab_act_manage:
        st.subheader("✏️ 編輯或刪除工作行事 (操作一站搞定，連動更新/刪除手機日曆)")
        
        df_w_all = load_data("work_events", WORK_EVENT_COLS)
        today_tw = datetime.now(TAIWAN_TZ).date()
        
        # 提供是否顯示已過期歷史行事的切換選項
        show_expired = st.checkbox("🔍 包含已過期的歷史行程 (預設只顯示今天及未來的行事)", value=False, key="chk_show_expired")

        if not df_w_all.empty and len(df_w_all) > 0:
            # 依條件過濾已過期行程
            if not show_expired:
                # 結束日期大於等於今天的有效行程
                df_w_valid = df_w_all[df_w_all["end_date"].astype(str) >= str(today_tw)]
            else:
                df_w_valid = df_w_all

            if not df_w_valid.empty and len(df_w_valid) > 0:
                # 組裝清晰易讀的選單標籤
                w_options = {}
                for _, r in df_w_valid.iterrows():
                    r_title = str(r.get("title", "")).strip()
                    if not r_title:
                        continue
                    r_id = str(r["id"])
                    r_pic = str(r.get("person_in_charge", "全體"))
                    r_sd = str(r.get("start_date", ""))
                    r_ed = str(r.get("end_date", ""))
                    r_st = parse_and_normalize_time(r.get("start_time"), "09:00")
                    r_et = parse_and_normalize_time(r.get("end_time"), "10:00")
                    
                    date_range_str = f"{r_sd} {r_st}~{r_et}" if r_sd == r_ed else f"{r_sd}~{r_ed} {r_st}~{r_et}"
                    label = f"編號 {r_id} | [{r_pic}] {r_title} ({date_range_str})"
                    w_options[label] = r_id

                if w_options:
                    w_choice = st.selectbox("請選擇欲處理的工作事項", list(w_options.keys()), key="sel_manage_work")
                    target_w_id = w_options[w_choice]
                    w_curr = df_w_all[df_w_all["id"].astype(str) == target_w_id].iloc[0]

                    # 解析當前選定項目的真實數據
                    cur_title_val = str(w_curr.get("title", ""))
                    cur_pic_val = str(w_curr.get("person_in_charge", "全體"))
                    try:
                        cur_wsd_val = datetime.strptime(str(w_curr.get("start_date", "")).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_wsd_val = today_tw
                    try:
                        cur_wed_val = datetime.strptime(str(w_curr.get("end_date", "")).strip(), "%Y-%m-%d").date()
                    except Exception:
                        cur_wed_val = cur_wsd_val
                    try:
                        cur_wst_str = parse_and_normalize_time(w_curr.get("start_time"), "09:00")
                        cur_wst_val = datetime.strptime(cur_wst_str, "%H:%M").time()
                    except Exception:
                        cur_wst_val = time(9, 0)
                    try:
                        cur_wet_str = parse_and_normalize_time(w_curr.get("end_time"), "10:00")
                        cur_wet_val = datetime.strptime(cur_wet_str, "%H:%M").time()
                    except Exception:
                        cur_wet_val = time(10, 0)
                    cur_desc_val = str(w_curr.get("description", ""))

                    # 關鍵修復：將每個元件的 key 加上 target_w_id，確保切換選項時數據 100% 精準自動載入
                    ew_col1, ew_col2 = st.columns(2)
                    with ew_col1:
                        new_w_title = st.text_input("活動/事項標題", value=cur_title_val, key=f"ew_title_{target_w_id}")
                        pic_idx = EVENT_RESPONSIBLES.index(cur_pic_val) if cur_pic_val in EVENT_RESPONSIBLES else 0
                        new_w_pic = st.selectbox("負責人", EVENT_RESPONSIBLES, index=pic_idx, key=f"ew_pic_{target_w_id}")
                        new_w_sd = st.date_input("開始日期", value=cur_wsd_val, key=f"ew_sd_{target_w_id}")
                        new_w_st = st.time_input("開始時間", value=cur_wst_val, key=f"ew_st_{target_w_id}")
                    with ew_col2:
                        min_ed = new_w_sd
                        valid_wed = cur_wed_val if cur_wed_val >= min_ed else min_ed
                        new_w_ed = st.date_input("結束日期", value=valid_wed, min_value=min_ed, key=f"ew_ed_{target_w_id}")
                        new_w_et = st.time_input("結束時間", value=cur_wet_val, key=f"ew_et_{target_w_id}")
                        new_w_desc = st.text_area("內容說明", value=cur_desc_val, key=f"ew_desc_{target_w_id}")

                    st.divider()
                    col_btn_save, col_btn_del = st.columns([1, 1])

                    with col_btn_save:
                        if st.button("💾 儲存修改 (連動更新日曆)", key=f"btn_save_{target_w_id}"):
                            existing_cal_id = str(w_curr.get("google_event_id", "")).strip()
                            s_t_norm = new_w_st.strftime("%H:%M")
                            e_t_norm = new_w_et.strftime("%H:%M")

                            summary = f"💼 [{new_w_pic}] {new_w_title.strip()}"
                            desc = f"負責人：{new_w_pic}\n內容說明：{new_w_desc.strip()}"
                            synced, final_cal_id, sync_msg = sync_event_to_google_calendar(
                                summary, desc, str(new_w_sd), str(new_w_ed),
                                s_t_norm, e_t_norm,
                                existing_cal_id=existing_cal_id
                            )

                            df_w_all.loc[df_w_all["id"].astype(str) == target_w_id, ["title", "person_in_charge", "start_date", "end_date", "start_time", "end_time", "description", "google_event_id"]] = [
                                str(new_w_title).strip(), str(new_w_pic), str(new_w_sd), str(new_w_ed), s_t_norm, e_t_norm, str(new_w_desc).strip(), str(final_cal_id)
                            ]
                            save_data("work_events", df_w_all)

                            if synced:
                                st.success(f"✅ 工作行事已成功儲存！時間：{s_t_norm}~{e_t_norm}，且手機 Google 日曆已連動更新！")
                            else:
                                st.warning(f"⚠️ 資料已儲存，但日曆連動修改提示：{sync_msg}")
                            st.rerun()

                    with col_btn_del:
                        confirm_del = st.checkbox("確認刪除此事項", key=f"chk_del_{target_w_id}")
                        if st.button("🗑️ 刪除此行程 (連動移除日曆)", key=f"btn_del_{target_w_id}"):
                            if not confirm_del:
                                st.warning("⚠️ 請先勾選「確認刪除此事項」以防止誤按！")
                            else:
                                cal_id = str(w_curr.get("google_event_id", "")).strip()
                                cal_deleted, cal_msg = delete_google_calendar_event(cal_id)

                                df_w_all = df_w_all[df_w_all["id"].astype(str) != target_w_id]
                                save_data("work_events", df_w_all)

                                st.success(f"🗑️ 工作行事已成功刪除！{cal_msg}")
                                st.rerun()
                else:
                    st.info("目前沒有符合條件的工作行事可供編輯。")
            else:
                st.info("目前沒有尚未過期之工作行事。若欲查看已結束之行程，請勾選上方的「包含已過期的歷史行程」。")
        else:
            st.info("目前尚無任何工作行事紀錄。")

# ==================== 模組 5: 3C產品借用申請 ====================
elif menu == "📱 3C產品借用申請":
    st.header("📱 3C產品借用申請與狀況登記")
    tab_dev_add, tab_dev_edit = st.tabs(["➕ 登記借用", "✏️ 修改借用紀錄"])

    with tab_dev_add:
        col_b1, col_b2 = st.columns([1, 2])
        with col_b1:
            borrow_item = st.selectbox("借用申請物品", DEVICES, key="borrow_item")
            borrow_applicant = st.selectbox("申請人", CURRENT_EMPLOYEES, key="borrow_app")
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
                    ed_app = st.selectbox("申請同仁", CURRENT_EMPLOYEES, index=CURRENT_EMPLOYEES.index(b_curr["applicant"]) if b_curr["applicant"] in CURRENT_EMPLOYEES else 0, key="ed_b_app")
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

# ==================== 模組 6: 影印輸出登記 ====================
elif menu == "🖨️ 影印輸出登記":
    st.header("🖨️ 影印輸出登記與每月報表")
    tab_p_reg, tab_p_month = st.tabs(["📝 影印登記", "📊 每月影印統計與輸出"])

    with tab_p_reg:
        with st.form("print_form"):
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                p_user = st.selectbox("登記人姓名", CURRENT_EMPLOYEES)
                p_date = st.date_input("影印日期", value=date.today())
                p_pages = st.number_input("輸出張數", min_value=1, value=1, step=1)
            with col_p2:
                p_type = st.selectbox("色彩規格", ["黑白", "彩色"])
                p_purpose = st.text_input("輸出用途")
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

# ==================== 模組 7: 物資出入庫管理 ====================
elif menu == "📦 物資出入庫管理":
    st.header("📦 物資申請出庫與採購入庫")
    df_supplies = load_data("supplies", ["item_name", "stock"])
    items = [x for x in df_supplies["item_name"].astype(str).tolist() if x.strip()]

    tab_out, tab_in = st.tabs(["📤 領用出庫", "📥 採購入庫"])

    with tab_out:
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            applicant = st.selectbox("領用人", CURRENT_EMPLOYEES, key="mat_out_user")
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
            buyer = st.selectbox("入庫人", CURRENT_EMPLOYEES, key="mat_in_user")
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
