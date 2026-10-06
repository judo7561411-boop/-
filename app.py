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

# 短期快取保護，防止頁面卡死
@st.cache_data(ttl=30, show_spinner=False)
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
    st.cache_data.clear()
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
            st.cache_data.clear()
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

    # 3. 組裝工作行事事件
    for _, row in df_works.iterrows():
        title = str(row.get("title", "")).strip()
        s_d = str(row.get("start_date", "")).strip()
        if not title or not s_d:
            continue
        e_d = str(row.get("end_date", "")).strip() if str(row.get("
