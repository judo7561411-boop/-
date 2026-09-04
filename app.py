import sqlite3
from datetime import date, datetime, time
import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

# 資料庫名稱
DB_NAME = "office_admin.db"

# 預設固定名單與參數
EMPLOYEES = ["伊臻", "美釵", "涵玟", "勝順"]
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
    """初始化所有資料表並具備向下相容機制"""
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

    # 自動補齊欄位防呆檢查
    c.execute("PRAGMA table_info(schedules)")
    columns = [row[1] for row in c.fetchall()]
    if "start_date" not in columns:
        try:
            c.execute("ALTER TABLE schedules ADD COLUMN start_date TEXT")
            c.execute("ALTER TABLE schedules ADD COLUMN end_date TEXT")
            c.execute("ALTER TABLE schedules ADD COLUMN start_datetime TEXT")
            c.execute("ALTER TABLE schedules ADD COLUMN end_datetime TEXT")
        except Exception:
            pass

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
                    event_date TEXT,
                    event_time TEXT,
                    title TEXT,
                    person_in_charge TEXT,
                    description TEXT)"""
    )

    # 預載預設物資
    for item in DEFAULT_SUPPLIES:
        c.execute(
            "INSERT OR IGNORE INTO supplies (item_name, stock) VALUES (?, 0)",
            (item,),
        )

    conn.commit()
    conn.close()


init_db()

# 頁面配置
st.set_page_config(page_title="內部行政管理系統", layout="wide")
st.title("🏢 公司內部行政管理系統")

# 側邊導覽選單（新增月曆視圖選項）
menu = st.sidebar.radio(
    "系統模組切換",
    [
        "🗓️ 互動月曆視圖",
        "📅 班表、排休與調班",
        "📌 工作行事登記",
        "🖨️ 影印輸出登記",
        "📦 物資出入庫管理",
    ],
)


# 計算當日班表函式 (考慮每月輪替與臨時調班)
def get_daily_roster(query_date_str):
    q_date = datetime.strptime(query_date_str, "%Y-%m-%d")
    month = q_date.month

    # 奇數月：A班(伊臻、涵玟)，B班(美釵)；偶數月對調
    if month % 2 != 0:
        roster = {"伊臻": "A班", "涵玟": "A班", "美釵": "B班", "勝順": "未排班"}
    else:
        roster = {"美釵": "A班", "伊臻": "B班", "涵玟": "B班", "勝順": "未排班"}

    # 檢查有無臨時調班紀錄覆蓋
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


# ==================== 模組 0: 互動月曆視圖 ====================
if menu == "🗓️ 互動月曆視圖":
    st.header("🗓️ 整合工作行事與同仁排休月曆")
    st.info("💡 藍色代表【工作事項】，橘色代表【人員排休】。點擊事件可查看詳細資訊。")

    conn = sqlite3.connect(DB_NAME)
    # 讀取排休與工作事件
    df_schedules = pd.read_sql(
        "SELECT employee_name, leave_type, start_date, end_date, start_datetime, end_datetime, note FROM schedules",
        conn,
    )
    df_works = pd.read_sql(
        "SELECT event_date, event_time, title, person_in_charge, description FROM work_events",
        conn,
    )
    conn.close()

    # 轉換資料庫紀錄為 FullCalendar 事件格式
    calendar_events = []

    # 1. 加入排休事件 (橘色系)
    for _, row in df_schedules.iterrows():
        # 如果沒有具體時間，則預設以日期呈現
        s_date = (
            row["start_date"]
            if pd.notna(row["start_date"])
            else str(date.today())
        )
        calendar_events.append(
            {
                "title": f"🏖️ {row['employee_name']} ({row['leave_type']})",
                "start": s_date,
                "end": (
                    row["end_date"] if pd.notna(row["end_date"]) else s_date
                ),
                "backgroundColor": "#FF7A00",
                "borderColor": "#FF7A00",
                "textColor": "#FFFFFF",
                "extendedProps": {
                    "人員": row["employee_name"],
                    "假別": row["leave_type"],
                    "時間": f"{row['start_datetime']} 至 {row['end_datetime']}",
                    "原因備註": row["note"],
                },
            }
        )

    # 2. 加入工作事項 (藍色系)
    for _, row in df_works.iterrows():
        calendar_events.append(
            {
                "title": f"💼 {row['title']} ({row['person_in_charge']})",
                "start": row["event_date"],
                "backgroundColor": "#1E88E5",
                "borderColor": "#1E88E5",
                "textColor": "#FFFFFF",
                "extendedProps": {
                    "負責人": row["person_in_charge"],
                    "時間": row["event_time"],
                    "內容說明": row["description"],
                },
            }
        )

    # 設定 FullCalendar 參數
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

    # 渲染互動月曆組件
    calendar_result = calendar(
        events=calendar_events,
        options=calendar_options,
        custom_css="""
        .fc-event-title {
            font-weight: 500;
        }
        """,
        key="full_calendar_view",
    )

    # 如果點擊了某個事件，於下方顯示詳細卡片
    if calendar_result and "eventClick" in calendar_result:
        clicked_event = calendar_result["eventClick"]["event"]
        st.subheader("📌 事項詳細資訊")
        st.json(clicked_event.get("extendedProps", {}))

# ==================== 模組 1: 班表、排休與調班 ====================
elif menu == "📅 班表、排休與調班":
    st.header("📅 班表排班、排休與調班管理")
    tab_leave, tab_swap, tab_schedule_view = st.tabs(
        ["📝 請假/排休登記", "🔄 臨時調班申請", "👀 當日班表與休假查詢"]
    )

    # [子分頁 1] 請假與排休登記 (防呆：每天限1人)
    with tab_leave:
        st.subheader("排休登記 (每日限制請假最多 1 人)")
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

            if datetime.strptime(
                start_dt_str, "%Y-%m-%d %H:%M"
            ) >= datetime.strptime(end_dt_str, "%Y-%m-%d %H:%M"):
                st.error("結束時間必須晚於開始時間！")
            else:
                # 檢查當日是否已有人排休
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                conflict = c.execute(
                    """SELECT employee_name, start_date, end_date FROM schedules 
                       WHERE employee_name != ? 
                       AND NOT (end_date < ? OR start_date > ?)""",
                    (emp, str(s_date), str(e_date)),
                ).fetchall()

                if conflict:
                    conflict_info = "、".join(
                        [f"{row[0]} ({row[1]}~{row[2]})" for row in conflict]
                    )
                    st.error(
                        f"⚠️ 無法登記！當日已有同仁排休：{conflict_info}。依規定每日僅限 1 人排休。"
                    )
                    conn.close()
                else:
                    c.execute(
                        """INSERT INTO schedules (employee_name, leave_type, start_datetime, end_datetime, start_date, end_date, note) 
                                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            emp,
                            l_type,
                            start_dt_str,
                            end_dt_str,
                            str(s_date),
                            str(e_date),
                            l_note,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    st.success("排休登記成功！")
                    st.rerun()

    # [子分頁 2] 臨時調班登記
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
            st.success(
                f"{swap_d} {swap_emp} 已成功調整為 {new_shift.split(' ')[0]}！"
            )

    # [子分頁 3] 班表與排休清單
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
            """SELECT employee_name AS 姓名, leave_type AS 假別, 
                      start_datetime AS 開始時間, end_datetime AS 結束時間, note AS 備註 
               FROM schedules ORDER BY id DESC LIMIT 20""",
            conn,
        )
        st.dataframe(df_l, width="stretch")
        conn.close()

# ==================== 模組 2: 工作行事登記 ====================
elif menu == "📌 工作行事登記":
    st.header("📌 工作行事登記與清單")
    col_e1, col_e2 = st.columns([1, 2])

    with col_e1:
        st.subheader("新增工作行事")
        event_d = st.date_input("事項日期", key="we_date")
        event_t = st.time_input("事項時間", value=time(9, 0), key="we_time")
        event_title = st.text_input("事項標題 (例如：例行週會、客戶合約送件)")
        event_pic = st.selectbox("負責人", EMPLOYEES, key="we_pic")
        event_desc = st.text_area("內容說明")

        if st.button("新增行事"):
            if event_title:
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute(
                    """INSERT INTO work_events (event_date, event_time, title, person_in_charge, description) 
                             VALUES (?, ?, ?, ?, ?)""",
                    (
                        str(event_d),
                        event_t.strftime("%H:%M"),
                        event_title,
                        event_pic,
                        event_desc,
                    ),
                )
                conn.commit()
                conn.close()
                st.success("工作行事已登記！")
                st.rerun()
            else:
                st.warning("請填寫事項標題。")

    with col_e2:
        st.subheader("📋 所有工作事項清單")
        conn = sqlite3.connect(DB_NAME)
        df_w = pd.read_sql(
            """SELECT event_date AS 日期, event_time AS 時間, 
                      person_in_charge AS 負責人, title AS 事項標題, description AS 說明 
               FROM work_events ORDER BY event_date DESC""",
            conn,
        )
        conn.close()
        st.dataframe(df_w, width="stretch")

# ==================== 模組 3: 影印輸出登記 ====================
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

# ==================== 模組 4: 物資出入庫管理 ====================
elif menu == "📦 物資出入庫管理":
    st.header("📦 物資申請出庫與採購入庫")
    conn = sqlite3.connect(DB_NAME)
    items = [
        row[0]
        for row in conn.cursor()
        .execute("SELECT item_name FROM supplies ORDER BY id ASC")
        .fetchall()
    ]
    conn.close()

    tab_out, tab_in = st.tabs(["📤 領用出庫", "📥 採購入庫"])

    with tab_out:
        col_o1, col_o2 = st.columns(2)
        with col_o1:
            applicant = st.selectbox("領用人", EMPLOYEES, key="mat_out_user")
            out_item = st.selectbox("物資品項", items, key="mat_out_item")
        with col_o2:
            out_qty = st.number_input(
                "領用數量", min_value=1, value=1, step=1, key="mat_out_q"
            )
            out_d = st.date_input("領用日期", value=date.today())

        if st.button("確認出庫", key="btn_mat_out"):
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            cur_stock = c.execute(
                "SELECT stock FROM supplies WHERE item_name = ?", (out_item,)
            ).fetchone()[0]
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
                st.success(
                    f"出庫成功！{out_item} 剩餘庫存：{cur_stock - out_qty}"
                )
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
            in_qty = st.number_input(
                "採購進貨數量", min_value=1, value=1, step=1, key="mat_in_q"
            )
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