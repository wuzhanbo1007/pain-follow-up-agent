"""
SQLite 数据库模块 — Mock 数据持久化存储
Demo 无需真实数据库，SQLite 文件即数据
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import json
import random
import os
from datetime import datetime, timedelta

# 数据库文件路径
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "history.db")


# ======================================================================
# 数据生成配置
# ======================================================================
SURNAMES = [
    "张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗",
    "郑", "梁", "谢", "宋", "唐", "韩", "冯", "于", "董", "萧"
]
GIVEN_NAMES = [
    "建国", "爱华", "志强", "秀英", "伟", "芳", "娜", "敏", "静", "丽",
    "强", "磊", "军", "洋", "勇", "艳", "杰", "涛", "明", "超",
    "秀兰", "桂英", "秀珍", "玉兰", "桂芳", "秀云", "淑珍", "淑芳", "玉珍", "秀梅"
]
DIAGNOSES = ["带状疱疹后神经痛", "腰椎术后疼痛", "糖尿病周围神经痛", "癌性疼痛"]
PAIN_TYPES = ["神经病理性疼痛", "伤害感受性疼痛", "混合性疼痛", "中枢性疼痛"]
DOCTORS = [
    ("D001", "李医生"), ("D002", "王医生"), ("D003", "陈医生"),
    ("D004", "刘医生"), ("D005", "赵医生")
]
SLEEP_QUALITIES = ["好", "一般", "差", "很差"]

# 随访状态枚举（仅作词汇保留，不再用于硬编码免随访）
FOLLOW_UP_STATUSES = [
    "正常随访", "正常随访", "正常随访", "正常随访", "正常随访",
    "正常随访", "正常随访", "正常随访", "正常随访",
    "随访周期已结束", "昨日已复诊", "患者请假", "已安排住院",
]
# 固定随机种子，使整库 Mock 数据（姓名/诊断/风险等）可复现，避免每次重建数字浮动
_SEED_RANDOM = 20240618

# ======================================================================
# 当日随访名单的"确定性设计"（需求文档 v4.0，方案 A）
# ----------------------------------------------------------------------
# 说明：今日谁随访不再硬编码下标，而是完全由各患者的
#   follow_up_plan(频次/周期) + discharge_date + daily_status
# 经判定引擎推导。
#   · 12 位患者（_FOLLOWUP_EXCLUSIONS）分配导致落选的规则类型（C2/C3/C4）。
#   · 其余 38 位为"应随访"，其频次由 _assign_send_freq_offset 给出真实多样
#     的取值（每日 / 每周1次 / 每2周1次 / 分阶段），并通过 offset 保证命中，
#     使名单与"随访计划"面板显示的频次完全一致、且固定 today 下稳定 = 38 人。
#   键 = 患者下标 i（P2024{i:04d}）；刻意避开演示患者 4/5/6。
#   值 = (频次 frequency, 出院前天数 offset, 当日状态 daily_status)
#     offset 直接等于"出院后第 N 天"，故 n = offset 与真实日历无关，可复现。
_FOLLOWUP_EXCLUSIONS = {
    # C2 随访周期已结束（n = offset > duration_days=90）
    1:  ("每日", 100, "normal"),
    2:  ("每日", 110, "normal"),
    3:  ("每日", 120, "normal"),
    # C3 频次未命中：每周1次，n%7 != 0
    7:  ("每周1次", 33, "normal"),
    8:  ("每周1次", 31, "normal"),
    9:  ("每周1次", 37, "normal"),
    # C3 频次未命中：每2周1次，n%14 != 0
    10: ("每2周1次", 30, "normal"),
    11: ("每2周1次", 41, "normal"),
    12: ("每2周1次", 45, "normal"),
    # C4 临时豁免
    13: ("每日", 20, "on_leave"),      # 患者请假
    14: ("每日", 25, "revisited"),     # 昨日已复诊
    15: ("每日", 28, "hospitalized"), # 已安排住院
}

# 应随访患者的处方频次池（与判定引擎 parse_frequency_to_rule 对齐）。
# 演示患者 4/5 的频次仅作调度器占位，实际频次由 A 号 Agent（LLM）从出院小结中生成。
_SEND_FREQ_POOL = [
    "每日",          # 高频（急性期/术后早期）
    "每周一、周四",    # 中频（恢复期）
    "每周三",         # 低频（稳定期）
    "每周二、周五",    # 中频（慢性维持期）
]


def _assign_send_freq_offset(i):
    """为第 i 位应随访患者分配 (频次, 出院偏移)。

    v4.0 频次使用具体星期格式（每周三/每周一、周四），C3 命中由 today.weekday() 判定，
    与 offset 无关。offset 仅需保证 C2（n ≤ duration_days=90）通过即可。
    """
    mod = i % len(_SEND_FREQ_POOL)
    freq = _SEND_FREQ_POOL[mod]
    offset = 5 + (i * 7) % 85       # 5..89，均在 90 天内
    return freq, offset


REPLY_LIBRARY = {
    "low_risk": [
        "今天好多了，基本不疼了，大概1分吧",
        "没什么感觉，睡得也挺好，药按时吃了",
        "恢复得不错，就偶尔有点不舒服，2分左右",
        "今天1分，睡得很好，药吃了",
    ],
    "medium_risk": [
        "今天差不多还是5分，睡觉不好",
        "还是疼，大概4分吧，药吃了但效果一般",
        "昨晚没睡好，疼得醒了两次，今天早上吃了药",
        "今天5分，时好时坏的，睡眠一般",
    ],
    "high_risk": [
        "疼得受不了，7分，昨晚一夜没睡",
        "越来越疼了，昨天8分今天还是8分，药吃了没用",
        "没吃药，感觉吃了也没用，睡也睡不着，疼得厉害",
        "今天9分，痛得浑身冒汗，什么药都不管用",
    ],
    "ambiguous": [
        "还行吧", "不知道怎么说", "就那样", "差不多",
    ]
}

# 演示专用：确保 4 种风险等级各有一个典型患者
DEMO_RISK_LEVELS = [
    ("low_risk",      "今天好多了，基本不疼了，大概1分吧", 1, "好"),
    ("medium_risk",   "今天差不多还是5分，睡觉不好", 5, "差"),
    ("high_risk",     "疼得受不了，7分，昨晚一夜没睡", 7, "很差"),
    ("ambiguous",     "还行吧", None, None),
]


# ======================================================================
# 数据库初始化
# ======================================================================
def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def _calibrate_risk_labels_with_parser(cursor, all_pids):
    """用真实解析器 + RiskEngine 检验所有患者，校准标签（ReplyParser.parse 已为 async）"""
    from core.logging_config import get_logger as _get_log
    _log = _get_log("painsmart.db")
    from llm.parser import ReplyParser
    from engine.risk_engine import RiskEngine
    parser = ReplyParser()
    engine = RiskEngine()
    level_map = {"high": "high_risk", "medium": "medium_risk", "low": "low_risk"}

    for pid in all_pids:
        rp = cursor.execute("SELECT * FROM preset_replies WHERE patient_id=?", (pid,)).fetchone()
        if not rp or rp["risk_tendency"] == "ambiguous":
            continue

        pat = dict(cursor.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone())
        history = [_row_to_dict(r) for r in cursor.execute(
            "SELECT * FROM followup_history WHERE patient_id=? ORDER BY date", (pid,)
        ).fetchall()]
        history_ctx = "\n".join([f"{h['date']}: NRS={h.get('nrs_score','?')}, 睡眠={h.get('sleep_quality','?')}" for h in history[-5:]]) if history else ""

        # 用真实解析器提取（async）
        parsed = await parser.parse(rp["reply_text"], history_ctx)
        today = {"nrs": parsed.get("nrs_score"), "sleep_quality": parsed.get("sleep_quality"), "medication_taken": parsed.get("medication_taken")}
        result = engine.calculate(pid, pat, today, history[-7:])
        actual = level_map.get(result["level"], result["level"])
        old_label = rp["risk_tendency"]

        if old_label != actual:
            _log.debug("[校准] %s(%s): %s → %s (评分:%d)",
                       pat['name'], pid, old_label, actual, result['total_score'])
            cursor.execute("UPDATE preset_replies SET risk_tendency=?, nrs_expected=?, sleep_expected=?, medication_expected=? WHERE patient_id=?",
                           (actual, parsed.get("nrs_score"), parsed.get("sleep_quality"), 1 if parsed.get("medication_taken") is not False else 0, pid))


def _compute_real_risk(patient, today_nrs, today_sleep, today_med, history):
    """使用真实 RiskEngine 计算风险等级"""
    from engine.risk_engine import RiskEngine
    engine = RiskEngine()
    today = {"nrs": today_nrs, "sleep_quality": today_sleep, "medication_taken": today_med}
    result = engine.calculate(patient.get("patient_id", ""), patient, today, history[-7:])
    return result["level"], result["level_label"]


def _set_patient_risk_profile(cursor, pid, level):
    """为患者设定等级：调整年龄、病史、回复文本，确保 RiskEngine 计算结果 = level"""
    # 读取当前患者
    pat = dict(cursor.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone())

    # 根据目标等级设定年龄
    if level == "high_risk":
        age = random.randint(65, 80)  # 偏老
    elif level == "medium_risk":
        age = random.randint(50, 70)
    else:
        age = random.randint(35, 55)  # 偏年轻

    cursor.execute("UPDATE patients SET age=? WHERE patient_id=?", (age, pid))
    pat["age"] = age

    # 删除旧历史，生成匹配的新历史
    cursor.execute("DELETE FROM followup_history WHERE patient_id=?", (pid,))
    for d in range(5, 0, -1):
        date = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
        if level == "high_risk":
            nrs = 9 - d       # 4,5,6,7,8  持续上升
            sleep = "很差" if d >= 3 else "差"
            med = 0
        elif level == "medium_risk":
            nrs = [5, 4, 6, 5, 5][5-d]
            sleep = ["一般", "差", "一般", "一般", "一般"][5-d]
            med = 1 if random.random() > 0.3 else 0
        elif level == "low_risk":
            nrs = d           # 5,4,3,2,1  持续下降
            sleep = "好"
            med = 1
        else:  # ambiguous
            nrs = [4, None, 5, None, 4][5-d]
            sleep = ["一般", None, "一般", None, "一般"][5-d]
            med = random.choice([1, 0])
        nrs = nrs or random.randint(1, 8)
        sleep = sleep or random.choice(SLEEP_QUALITIES)
        med = med if med is not None else 1
        reply = f"今天大概{nrs}分吧，睡眠{sleep}，药{'按时吃了' if med else '忘了吃'}"
        cursor.execute("INSERT INTO followup_history (patient_id,date,nrs_score,sleep_quality,medication_taken,reply_text) VALUES (?,?,?,?,?,?)",
                       (pid, date, nrs, sleep, med, reply))

    # 选择回复文本和风险标签（直接使用分配等级，病史已匹配）
    if level == "ambiguous":
        reply_text = random.choice(REPLY_LIBRARY["ambiguous"])
        today_nrs, today_sleep, today_med = None, None, None
    else:
        reply_text = random.choice(REPLY_LIBRARY[level])
        import re
        m = re.search(r'(\d+)\s*分', reply_text)
        today_nrs = int(m.group(1)) if m else None
        today_sleep = "差" if "没睡好" in reply_text or "没睡" in reply_text else None
        today_med = False if "没吃" in reply_text else None

    cursor.execute(
        "INSERT OR REPLACE INTO preset_replies (patient_id,reply_text,risk_tendency,nrs_expected,sleep_expected,medication_expected) VALUES (?,?,?,?,?,?)",
        (pid, reply_text, level, today_nrs, today_sleep, 1 if today_med is not False else 0))


def _verify_all_risk_labels(cursor, all_pids):
    """用 RiskEngine 重算所有患者等级，确保标签 100% 一致"""
    from engine.risk_engine import RiskEngine
    engine = RiskEngine()
    level_map = {"high": "high_risk", "medium": "medium_risk", "low": "low_risk"}

    for pid in all_pids:
        pat = dict(cursor.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone())
        history = [_row_to_dict(r) for r in cursor.execute(
            "SELECT * FROM followup_history WHERE patient_id=? ORDER BY date", (pid,)
        ).fetchall()]
        rp = cursor.execute("SELECT * FROM preset_replies WHERE patient_id=?", (pid,)).fetchone()
        if not rp:
            continue
        # 模糊回复保留 "ambiguous" 标签，不被覆盖
        if rp["risk_tendency"] == "ambiguous":
            continue

        # 从回复文本提取今日数据
        reply_text = rp["reply_text"]
        import re
        nrs_m = re.search(r'(\d+)\s*分', reply_text)
        today_nrs = int(nrs_m.group(1)) if nrs_m else None
        today_sleep = "差" if "没睡好" in reply_text or "没睡" in reply_text else None
        today_med = False if "没吃" in reply_text or "忘了" in reply_text else None

        today = {"nrs": today_nrs, "sleep_quality": today_sleep, "medication_taken": today_med}
        result = engine.calculate(pid, pat, today, history[-7:])
        actual_level = level_map.get(result["level"], result["level"])

        # 如果标签不一致，更新
        if rp["risk_tendency"] != actual_level:
            cursor.execute("UPDATE preset_replies SET risk_tendency=? WHERE patient_id=?",
                           (actual_level, pid))




def init_db(force=False):
    """初始化数据库表结构并填充 Mock 数据"""
    conn = get_connection()
    cursor = conn.cursor()

    # 创建表
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id   TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            age          INTEGER,
            diagnosis    TEXT,
            discharge_date TEXT,
            doctor_id    TEXT,
            doctor_name  TEXT,
            skip_follow_up INTEGER DEFAULT 0,
            skip_reason  TEXT,
            follow_up_plan TEXT,
            consecutive_no_reply_days INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS followup_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id   TEXT NOT NULL,
            date         TEXT NOT NULL,
            nrs_score    INTEGER,
            sleep_quality TEXT,
            medication_taken INTEGER,
            reply_text   TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        );

        CREATE TABLE IF NOT EXISTS preset_replies (
            patient_id   TEXT PRIMARY KEY,
            reply_text   TEXT NOT NULL,
            risk_tendency TEXT,
            nrs_expected INTEGER,
            sleep_expected TEXT,
            medication_expected INTEGER,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        );

        -- ===== v3.0 新增：出院随访计划 / 随访会话 / 随访过程审阅 =====
        CREATE TABLE IF NOT EXISTS followup_plan (
            plan_id         TEXT PRIMARY KEY,
            patient_id      TEXT NOT NULL,
            discharge_summary TEXT,
            plan_json       TEXT NOT NULL,
            guideline_citations TEXT,
            status          TEXT DEFAULT 'draft',
            doctor_id       TEXT,
            original_snapshot TEXT,
            created_at      TEXT,
            reviewed_at     TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        );

        CREATE TABLE IF NOT EXISTS followup_session (
            session_id      TEXT PRIMARY KEY,
            patient_id      TEXT NOT NULL,
            plan_id         TEXT,
            transcript_json TEXT NOT NULL,
            risk_result     TEXT,
            agent_summary   TEXT,
            created_at      TEXT,
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        );

        CREATE TABLE IF NOT EXISTS followup_review (
            review_id       TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            patient_id      TEXT NOT NULL,
            doctor_score    INTEGER,
            doctor_comment  TEXT,
            track_status    TEXT DEFAULT 'pending_track',
            reviewer_id     TEXT,
            reviewed_at     TEXT,
            audit_snapshot  TEXT,
            FOREIGN KEY (session_id) REFERENCES followup_session(session_id),
            FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
        );

        CREATE INDEX IF NOT EXISTS idx_history_patient ON followup_history(patient_id, date);
        CREATE INDEX IF NOT EXISTS idx_history_date ON followup_history(date);
    """)

    # 检查是否需要填充数据
    cursor.execute("SELECT COUNT(*) FROM patients")
    count = cursor.fetchone()[0]

    # —— v4.0 迁移：确保当日随访判定所需新字段存在，并应用"确定性随访设计" ——
    # 判定是否需要重建患者数据：
    #   1) 关键字段缺失（全新库/旧版库）→ 需要；
    #   2) 字段已存在但未应用"新设计"（即不含分阶段频次"连续4周"标记）→ 需要。
    #      注：用"连续4周"作为新设计指纹，因为它只出现在最新种子的分阶段处方中；
    #      旧库即使含"每周/每2周"排除患者，也不会有该指纹，故能正确触发重建，
    #      避免重启后端后因误判"已是新设计"而不更新多样化的应随访频次。
    cols = [r[1] for r in cursor.execute("PRAGMA table_info(patients)").fetchall()]
    needs_reseed = ("daily_status" not in cols) or ("plan_start_date" not in cols)
    if not needs_reseed and count > 0:
        applied = cursor.execute(
            "SELECT COUNT(*) FROM patients WHERE follow_up_plan LIKE '%连续4周%'"
        ).fetchone()[0]
        needs_reseed = (applied == 0)

    if needs_reseed:
        for col, dflt in (("daily_status", "'normal'"), ("plan_start_date", "NULL")):
            try:
                cursor.execute(f"ALTER TABLE patients ADD COLUMN {col} TEXT DEFAULT {dflt}")
            except Exception:
                pass
        # 重建患者相关表（外键临时关闭），以应用确定性设计，保证固定 today 下稳定命中 38 人
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("DELETE FROM preset_replies")
        cursor.execute("DELETE FROM followup_history")
        cursor.execute("DELETE FROM patients")
        cursor.execute("PRAGMA foreign_keys=ON")
        _seed_data(conn)
        conn.commit()
        conn.close()
        return DB_PATH

    if count == 0 or force:
        if force:
            # 暂时禁用外键约束以便清空数据
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("DELETE FROM preset_replies")
            cursor.execute("DELETE FROM followup_history")
            cursor.execute("DELETE FROM patients")
            cursor.execute("PRAGMA foreign_keys=ON")
        _seed_data(conn)

    conn.commit()
    conn.close()
    return DB_PATH


def _seed_data(conn):
    """填充 Mock 数据：50 人。

    v4.0 起，当日随访名单不再由硬编码下标决定，而是完全由各患者的
    follow_up_plan(频次/周期) + discharge_date + daily_status 经判定引擎推导。
    此处仅做"确定性设计"：为 12 位患者分配导致落选的规则类型（见 _FOLLOWUP_EXCLUSIONS），
    其余 38 位为「每日 + 窗口内 + 正常」必然命中 → 固定 today 下稳定 = 38 人。
    discharge_date 相对"判定用今天"(DEMO_TODAY 或系统当天) 回推 offset 天，
    使 n = offset 与真实日历无关，保证可复现。
    """
    cursor = conn.cursor()
    random.seed(_SEED_RANDOM)  # 固定种子，保证姓名/诊断/风险等可复现
    used_names = set()

    # 判定用"今天"（与 followup_scheduler.get_today 保持一致）
    try:
        from core.config import DEMO_TODAY
    except Exception:
        DEMO_TODAY = ""
    if DEMO_TODAY:
        try:
            _TODAY = datetime.strptime(DEMO_TODAY, "%Y-%m-%d").date()
        except Exception:
            _TODAY = datetime.now().date()
    else:
        _TODAY = datetime.now().date()

    TOTAL_PATIENTS = 50
    demo_pids = []  # 记录演示患者 PID，确保回复正确分配

    for i in range(1, TOTAL_PATIENTS + 1):
        pid = f"P2024{i:04d}"

        # 固定演示患者姓名，保证每次重建数据库都一致
        if i == 4:
            name = "马淑珍"   # 手动演示患者
        elif i == 5:
            name = "宋艳"     # 3 天未回复患者
        else:
            name = random.choice(SURNAMES) + random.choice(GIVEN_NAMES)
            while name in used_names:
                name = random.choice(SURNAMES) + random.choice(GIVEN_NAMES)
        used_names.add(name)

        # —— 当日随访的"确定性设计" ——
        excl = _FOLLOWUP_EXCLUSIONS.get(i)
        if excl:
            frequency, offset, daily_status = excl
        else:
            # 应随访患者：真实多样频次 + 保证命中的偏移（详见 _assign_send_freq_offset）
            frequency, offset = _assign_send_freq_offset(i)
            daily_status = "normal"

        # 演示患者频次由 A 号 Agent（LLM）从出院小结中生成，不再硬编码
        # 种子数据仅给一个占位频次保证调度器能正常判定名单

        doctor = random.choice(DOCTORS)
        age = random.randint(35, 80)
        discharge_date = (_TODAY - timedelta(days=offset)).strftime("%Y-%m-%d")
        follow_up_plan = json.dumps({
            "frequency": frequency,
            "duration_days": 90,
            "pain_type": random.choice(PAIN_TYPES)
        }, ensure_ascii=False)

        consecutive_no_reply = 3 if i == 5 else 0

        cursor.execute("""
            INSERT INTO patients (patient_id, name, age, diagnosis, discharge_date,
                                  doctor_id, doctor_name, skip_follow_up, skip_reason,
                                  follow_up_plan, daily_status, plan_start_date, consecutive_no_reply_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pid, name, age, random.choice(DIAGNOSES), discharge_date,
            doctor[0], doctor[1],
            0, None,   # skip 字段改为由判定引擎运行时计算（此处先置 0/None）
            follow_up_plan, daily_status, None, consecutive_no_reply
        ))

        # 演示患者候选：取前 4 位"非落选"患者（含 P20240004/5/6），
        # 分配确定性的低/中/高/模糊风险画像，保证 Demo 风险分层直观。
        if i not in _FOLLOWUP_EXCLUSIONS:
            demo_pids.append(pid)

        # 生成历史记录（3~7天），演示患者分配匹配的历史
        is_demo_patient = (i not in _FOLLOWUP_EXCLUSIONS) and len(demo_pids) <= 4
        history_days = 5  # 演示患者固定 5 天历史
        if not is_demo_patient:
            history_days = random.randint(3, 7)

        for d in range(history_days, 0, -1):
            date = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")

            if is_demo_patient:
                # d=5(最旧) → d=1(最新)，让历史匹配预期风险等级
                demo_idx = len(demo_pids) - 1
                if demo_idx == 0:   # low_risk: NRS持续下降
                    nrs = d  # d=5→NRS5, d=4→4, d=3→3, d=2→2, d=1→1
                    sleep = "好"
                    med = 1
                elif demo_idx == 1: # medium_risk: NRS平稳中位
                    nrs = [5, 4, 6, 5, 5][5-d]
                    sleep = ["一般", "差", "一般", "一般", "一般"][5-d]
                    med = 1 if random.random() > 0.3 else 0
                elif demo_idx == 2: # high_risk: NRS持续上升
                    nrs = 9 - d  # d=5→NRS4, d=4→5, d=3→6, d=2→7, d=1→8
                    sleep = "很差" if d >= 3 else "差"
                    med = 0  # 未按时用药
                else:               # ambiguous: 数据不完整
                    nrs = [4, None, 5, None, 4][5-d]
                    sleep = ["一般", None, "一般", None, "一般"][5-d]
                    med = random.choice([1, 0])
                nrs = nrs or random.randint(1, 8)
                sleep = sleep or random.choice(SLEEP_QUALITIES)
                med = med if med is not None else (1 if random.random() > 0.3 else 0)
            else:
                nrs = random.randint(1, 8)
                sleep = random.choice(SLEEP_QUALITIES)
                med = 1 if random.random() > 0.15 else 0
            reply = f"今天大概{nrs}分吧，睡眠{sleep}，药{'按时吃了' if med else '忘了吃'}"

            cursor.execute("""
                INSERT INTO followup_history (patient_id, date, nrs_score, sleep_quality, medication_taken, reply_text)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pid, date, nrs, sleep, med, reply))

    # 为前 4 个演示患者分配回复文本 + 调整病史匹配等级
    demo_set = set(demo_pids[:4])
    for idx, pid in enumerate(demo_pids[:4]):
        level = DEMO_RISK_LEVELS[idx][0]
        _set_patient_risk_profile(cursor, pid, level)

    # 为剩余患者按目标分布分配等级
    other_pids = [p for p in demo_pids if p not in demo_set]
    random.shuffle(other_pids)
    n = len(other_pids)

    # 目标分布比例：高11 中10 低10 模糊5
    total_ratio = {"high_risk": 11, "medium_risk": 10, "low_risk": 10, "ambiguous": 5}
    total_patients = n + len(demo_set)
    scale = total_patients / sum(total_ratio.values())
    targets = {k: round(v * scale) for k, v in total_ratio.items()}
    targets["medium_risk"] += total_patients - sum(targets.values())  # 修正round误差
    for demo_lv in [DEMO_RISK_LEVELS[i][0] for i in range(len(demo_set))]:
        targets[demo_lv] = max(0, targets[demo_lv] - 1)

    idx = 0
    for level, count in targets.items():
        for _ in range(count):
            if idx >= n: break
            pid = other_pids[idx]
            _set_patient_risk_profile(cursor, pid, level)
            idx += 1
    # 处理剩余患者（round 误差导致多余的患者）
    while idx < n:
        pid = other_pids[idx]
        _set_patient_risk_profile(cursor, pid, "medium_risk")
        idx += 1

    # 初始化完成 — 校准延后到 LLM 配置就绪后按需执行
    conn.commit()




# ======================================================================
# 数据访问层
# ======================================================================
class PatientDB:
    """患者数据访问对象"""

    def __init__(self):
        init_db()  # 自动初始化

    # -------- 患者 --------
    def get_all_patients(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]

    def get_patient(self, patient_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        conn.close()
        return _row_to_dict(row) if row else None

    def get_patients_by_doctor(self, doctor_id):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM patients WHERE doctor_id = ?", (doctor_id,)).fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]

    def get_send_list(self):
        """获取需发送随访的患者列表"""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM patients WHERE skip_follow_up = 0").fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]

    def get_skip_list(self):
        """获取免随访患者列表"""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM patients WHERE skip_follow_up = 1").fetchall()
        conn.close()
        return [_row_to_dict(r) for r in rows]

    def update_patient(self, patient_id, **kwargs):
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [patient_id]
        conn = get_connection()
        conn.execute(f"UPDATE patients SET {sets} WHERE patient_id=?", values)
        conn.commit()
        conn.close()

    # -------- 随访历史 --------
    def get_history(self, patient_id, days=7):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM followup_history WHERE patient_id=? ORDER BY date DESC LIMIT ?",
            (patient_id, days)
        ).fetchall()
        conn.close()
        result = [_row_to_dict(r) for r in rows]
        result.reverse()
        return result

    def get_histories_batch(self, patient_ids, days=7):
        """批量查询多个患者的历史数据（单次连接），返回 {patient_id: [history_rows]}"""
        if not patient_ids:
            return {}
        placeholders = ",".join("?" * len(patient_ids))
        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM followup_history WHERE patient_id IN ({placeholders}) "
            "ORDER BY patient_id, date DESC",
            list(patient_ids)
        ).fetchall()
        conn.close()
        result = {}
        for r in rows:
            d = dict(r)
            d["skip_follow_up"] = bool(d.get("skip_follow_up", 0))
            d["medication_taken"] = bool(d.get("medication_taken", 0))
            pid = d["patient_id"]
            if pid not in result:
                result[pid] = []
            result[pid].append(d)
        # 取最近 days 条、按日期正序排列
        for pid in list(result.keys()):
            result[pid] = result[pid][:days]
            result[pid].reverse()
        # 未查询到的患者补空列表
        for pid in patient_ids:
            if pid not in result:
                result[pid] = []
        return result

    def add_history(self, patient_id, nrs_score, sleep_quality, medication_taken, reply_text):
        conn = get_connection()
        date = datetime.now().strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO followup_history (patient_id, date, nrs_score, sleep_quality, medication_taken, reply_text)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (patient_id, date, nrs_score, sleep_quality, 1 if medication_taken else 0, reply_text))
        conn.commit()
        conn.close()

    # -------- 预置回复 --------
    def get_all_replies(self):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM preset_replies").fetchall()
        conn.close()
        return {r["patient_id"]: _row_to_dict(r) for r in rows}

    def get_reply(self, patient_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM preset_replies WHERE patient_id=?", (patient_id,)).fetchone()
        conn.close()
        return _row_to_dict(row) if row else None

    def set_reply(self, patient_id, reply_text, risk_tendency="medium_risk", **extra):
        conn = get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO preset_replies (patient_id, reply_text, risk_tendency)
            VALUES (?, ?, ?)
        """, (patient_id, reply_text, risk_tendency))
        conn.commit()
        conn.close()

    # -------- 统计 --------
    def get_stats(self):
        conn = get_connection()
        total = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        skip = conn.execute("SELECT COUNT(*) FROM patients WHERE skip_follow_up=1").fetchone()[0]
        # 高风险: 历史中最新 NRS >= 7 的患者数
        high = conn.execute("""
            SELECT COUNT(DISTINCT p.patient_id) FROM patients p
            JOIN followup_history h ON p.patient_id = h.patient_id
            WHERE h.nrs_score >= 7
            AND h.date = (SELECT MAX(date) FROM followup_history WHERE patient_id=p.patient_id)
        """).fetchone()[0]
        # 中风险: NRS 5~6
        medium = conn.execute("""
            SELECT COUNT(DISTINCT p.patient_id) FROM patients p
            JOIN followup_history h ON p.patient_id = h.patient_id
            WHERE h.nrs_score BETWEEN 5 AND 6
            AND h.date = (SELECT MAX(date) FROM followup_history WHERE patient_id=p.patient_id)
        """).fetchone()[0]
        low = total - skip - high - medium
        conn.close()
        return {
            "total_patients": total,
            "skip_count": skip,
            "send_count": total - skip,
            "sent_count": 0,
            "replied_count": 0,
            "high_risk_count": max(0, high),
            "medium_risk_count": max(0, medium),
            "low_risk_count": max(0, low),
            "alert_count": 0,
        }

    def close(self):
        pass  # SQLite 连接每次操作后自动关闭


def _row_to_dict(row):
    """将 sqlite3.Row 转为 dict，并解析 JSON 字段"""
    d = dict(row)
    if "follow_up_plan" in d and isinstance(d["follow_up_plan"], str):
        try:
            d["follow_up_plan"] = json.loads(d["follow_up_plan"])
        except (json.JSONDecodeError, TypeError):
            pass
    d["skip_follow_up"] = bool(d.get("skip_follow_up", 0))
    d["medication_taken"] = bool(d.get("medication_taken", 0))
    return d


# ======================================================================
# 命令行入口：手动初始化数据库
# ======================================================================
if __name__ == "__main__":
    path = init_db(force=True)
    print(f"SQLite 数据库已初始化: {path}")

    db = PatientDB()
    stats = db.get_stats()
    print(f"患者总数: {stats['total_patients']}")
    print(f"待随访:   {stats['send_count']}")
    print(f"免随访:   {stats['skip_count']}")
    print(f"高风险:   {stats['high_risk_count']}")
    print(f"中风险:   {stats['medium_risk_count']}")
    print(f"低风险:   {stats['low_risk_count']}")

    replies = db.get_all_replies()
    print(f"预置回复: {len(replies)} 条")
