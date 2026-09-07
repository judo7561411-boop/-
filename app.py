import sqlite3
from datetime import date, datetime, time
import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

# 資料庫檔案名稱
DB_NAME = "office_admin.db"

# 預設固定名單與參數
EMPLOYEES = ["伊臻", "美釵", "涵玟", "勝順"]
EVENT_RESPONSIBLES = ["全體", "伊臻", "美釵", "涵玟", "勝順"]
DEVICES = ["平板-1", "平板-2", "平板-3", "投影機"]
DEFAULT_SUPPLIES = [
    "酒精",
    "漂白水",
    "衛生紙",
    "擦手紙",
    "洗手乳",
    "垃圾袋(小)",
    "垃圾袋(中)",
    "垃圾袋(大)",
    "廁所清潔劑",
    "洗碗精",
]


def init_db():
    """初始化資料庫並自動檢查修復資料表結構"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # 1. 物資庫存與出入庫表
    c.execute(
        """CREATE TABLE IF NOT EXISTS supplies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_name TEXT UNIQUE,
                    stock INTEGER)"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS inventory_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_type TEXT,
                    handler TEXT,
                    item_name TEXT,
                    quantity INTEGER,
                    log_date TEXT)"""
    )

    # 2. 影印登記表
    c.execute(
        """CREATE TABLE IF NOT EXISTS print_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_name TEXT,
                    pages INTEGER,
                    print_type TEXT,
                    purpose TEXT,
                    log_date TEXT)"""
    )

    # 3. 排休假紀錄表
    c.execute(
        """CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_name TEXT,
                    leave_type TEXT,
                    start_datetime TEXT,
                    end_datetime TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    note TEXT)"""
    )

    # 4. 臨時調班紀錄表
    c.execute(
        """CREATE TABLE IF NOT EXISTS shift_swaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    swap_date TEXT,
                    employee_name TEXT,
                    assigned_shift TEXT,
                    reason TEXT)"""
    )

    # 5. 工作行事表
    c.execute(
        """CREATE TABLE IF NOT EXISTS work_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_date TEXT,
                    end_date TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    title TEXT,
                    person_in_charge TEXT,
                    description TEXT)"""
    )

    # 6. 3C產品借用申請表
    c.execute(
        """CREATE TABLE IF NOT EXISTS device_borrows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_name TEXT,
                    applicant TEXT,
                    borrow_date TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    condition TEXT,
                    fault_desc TEXT,
                    created_at TEXT)"""
    )

    # 預載預設物資品項
    for item in DEFAULT_SUPPLIES:
        c.execute(
            "INSERT OR IGNORE INTO supplies (item_name, stock) VALUES (?, 0)",
            (item,),
        )

    conn.commit()
    conn.close()


init_db()

# 網頁配置
st.set_page_config(page_title="內部行政管理系統", layout="wide")
st.title("🏢 公司內部行政管理系統")

# 側邊導覽選單
menu = st.sidebar.radio(
    "系統模組切換",
    [
        "🗓️ 互動月曆視圖",
        "📅 班表、排休與調班",
        "📌 工作行事登記",
        "📱 3C產品借用申請",
        "🖨️ 影印輸出登記",
        "📦 物資出入庫管理",
    ],
)


def get_daily_roster(query_date_str):
    """計算指定日期的排班人員（奇偶月輪替與臨時調班）"""
    q_date = datetime.strptime(query_date_str, "%Y-%m-%d")
    month = q_date.month

    # 奇數月：A班(伊臻、涵玟)，B班(美釵)；偶數月對調
    if month % 2 != 0:
        roster = {"伊臻": "A班", "涵玟": "A班", "美釵": "B班", "勝順": "未排班"}
    else:
        roster = {"美釵": "A班", "伊臻": "B班", "涵玟": "B班", "勝順": "未排班"}

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    swaps = c.execute(
        "SELECT employee_name, assigned_shift FROM shift_swaps WHERE swap_date = ?",
        (query_date_str,),
    ).fetchall()
    conn.close()

    for emp, sft in swaps:
        roster[emp] = sft
    return roster


# ==================== 模組 0: 互動月曆視圖 (強化行事檢視) ====================
if menu == "🗓️ 互動月曆視圖":
    st.header("🗓️ 整合工作行事與同仁排休月曆")
    st.info("💡 藍色代表【工作事項】，橘色代表【同仁排休】。點擊月曆方塊可在下方查看詳細內容。")

    conn = sqlite3.connect(DB_NAME)
    df_schedules = pd.read_sql(
        "SELECT employee_name, leave_type, start_date, end_date, start_datetime, end_datetime, note FROM schedules",
        conn,
    )
    df_works = pd.read_sql(
        "SELECT start_date, end_date, start_time, end_time, title, person_in_charge, description FROM work_events",
        conn,
    )
    conn.close()

    calendar_events = []

    # 1. 組裝排休事件 (橘色系)
    for _, row in df_schedules.iterrows():
        s_date = row["start_date"] if pd.notna(row["start_date"]) and row["start_date"] else str(date.today())
        e_date = row["end_date"] if pd.notna(row["end_date"]) and row["end_date"] else s_date
        calendar_events.append(
            {
                "title": f"🏖️ {row['employee_name']} [{row['leave_type']}]",
                "start": s_date,
                "end": e_date,
                "backgroundColor": "#FF7A00",
                "borderColor": "#FF7A00",
                "textColor": "#FFFFFF",
                "extendedProps": {
                    "類別": "🏖️ 同仁排休",
                    "對象": row["employee_name"],
                    "項目": f"{row['leave_type']}假",
                    "日期區間": f"{s_date} ~ {e_date}",
                    "時間明細": f"{row['start_datetime']} 至 {row['end_datetime']}",
                    "詳細內容/備註": row["note"] if row["note"] else "無填寫備註",
                },
            }
        )

    # 2. 組裝工作行事事件 (藍色系，標題直接加入時間以便閱讀)
    for _, row in df_works.iterrows():
        s_d = row["start_date"] if pd.notna(row["start_date"]) and row["start_date"] else str(date.today())
        e_d = row["end_date"] if pd.notna(row["end_date"]) and row["end_date"] else s_d
        s_t = row["start_time"] if row["start_time"] else "09:00"
        e_t = row["end_time"] if row["end_time"] else "10:00"

        # 標題直覺化：顯示時間 + 負責人 + 工作名稱
        event_label = f"💼 {s_t} [{row['person_in_charge']}] {row['title']}"

        calendar_events.append(
            {
                "title": event_label,
                "start": f"{s_d}T{s_t}:00",
                "end": f"{e_d}T{e_t}:00",
                "backgroundColor": "#1E88E5",
                "borderColor": "#1E88E5",
                "textColor": "#FFFFFF",
                "extendedProps": {
                    "類別": "💼 工作行事",
                    "對象": row["person_in_charge"],
                    "項目": row["title"],
                    "日期區間": f"{s_d} 至 {e_d}" if s_d != e_d else s_d,
                    "時間明細": f"{s_t} ~ {e_t}",
                    "詳細內容/備註": row["description"] if row["description"] else "無詳細說明",
                },
            }
        )

    # FullCalendar 視圖設定
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
        "eventTimeFormat": {"hour": "2-digit", "minute": "2-digit", "hour12": False},
    }

    cal_out = calendar(
        events=calendar_events,
        options=calendar_options,
        custom_css="""
        .fc-event-title { font-weight: 500; font-size: 0.85rem; }
        .fc-toolbar-title { font-size: 1.3rem !important; font-weight: bold; }
        """,
        key="main_calendar_view",
    )

    # 點擊事項展開精美資訊卡片
    if cal_out and "eventClick" in cal_out:
        detail = cal_out["eventClick"]["event"].get("extendedProps", {})
        st.divider()
        st.markdown(f"### 📌 事項詳情：{detail.get('項目', '')}")
        col_c1, col_c2, col_c3 = st.columns(3)
        col_c1.metric("類別與性質", detail.get("類別", ""))
        col_c2.metric("負責人 / 請假同仁", detail.get("對象", ""))
        col_c3.metric("時間時段", detail.get("時間明細", ""))

        st.markdown(f"**🗓️ 活動日期：** `{detail.get('日期區間', '')}`")
        st.markdown(f"**📝 內容與說明：**")
        st.info(detail.get("詳細內容/備註", "無"))

    # 月曆下方：工作行事與排休的清晰清單檢視
    st.divider()
    st.subheader("📋 近期實際行事與排休總覽清單")
    list_tab1, list_tab2 = st.tabs(["💼 實際工作行事清單", "🏖️ 同仁排休明細"])

    with list_tab1:
        conn = sqlite3.connect(DB_NAME)
        df_w_list = pd.read_sql(
            """SELECT start_date AS 開始日期, end_date AS 結束日期,
                      start_time || ' ~ ' || end_time AS 活動時段,
                      person_in_charge AS 負責人, title AS 事項標題,
                      description AS 詳細說明
               FROM work_events ORDER BY start_date DESC LIMIT 30""",
            conn,
        )
        conn.close()
        st.dataframe(df_w_list, width="stretch")

    with list_tab2:
        conn = sqlite3.connect(DB_NAME)
        df_s_list = pd.read_sql(
            """SELECT employee_name AS 姓名, leave_type AS 假別,
                      start_date AS 開始日期, end_date AS 結束日期,
                      start_datetime AS 開始時段, end_datetime AS 結束時段,
                      note AS 請假原因
               FROM schedules ORDER BY start_date DESC LIMIT 30""",
            conn,
        )
        conn.close()
        st.dataframe(df_s_list, width="stretch")

# ==================== 模組 1: 班表、排休與調班 (含排休修改) ====================
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
            l_type = st.selectbox(
                "假別",
                ["特休", "補休", "公假", "公出", "事假", "病假"],
                key="leave_type",
            )
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
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                conflict = c.execute(
                    """SELECT employee_name, start_date, end_date FROM schedules 
                       WHERE employee_name != ? 
                       AND NOT (end_date < ? OR start_date > ?)""",
                    (emp, str(s_date), str(e_date)),
                ).fetchall()

                if conflict:
                    conflict_info = "、".join([f"{row[0]} ({row[1]}~{row[2]})" for row in conflict])
                    st.error(f"⚠️ 無法登記！當日已有同仁排休：{conflict_info}。依規定每日僅限 1 人排休。")
                    conn.close()
                else:
                    c.execute(
                        """INSERT INTO schedules (employee_name, leave_type, start_datetime, end_datetime, start_date, end_date, note) 
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (emp, l_type, start_dt_str, end_dt_str, str(s_date), str(e_date), l_note),
                    )
                    conn.commit()
                    conn.close()
                    st.success("排休登記成功！")
                    st.rerun()

    with tab_edit_leave:
        st.subheader("✏️ 修改或更新排休紀錄")
        conn = sqlite3.connect(DB_NAME)
        leave_records = conn.cursor().execute(
            "SELECT id, employee_name, leave_type, start_date, end_date, note FROM schedules ORDER BY id DESC"
        ).fetchall()
        conn.close()

        if leave_records:
            record_options = {f"編號 {r[0]} | {r[1]} - {r[2]} ({r[3]} ~ {r[4]})": r[0] for r in leave_records}
            selected_label = st.selectbox("請選擇欲修改的排休紀錄", list(record_options.keys()))
            target_id = record_options[selected_label]

            conn = sqlite3.connect(DB_NAME)
            curr = conn.cursor().execute(
                "SELECT employee_name, leave_type, start_date, end_date, note, start_datetime, end_datetime FROM schedules WHERE id = ?",
                (target_id,),
            ).fetchone()
            conn.close()

            c_e1, c_e2 = st.columns(2)
            with c_e1:
                edit_emp = st.selectbox("請假同仁", EMPLOYEES, index=EMPLOYEES.index(curr[0]), key="ed_l_emp")
                edit_type = st.selectbox(
                    "假別",
                    ["特休", "補休", "公假", "公出", "事假", "病假"],
                    index=["特休", "補休", "公假", "公出", "事假", "病假"].index(curr[1]),
                    key="ed_l_type",
                )
                cur_sd = datetime.strptime(curr[2], "%Y-%m-%d").date() if curr[2] else date.today()
                edit_sd = st.date_input("開始休假日期", value=cur_sd, key="ed_l_sd")
            with c_e2:
                cur_ed = datetime.strptime(curr[3], "%Y-%m-%d").date() if curr[3] else edit_sd
                edit_ed = st.date_input("結束休假日期", value=cur_ed, min_value=edit_sd, key="ed_l_ed")
                edit_note = st.text_input("原因備註", value=curr[4] if curr[4] else "", key="ed_l_note")

            if st.button("確認儲存修改", key="btn_save_edit_leave"):
                s_dt = f"{edit_sd} 08:00"
                e_dt = f"{edit_ed} 17:00"
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                conflict = c.execute(
                    """SELECT employee_name FROM schedules 
                       WHERE id != ? AND employee_name != ? 
                       AND NOT (end_date < ? OR start_date > ?)""",
                    (target_id, edit_emp, str(edit_sd), str(edit_ed)),
                ).fetchall()

                if conflict:
                    st.error(f"⚠️ 無法修改！修改後的時間區間已有同仁（{conflict[0][0]}）排休。")
                    conn.close()
                else:
                    c.execute(
                        """UPDATE schedules SET employee_name = ?, leave_type = ?, 
                                  start_date = ?, end_date = ?, start_datetime = ?, end_datetime = ?, note = ? 
                           WHERE id = ?""",
                        (edit_emp, edit_type, str(edit_sd), str(edit_ed), s_dt, e_dt, edit_note, target_id),
                    )
                    conn.commit()
                    conn.close()
                    st.success("排休資料已成功修改！")
                    st.rerun()
        else:
            st.info("目前尚無任何排休紀錄可供修改。")

    with tab_swap:
        st.subheader("臨時調班登記")
        st.info("班表預設規則：奇數月 A班(伊臻、涵玟)、B班(美釵)；偶數月對調。如有臨時變動請在此登記。")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            swap_d = st.date_input("調班日期", key="swap_date")
            swap_emp = st.selectbox("調班同仁", EMPLOYEES, key="swap_emp")
        with col_s2:
            new_shift = st.selectbox(
                "變更為班別",
                ["A班 (08:00-16:30)", "B班 (08:30-17:00)", "休假/不排班"],
            )
            swap_reason = st.text_input("調班事由")

        if st.button("確認調班"):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute(
                "DELETE FROM shift_swaps WHERE swap_date = ? AND employee_name = ?",
                (str(swap_d), swap_emp),
            )
            c.execute(
                "INSERT INTO shift_swaps (swap_date, employee_name, assigned_shift, reason) VALUES (?, ?, ?, ?)",
                (str(swap_d), swap_emp, new_shift.split(" ")[0], swap_reason),
            )
            conn.commit()
            conn.close()
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

        st.divider()
        st.subheader("近期排休登記明細")
        conn = sqlite3.connect(DB_NAME)
        df_l = pd.read_sql(
            """SELECT id AS 編號, employee_name AS 姓名, leave_type AS 假別, 
                      start_datetime AS 開始時間, end_datetime AS 結束時間, note AS 備註 
               FROM schedules ORDER BY id DESC LIMIT 20""",
            conn,
        )
        st.dataframe(df_l, width="stretch")
        conn.close()

# ==================== 模組 2: 工作行事登記 (支援新增與修改) ====================
elif menu == "📌 工作行事登記":
    st.header("📌 工作行事管理")
    tab_act_add, tab_act_edit = st.tabs(["➕ 新增工作行事", "✏️ 修改既有行事"])

    with tab_act_add:
        col_e1, col_e2 = st.columns([1, 2])
        with col_e1:
            st.subheader("填寫工作事項")
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
                if not event_title:
                    st.warning("請填寫活動/事項標題。")
                elif event_s_date == event_e_date and event_s_time >= event_e_time:
                    st.error("同一天活動的結束時間必須晚於開始時間！")
                else:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO work_events (start_date, end_date, start_time, end_time, title, person_in_charge, description) 
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(event_s_date),
                            str(event_e_date),
                            event_s_time.strftime("%H:%M"),
                            event_e_time.strftime("%H:%M"),
                            event_title,
                            event_pic,
                            event_desc,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success("工作行事已成功登記！")
                    st.rerun()

        with col_e2:
            st.subheader("📋 最新工作行事清單")
            conn = sqlite3.connect(DB_NAME)
            df_w = pd.read_sql(
                """SELECT id AS 編號, start_date AS 開始日期, end_date AS 結束日期, 
                          start_time AS 開始時間, end_time AS 結束時間, 
                          person_in_charge AS 負責人, title AS 事項標題, description AS 說明 
                   FROM work_events ORDER BY start_date DESC LIMIT 30""",
                conn,
            )
            conn.close()
            st.dataframe(df_w, width="stretch")

    with tab_act_edit:
        st.subheader("✏️ 修改既有工作行事")
        conn = sqlite3.connect(DB_NAME)
        work_list = conn.cursor().execute(
            "SELECT id, title, start_date, person_in_charge FROM work_events ORDER BY id DESC"
        ).fetchall()
        conn.close()

        if work_list:
            w_options = {f"編號 {w[0]} | [{w[3]}] {w[1]} ({w[2]})": w[0] for w in work_list}
            w_choice = st.selectbox("選擇欲修改的事項", list(w_options.keys()), key="edit_w_select")
            target_w_id = w_options[w_choice]

            conn = sqlite3.connect(DB_NAME)
            w_data = conn.cursor().execute(
                "SELECT title, person_in_charge, start_date, end_date, start_time, end_time, description FROM work_events WHERE id = ?",
                (target_w_id,),
            ).fetchone()
            conn.close()

            ew_col1, ew_col2 = st.columns(2)
            with ew_col1:
                new_w_title = st.text_input("活動/事項標題", value=w_data[0], key="ew_title")
                new_w_pic = st.selectbox(
                    "負責人",
                    EVENT_RESPONSIBLES,
                    index=EVENT_RESPONSIBLES.index(w_data[1]),
                    key="ew_pic",
                )
                cur_wsd = datetime.strptime(w_data[2], "%Y-%m-%d").date() if w_data[2] else date.today()
                new_w_sd = st.date_input("開始日期", value=cur_wsd, key="ew_sd")
                cur_wst = datetime.strptime(w_data[4], "%H:%M").time() if w_data[4] else time(9, 0)
                new_w_st = st.time_input("開始時間", value=cur_wst, key="ew_st")

            with ew_col2:
                cur_wed = datetime.strptime(w_data[3], "%Y-%m-%d").date() if w_data[3] else new_w_sd
                new_w_ed = st.date_input("結束日期", value=cur_wed, min_value=new_w_sd, key="ew_ed")
                cur_wet = datetime.strptime(w_data[5], "%H:%M").time() if w_data[5] else time(10, 0)
                new_w_et = st.time_input("結束時間", value=cur_wet, key="ew_et")
                new_w_desc = st.text_area("內容說明", value=w_data[6] if w_data[6] else "", key="ew_desc")

            if st.button("儲存行事修改", key="btn_save_edit_work"):
                if not new_w_title.strip():
                    st.warning("標題不可為空！")
                else:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute(
                        """UPDATE work_events 
                           SET title = ?, person_in_charge = ?, start_date = ?, end_date = ?, 
                               start_time = ?, end_time = ?, description = ? 
                           WHERE id = ?""",
                        (
                            new_w_title,
                            new_w_pic,
                            str(new_w_sd),
                            str(new_w_ed),
                            new_w_st.strftime("%H:%M"),
                            new_w_et.strftime("%H:%M"),
                            new_w_desc,
                            target_w_id,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success("工作行事已成功更新！")
                    st.rerun()
        else:
            st.info("目前尚無工作行事可供修改。")

# ==================== 模組 3: 3C產品借用申請 (支援新增與修改) ====================
elif menu == "📱 3C產品借用申請":
    st.header("📱 3C產品借用申請與狀況登記")
    tab_dev_add, tab_dev_edit = st.tabs(["➕ 登記借用", "✏️ 修改借用紀錄"])

    with tab_dev_add:
        col_b1, col_b2 = st.columns([1, 2])
        with col_b1:
            st.subheader("借用表單")
            borrow_item = st.selectbox("借用申請物品", DEVICES, key="borrow_item")
            borrow_applicant = st.selectbox("申請人", EMPLOYEES, key="borrow_app")
            borrow_date = st.date_input("借用日期", value=date.today(), key="borrow_d")

            col_bt1, col_bt2 = st.columns(2)
            with col_bt1:
                borrow_s_time = st.time_input("借用開始時間", value=time(9, 0), key="borrow_st")
            with col_bt2:
                borrow_e_time = st.time_input("預計結束時間", value=time(17, 0), key="borrow_et")

            borrow_condition = st.radio("借用狀況檢查", ["良好", "故障"], horizontal=True, key="borrow_cond")
            fault_description = ""
            if borrow_condition == "故障":
                fault_description = st.text_area("⚠️ 故障說明", placeholder="請具體描述異常情況...", key="dev_f_desc")

            if st.button("送出借用申請"):
                if borrow_s_time >= borrow_e_time:
                    st.error("結束時間必須晚於開始時間！")
                elif borrow_condition == "故障" and not fault_description.strip():
                    st.warning("狀況為故障時，請務必填寫故障說明！")
                else:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute(
                        """INSERT INTO device_borrows (device_name, applicant, borrow_date, start_time, end_time, condition, fault_desc, created_at) 
                                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            borrow_item,
                            borrow_applicant,
                            str(borrow_date),
                            borrow_s_time.strftime("%H:%M"),
                            borrow_e_time.strftime("%H:%M"),
                            borrow_condition,
                            fault_description,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"{borrow_applicant} 借用 {borrow_item} 登記成功！")
                    st.rerun()

        with col_b2:
            st.subheader("📋 3C借用歷程清單")
            conn = sqlite3.connect(DB_NAME)
            df_borrows = pd.read_sql(
                """SELECT id AS 編號, borrow_date AS 借用日期, applicant AS 申請人, device_name AS 物品, 
                          start_time || ' ~ ' || end_time AS 借用時段, condition AS 狀況, 
                          fault_desc AS 故障說明 
                   FROM device_borrows ORDER BY id DESC LIMIT 30""",
                conn,
            )
            conn.close()
            st.dataframe(df_borrows, width="stretch")

    with tab_dev_edit:
        st.subheader("✏️ 修改或更新 3C 借用紀錄")
        conn = sqlite3.connect(DB_NAME)
        b_records = conn.cursor().execute(
            "SELECT id, applicant, device_name, borrow_date, condition FROM device_borrows ORDER BY id DESC"
        ).fetchall()
        conn.close()

        if b_records:
            b_opts = {f"編號 {r[0]} | {r[1]} 借用 {r[2]} ({r[3]} - {r[4]})": r[0] for r in b_records}
            b_choice = st.selectbox("請選擇欲修改的借用紀錄", list(b_opts.keys()), key="ed_b_select")
            target_b_id = b_opts[b_choice]

            conn = sqlite3.connect(DB_NAME)
            b_curr = conn.cursor().execute(
                "SELECT device_name, applicant, borrow_date, start_time, end_time, condition, fault_desc FROM device_borrows WHERE id = ?",
                (target_b_id,),
            ).fetchone()
            conn.close()

            eb_col1, eb_col2 = st.columns(2)
            with eb_col1:
                ed_item = st.selectbox("借用物品", DEVICES, index=DEVICES.index(b_curr[0]), key="ed_b_item")
                ed_app = st.selectbox("申請同仁", EMPLOYEES, index=EMPLOYEES.index(b_curr[1]), key="ed_b_app")
                cur_bd = datetime.strptime(b_curr[2], "%Y-%m-%d").date() if b_curr[2] else date.today()
                ed_bd = st.date_input("借用日期", value=cur_bd, key="ed_b_date")

            with eb_col2:
                cur_st = datetime.strptime(b_curr[3], "%H:%M").time() if b_curr[3] else time(9, 0)
                ed_st = st.time_input("開始時間", value=cur_st, key="ed_b_st")
                cur_et = datetime.strptime(b_curr[4], "%H:%M").time() if b_curr[4] else time(17, 0)
                ed_et = st.time_input("結束時間", value=cur_et, key="ed_b_et")
                ed_cond = st.radio(
                    "設備狀態",
                    ["良好", "故障"],
                    index=0 if b_curr[5] == "良好" else 1,
                    horizontal=True,
                    key="ed_b_cond",
                )
                ed_fault = st.text_area("故障說明", value=b_curr[6] if b_curr[6] else "", key="ed_b_fault")

            if st.button("儲存借用修改", key="btn_save_edit_device"):
                if ed_st >= ed_et:
                    st.error("結束時間必須晚於開始時間！")
                else:
                    conn = sqlite3.connect(DB_NAME)
                    c = conn.cursor()
                    c.execute(
                        """UPDATE device_borrows 
                           SET device_name = ?, applicant = ?, borrow_date = ?, 
                               start_time = ?, end_time = ?, condition = ?, fault_desc = ? 
                           WHERE id = ?""",
                        (
                            ed_item,
                            ed_app,
                            str(ed_bd),
                            ed_st.strftime("%H:%M"),
                            ed_et.strftime("%H:%M"),
                            ed_cond,
                            ed_fault,
                            target_b_id,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success("3C 借用紀錄已成功更新！")
                    st.rerun()
        else:
            st.info("目前尚無 3C 借用紀錄可供修改。")

# ==================== 模組 4: 影印輸出登記 ====================
elif menu == "🖨️ 影印輸出登記":
    st.header("🖨️ 影印輸出登記")
    with st.form("print_form"):
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            p_user = st.selectbox("登記人姓名", EMPLOYEES)
            p_date = st.date_input("影印日期", value=date.today())
            p_pages = st.number_input("輸出張數", min_value=1, value=1, step=1)
        with col_p2:
            p_type = st.selectbox("色彩規格", ["黑白", "彩色"])
            p_purpose = st.text_input("輸出用途 (例如：會議簡報、合約列印)")
        submit_print = st.form_submit_button("登記輸出")

        if submit_print:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute(
                "INSERT INTO print_logs (user_name, pages, print_type, purpose, log_date) VALUES (?, ?, ?, ?, ?)",
                (p_user, p_pages, p_type, p_purpose, str(p_date)),
            )
            conn.commit()
            conn.close()
            st.success("影印紀錄已送出！")

    st.divider()
    st.subheader("近期影印紀錄")
    conn = sqlite3.connect(DB_NAME)
    df_prints = pd.read_sql(
        "SELECT log_date AS 影印日期, user_name AS 登記人, pages AS 張數, print_type AS 規格, purpose AS 用途 FROM print_logs ORDER BY id DESC LIMIT 20",
        conn,
    )
    st.dataframe(df_prints, width="stretch")
    conn.close()

# ==================== 模組 5: 物資出入庫管理 ====================
elif menu == "📦 物資出入庫管理":
    st.header("📦 物資申請出庫與採購入庫")
    conn = sqlite3.connect(DB_NAME)
    items = [row[0] for row in conn.cursor().execute("SELECT item_name FROM supplies ORDER BY id ASC").fetchall()]
    conn.close()

    tab_out, tab_in = st.tabs(["📤 領用出庫", "📥 採購入庫"])

    with tab_out:
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            applicant = st.selectbox("領用人", EMPLOYEES, key="mat_out_user")
            out_item = st.selectbox("物資品項", items, key="mat_out_item")
        with col_o2:
            out_qty = st.number_input("領用數量", min_value=1, value=1, step=1, key="mat_out_q")
            out_d = st.date_input("領用日期", value=date.today())

        if st.button("確認出庫", key="btn_mat_out"):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            cur_stock = c.execute("SELECT stock FROM supplies WHERE item_name = ?", (out_item,)).fetchone()[0]
            if cur_stock >= out_qty:
                c.execute(
                    "UPDATE supplies SET stock = stock - ? WHERE item_name = ?",
                    (out_qty, out_item),
                )
                c.execute(
                    "INSERT INTO inventory_logs (log_type, handler, item_name, quantity, log_date) VALUES (?, ?, ?, ?, ?)",
                    ("領用出庫", applicant, out_item, out_qty, str(out_d)),
                )
                conn.commit()
                st.success(f"出庫成功！{out_item} 剩餘庫存：{cur_stock - out_qty}")
                st.rerun()
            else:
                st.error(f"庫存不足！{out_item} 目前僅剩 {cur_stock}")
            conn.close()

    with tab_in:
        col_i1, col_i2 = st.columns(2)
        with col_i1:
            buyer = st.selectbox("入庫人", EMPLOYEES, key="mat_in_user")
            in_item = st.selectbox("入庫品項", items, key="mat_in_item")
        with col_i2:
            in_qty = st.number_input("採購進貨數量", min_value=1, value=1, step=1, key="mat_in_q")
            in_d = st.date_input("入庫日期", value=date.today())

        if st.button("確認入庫", key="btn_mat_in"):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute(
                "UPDATE supplies SET stock = stock + ? WHERE item_name = ?",
                (in_qty, in_item),
            )
            c.execute(
                "INSERT INTO inventory_logs (log_type, handler, item_name, quantity, log_date) VALUES (?, ?, ?, ?, ?)",
                ("採購入庫", buyer, in_item, in_qty, str(in_d)),
            )
            conn.commit()
            conn.close()
            st.success(f"入庫成功！{in_item} 增加 {in_qty}")
            st.rerun()

    st.divider()
    c_st1, c_st2 = st.columns(2)
    with c_st1:
        st.subheader("現有庫存總覽")
        conn = sqlite3.connect(DB_NAME)
        df_stock = pd.read_sql("SELECT item_name AS 品項, stock AS 庫存 FROM supplies", conn)
        st.dataframe(df_stock, width="stretch")
        conn.close()
    with c_st2:
        st.subheader("最近出入庫歷程")
        conn = sqlite3.connect(DB_NAME)
        df_hist = pd.read_sql(
            "SELECT log_date AS 日期, log_type AS 類型, handler AS 經手人, item_name AS 品項, quantity AS 數量 FROM inventory_logs ORDER BY id DESC LIMIT 10",
            conn,
        )
        st.dataframe(df_hist, width="stretch")
        conn.close()
