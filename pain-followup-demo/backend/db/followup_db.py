"""
随访计划 / 会话 / 审阅 数据访问层（v3.0 新增表）
对应需求文档：二(2.3) followup_plan、三(3.3) followup_session/followup_review

原则：
  - 既有患者数据、预置回复**只读不写**（沿用 v2.0 约束）；
  - 本文件操作的是新增的 3 张表，属于新能力，可写。
  - 所有审阅/修改均落 audit_snapshot，满足医疗审计（风险 4）。
"""
import json
import sqlite3
import uuid
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row):
    d = dict(row)
    # JSON 字段解析
    for key in ("plan_json", "guideline_citations", "transcript_json",
                "risk_result", "audit_snapshot", "original_snapshot"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ======================================================================
# followup_plan
# ======================================================================
def create_plan(patient_id, plan_json, discharge_summary=None,
               citations=None, doctor_id=None, status="draft"):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    plan_id = f"PLAN-{uuid.uuid4().hex[:12].upper()}"
    now = _now()
    conn.execute(
        """INSERT INTO followup_plan
           (plan_id, patient_id, discharge_summary, plan_json, guideline_citations, status, doctor_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, patient_id, discharge_summary,
         json.dumps(plan_json, ensure_ascii=False),
         json.dumps(citations or [], ensure_ascii=False),
         status, doctor_id, now),
    )
    conn.commit()
    conn.close()
    return plan_id


def get_plan(plan_id):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM followup_plan WHERE plan_id=?", (plan_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def get_latest_plan(patient_id):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM followup_plan WHERE patient_id=? ORDER BY created_at DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def list_plans_by_doctor(doctor_id):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM followup_plan WHERE doctor_id=? ORDER BY created_at DESC",
        (doctor_id,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def list_all_latest_plans():
    """每位患者最新一条随访计划（按 patient_id 去重），供前端 initial load 用。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM followup_plan
        WHERE plan_id IN (
            SELECT plan_id FROM (
                SELECT plan_id,
                       ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY created_at DESC) rn
                FROM followup_plan
            ) WHERE rn = 1
        )
        ORDER BY patient_id
    """).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def approve_plan(plan_id, doctor_id=None):
    """医生同意：状态→approved"""
    return _update_plan_status(plan_id, "approved", doctor_id)


def modify_plan(plan_id, plan_json, doctor_id=None):
    """医生修改后确认：保留原文快照，状态→modified"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    old = conn.execute("SELECT plan_json FROM followup_plan WHERE plan_id=?", (plan_id,)).fetchone()
    snapshot = old["plan_json"] if old else None
    now = _now()
    conn.execute(
        """UPDATE followup_plan
           SET plan_json=?, status='modified', doctor_id=?,
               original_snapshot=?, reviewed_at=?
           WHERE plan_id=?""",
        (json.dumps(plan_json, ensure_ascii=False), doctor_id,
         snapshot, now, plan_id),
    )
    conn.commit()
    conn.close()
    return True


def batch_approve_plans(doctor_id=None, only_draft=True):
    """批量同意：将待审阅（status='draft'）或全部未决的随访计划一次性置为 approved。

    用于「批量同意」按钮：避免医生逐一点击。
    返回被更新的计划数量（**按患者去重后的患者数**）与 plan_id 列表。

    关键：随访计划可能因人反复生成而产生「同一患者的多份计划」（重复行），
    因此必须按 patient_id 去重——每位患者只取最新一条计划来同意，
    否则批量同意人数会远超患者总数（如 666 条计划被数成 666 人）。
    “修改（modified）”视为该患者计划的一部分，计入其 1 个名额，不额外加算。
    - only_draft=True：同意每位患者最新一条 status IN ('draft','modified') 的计划
    - only_draft=False：把 draft/modified 全部置为 approved（同样按患者去重）。
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    if only_draft:
        # 每位患者只取最新一条 draft/modified 计划（去重），避免重复生成导致多份被重复计数
        rows = conn.execute(
            """SELECT plan_id FROM (
                   SELECT plan_id,
                          ROW_NUMBER() OVER (
                              PARTITION BY patient_id ORDER BY created_at DESC
                          ) rn
                   FROM followup_plan
                   WHERE status IN ('draft', 'modified')
               ) WHERE rn = 1"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT plan_id FROM (
                   SELECT plan_id,
                          ROW_NUMBER() OVER (
                              PARTITION BY patient_id ORDER BY created_at DESC
                          ) rn
                   FROM followup_plan
                   WHERE status IN ('draft', 'modified')
               ) WHERE rn = 1"""
        ).fetchall()
    plan_ids = [r["plan_id"] for r in rows]
    now = _now()
    for pid in plan_ids:
        conn.execute(
            "UPDATE followup_plan SET status='approved', doctor_id=?, reviewed_at=? WHERE plan_id=?",
            (doctor_id, now, pid),
        )
    conn.commit()
    conn.close()
    return {"approved": len(plan_ids), "plan_ids": plan_ids}


def _update_plan_status(plan_id, status, doctor_id=None):
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "UPDATE followup_plan SET status=?, doctor_id=?, reviewed_at=? WHERE plan_id=?",
        (status, doctor_id, _now(), plan_id),
    )
    conn.commit()
    conn.close()
    return True


# ======================================================================
# followup_session
# ======================================================================
def create_session(patient_id, transcript_json, risk_result=None,
                  agent_summary=None, plan_id=None):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    session_id = f"SESS-{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO followup_session
           (session_id, patient_id, plan_id, transcript_json, risk_result, agent_summary, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, patient_id, plan_id,
         json.dumps(transcript_json, ensure_ascii=False),
         json.dumps(risk_result or {}, ensure_ascii=False),
         agent_summary, _now()),
    )
    conn.commit()
    conn.close()
    return session_id


def get_today_session(patient_id):
    """返回该患者今天已有的随访会话（若有），用于去重，避免重复生成记录。"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM followup_session WHERE patient_id=? AND created_at LIKE ? "
        "ORDER BY created_at DESC LIMIT 1",
        (patient_id, today + "%"),
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def update_session(session_id, transcript_json=None, risk_result=None,
                   agent_summary=None):
    """更新已有会话的对话/风险/摘要（同日去重时复用同一条记录）。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """UPDATE followup_session SET
              transcript_json=COALESCE(?, transcript_json),
              risk_result=COALESCE(?, risk_result),
              agent_summary=COALESCE(?, agent_summary)
           WHERE session_id=?""",
        (json.dumps(transcript_json, ensure_ascii=False) if transcript_json is not None else None,
         json.dumps(risk_result or {}, ensure_ascii=False) if risk_result is not None else None,
         agent_summary, session_id),
    )
    conn.commit()
    conn.close()


def get_session(session_id):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM followup_session WHERE session_id=?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = _row_to_dict(row)
    d["ai_review"] = get_review_ai(session_id)
    return d


def get_review_ai(session_id):
    """从 review 行的 audit_snapshot 取出 AI（D 号 Agent）审阅意见。

    同一会话可能存在多条 review（随访自动生成 + 医生手动提交），
    故倒序遍历，返回首个含 ai_review 的行，确保 AI 意见不丢失。
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT audit_snapshot FROM followup_review WHERE session_id=? ORDER BY reviewed_at DESC",
        (session_id,),
    ).fetchall()
    conn.close()
    for row in rows:
        if not row["audit_snapshot"]:
            continue
        try:
            snap = json.loads(row["audit_snapshot"])
        except (json.JSONDecodeError, TypeError):
            continue
        ai = snap.get("ai_review")
        if ai:
            return ai
    return None


def list_sessions_by_doctor(doctor_id, patient_id=None):
    """医生名下待审阅会话（通过患者 doctor_id 关联）"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    if patient_id:
        rows = conn.execute(
            "SELECT * FROM followup_session WHERE patient_id=? ORDER BY created_at DESC",
            (patient_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT s.* FROM followup_session s
               JOIN patients p ON s.patient_id = p.patient_id
               WHERE p.doctor_id = ?
               ORDER BY s.created_at DESC""",
            (doctor_id,),
        ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_latest_transcripts():
    """返回每个患者最新一次随访会话的 transcript（dict: patient_id -> [{role, content}])。

    供微信对话页（ChatPage）刷新/重启后还原历史对话。
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT patient_id, transcript_json, created_at FROM followup_session ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        pid = r["patient_id"]
        if pid in result:
            continue  # 已保留该患者最新一条
        try:
            t = json.loads(r["transcript_json"]) if r["transcript_json"] else []
        except Exception:
            t = []
        result[pid] = t
    return result


# ======================================================================
# followup_review
# ======================================================================
def create_review(session_id, patient_id, doctor_score=None,
                  doctor_comment=None, track_status="pending_track",
                  reviewer_id=None, audit_snapshot=None):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    review_id = f"REV-{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        """INSERT INTO followup_review
           (review_id, session_id, patient_id, doctor_score, doctor_comment,
            track_status, reviewer_id, reviewed_at, audit_snapshot)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (review_id, session_id, patient_id, doctor_score, doctor_comment,
         track_status, reviewer_id, _now(),
         json.dumps(audit_snapshot or {}, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return review_id


def get_review_by_session(session_id):
    """返回该会话已有的审阅行（用于避免重复生成 AI 审阅）。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM followup_review WHERE session_id=?", (session_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def attach_ai_review(review_id, ai_review):
    """把 D 号 Agent 的结构化审阅结果合并进 review 行的 audit_snapshot（兼容式更新，不改表结构）。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT audit_snapshot FROM followup_review WHERE review_id=?", (review_id,)).fetchone()
    snap = {}
    if row and row["audit_snapshot"]:
        try:
            snap = json.loads(row["audit_snapshot"])
        except (json.JSONDecodeError, TypeError):
            snap = {}
    snap["ai_review"] = ai_review
    conn.execute(
        "UPDATE followup_review SET audit_snapshot=? WHERE review_id=?",
        (json.dumps(snap, ensure_ascii=False), review_id),
    )
    conn.commit()
    conn.close()
    return True


def list_reviews(status=None, patient_id=None):
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM followup_review WHERE 1=1"
    params = []
    if status:
        sql += " AND track_status=?"
        params.append(status)
    if patient_id:
        sql += " AND patient_id=?"
        params.append(patient_id)
    sql += " ORDER BY reviewed_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_latest_sessions(today=None):
    """返回每个患者最新一次随访会话（按 created_at DESC 去重），并附带该会话的审阅信息（若有）。

    供 Review 面板展示「最新内容」：重复随访后不再一直显示最早那一次。
    today: 若传入（格式 YYYY-MM-DD），只返回当天创建的会话。
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    sql = """SELECT s.*,
                  r.review_id, r.doctor_score, r.doctor_comment,
                  r.track_status, r.reviewed_at AS review_time
           FROM followup_session s
           LEFT JOIN followup_review r ON r.session_id = s.session_id
           WHERE 1=1"""
    params = []
    if today:
        sql += " AND date(s.created_at) = ?"
        params.append(today)
    sql += " ORDER BY s.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    result = {}
    for r in rows:
        d = _row_to_dict(r)
        pid = d["patient_id"]
        if pid in result:
            continue  # 已保留该患者最新一条
        d["ai_review"] = get_review_ai(d["session_id"])
        result[pid] = d
    return list(result.values())


def review_stats(today=None):
    """统计基于每个患者最新一次随访会话的审阅，避免被重跑覆盖的旧审阅仍被计入。

    与 /api/reviews/latest（列表只展示每人最新会话）口径保持一致：
    列表里看不到的（旧会话上的）审阅，统计也不再计数。
    today: 若传入（格式 YYYY-MM-DD），只统计当天创建的会话。
    """
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    # 1. 取每个患者最新一次会话的 session_id
    sql = "SELECT session_id, patient_id FROM followup_session"
    params = []
    if today:
        sql += " WHERE date(created_at) = ?"
        params.append(today)
    sql += " ORDER BY created_at DESC"
    latest_rows = conn.execute(sql, params).fetchall()
    latest_ids = []
    seen = set()
    for r in latest_rows:
        pid = r["patient_id"]
        if pid in seen:
            continue
        seen.add(pid)
        latest_ids.append(r["session_id"])

    if latest_ids:
        placeholders = ",".join("?" for _ in latest_ids)
        total = conn.execute(
            f"SELECT COUNT(*) FROM followup_review WHERE session_id IN ({placeholders})",
            latest_ids).fetchone()[0]
        pending = conn.execute(
            f"SELECT COUNT(*) FROM followup_review WHERE session_id IN ({placeholders}) AND track_status='pending_track'",
            latest_ids).fetchone()[0]
        tracking = conn.execute(
            f"SELECT COUNT(*) FROM followup_review WHERE session_id IN ({placeholders}) AND track_status='tracking'",
            latest_ids).fetchone()[0]
        resolved = conn.execute(
            f"SELECT COUNT(*) FROM followup_review WHERE session_id IN ({placeholders}) AND track_status='resolved'",
            latest_ids).fetchone()[0]
        need_revisit = conn.execute(
            f"SELECT COUNT(*) FROM followup_review WHERE session_id IN ({placeholders}) AND track_status='need_revisit'",
            latest_ids).fetchone()[0]
        avg_row = conn.execute(
            f"SELECT AVG(doctor_score) FROM followup_review WHERE session_id IN ({placeholders}) AND doctor_score IS NOT NULL",
            latest_ids).fetchone()[0]
    else:
        total = pending = tracking = resolved = need_revisit = 0
        avg_row = None
    conn.close()
    return {
        "total": total,
        "pending_track": pending,
        "tracking": tracking,
        "resolved": resolved,
        "need_revisit": need_revisit,
        "avg_score": round(avg_row, 2) if avg_row else None,
    }


# 复用既有的 DB 路径（与 data/database.py 一致）
def _db_path():
    from data.database import DB_PATH
    return DB_PATH
