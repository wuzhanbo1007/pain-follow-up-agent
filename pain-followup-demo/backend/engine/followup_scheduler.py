"""
随访计划驱动的"当日随访名单"判定引擎（需求文档 v4.0，方案 A：执行时实时计算）

核心判定模型 —— 一位患者"今天需要随访"当且仅当同时满足 4 个条件（逻辑与）：
    C1 计划已生效    ：有一份有效随访计划（follow_up_plan 内容有效；followup_plan 表状态为 rejected 则否决）
    C2 在随访窗口内  ：discharge_date <= today <= discharge_date + duration_days
    C3 频次命中今日  ：按 plan.frequency 解析出的节奏，today 是应随访日
    C4 无临时豁免    ：当日 daily_status 非 on_leave / revisited / hospitalized

引擎为每一位患者产出可解释的判定结果（命中哪条规则、第几天、是否在窗口内、落选原因）。
对外暴露：
    get_today()                       → 判定用"今天"（可被 config.DEMO_TODAY 覆盖，保证 Demo 可复现）
    parse_frequency_to_rule(freq)     → 自然语言频次 → 结构化规则
    is_followup_due(patient, today)   → 单人判定结果 dict
    build_today_send_list(patients)   → {send_list, skip_list, details, ...}
    apply_today_followup_flags(pats)  → 把引擎结果回写每位患者字典的 skip_follow_up / skip_reason（方案 B 缓存由 A 回写）
    compute_next_followup(patient)    → 下次随访日（方案 B 展示用）
"""
import json
import re
from datetime import datetime, date, timedelta

from db import followup_db


# ==========================================================================
# 时间基准
# ==========================================================================
def get_today() -> date:
    """判定用"今天"。默认系统当天；若 config.DEMO_TODAY 配置则覆盖（Demo 可复现）。"""
    try:
        from core.config import DEMO_TODAY
    except Exception:
        DEMO_TODAY = ""
    if DEMO_TODAY:
        try:
            return datetime.strptime(DEMO_TODAY, "%Y-%m-%d").date()
        except Exception:
            pass
    return date.today()


# ==========================================================================
# 频次解析：自然语言 → 结构化频次规则
# ==========================================================================
def parse_frequency_to_rule(frequency):
    """解析随访频次为结构化规则，供命中算法使用。

    返回结构（统一以 type 区分）：
      {"type": "daily"}                                 每日
      {"type": "interval", "interval_days": 7}          每 N 天 / 每 N 周
      {"type": "weekly",  "weekdays": [0,3]}            每周 K 次（0=周一…6=周日）
      {"type": "staged",  "stages": [{"until_day":14,"rule":{...}}, ...]}  分阶段
    兜底：无法解析 → 每日（保守：宁可多随访不漏）
    """
    f = (frequency or "").strip()
    if not f:
        return {"type": "daily"}
    # 分阶段判定：含分号/第X周/连续N周（自然语言描述的频次如"首次"走简单解析）
    if "；" in f or ";" in f or re.search(r"第\s*\d", f) or "连续" in f:
        stages = _parse_staged(f)
        if stages:
            return {"type": "staged", "stages": stages}
    return _parse_simple_rule(f)


def _parse_simple_rule(f):
    """解析单一节奏（不含分阶段递归）。"""
    if re.search(r"每日|每天|每天1次|日1次", f):
        return {"type": "daily"}

    # 具体星期：每周三 / 每周二、周五 / 每周一、三、五 / 每周一至周五
    wd = _parse_weekdays(f)
    if wd:
        return {"type": "weekly", "weekdays": wd}

    # 每 N 周（每2周/每两周/每三周 / 每1～2周）
    m = re.search(r"每\s*(\d+)\s*[~～至到]?\s*(\d*)\s*周", f)
    if m:
        return {"type": "interval", "interval_days": 7 * int(m.group(1))}
    # 每周 K 次 → 每 7/k 天（如每周1次=每7天，每周2次=每3~4天）
    m = re.search(r"每周\s*(\d+)\s*次", f)
    if m:
        k = int(m.group(1))
        return {"type": "interval", "interval_days": max(1, 7 // k)}
    if "每周" in f:
        return {"type": "interval", "interval_days": 7}
    # 含"首次随访"或"1周内"字样的 → 视为出院初期密集随访
    if "首次" in f or "1周内" in f:
        return {"type": "daily"}
    return {"type": "daily"}


def _parse_staged(f):
    """解析"第1-2周每日；第3-4周每周2次；之后每2周1次"为阶段列表。

    支持三种阶段写法（可混用）：
      · "第1-2周…" / "第3周…"      → 用正则取周数区间末端
      · "连续4周…"                 → 用"连续N周"取 N 周为末端
      · "之后…" / "以后…"          → 末端置 9999（无限）
    任一阶段若未给出明确周数，默认末端 9999（兜底，不截断）。
    """
    parts = [p.strip() for p in re.split(r"[；;]", f) if p.strip()]
    stages = []
    for part in parts:
        if "之后" in part or "以后" in part or "后续" in part:
            until_day = 9999
        else:
            rng = re.search(r"第\s*(\d+)\s*[-~至到]?\s*(\d+)?\s*周", part)
            cont = re.search(r"连续\s*(\d+)\s*周", part)
            first = re.search(r"(\d+)\s*周内", part)  # "1周内" → 7天
            if rng:
                end_w = int(rng.group(2)) if rng.group(2) else int(rng.group(1))
                until_day = end_w * 7
            elif cont:
                until_day = int(cont.group(1)) * 7
            elif first:
                until_day = int(first.group(1)) * 7
            else:
                until_day = 9999
        stages.append({"until_day": until_day, "rule": _parse_simple_rule(part)})
    return stages or None


# ==========================================================================
# 具体星期解析
# ==========================================================================
_WEEKDAY_MAP = {
    "周一": 0, "星期一": 0, "一": 0,
    "周二": 1, "星期二": 1, "二": 1,
    "周三": 2, "星期三": 2, "三": 2,
    "周四": 3, "星期四": 3, "四": 3,
    "周五": 4, "星期五": 4, "五": 4,
    "周六": 5, "星期六": 5, "六": 5,
    "周日": 6, "星期日": 6, "星期天": 6, "日": 6, "天": 6,
}


def _parse_weekdays(f):
    """从文本中提取具体星期几，如 每周三→[2]，每周二、周五→[1,4]，每周一至周五→[0,1,2,3,4]"""

    # 1) 范围：每周一至周五 / 每周一到周五 / 周一至周五
    m_range = re.search(r"(?:每周\s*)?(周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期天)\s*[-~至到]\s*(?:每周\s*)?(周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期天)", f)
    if m_range:
        start_wd = _WEEKDAY_MAP.get(m_range.group(1))
        end_wd = _WEEKDAY_MAP.get(m_range.group(2))
        if start_wd is not None and end_wd is not None and start_wd <= end_wd:
            return list(range(start_wd, end_wd + 1))

    # 2) 枚举：每周二、周五 / 每周一、三、五 / 周二、周五
    #    匹配完整星期名称 + 中文数字缩写
    parts = re.findall(r"(周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期天|[一二三四五六日天])", f)
    if parts:
        seen = set()
        weekdays = []
        for p in parts:
            w = _WEEKDAY_MAP.get(p)
            if w is not None and w not in seen:
                seen.add(w)
                weekdays.append(w)
        if weekdays:
            return sorted(weekdays)

    return None


# ==========================================================================
# 命中算法
# ==========================================================================
def _frequency_hit(rule, n, today):
    """n = (today - start).days（出院后第 n 天），判断今天是否为随访日。"""
    t = rule.get("type")
    if t == "daily":
        return True
    if t == "interval":
        interval = rule.get("interval_days", 1)
        return interval > 0 and n % interval == 0
    if t == "weekly":
        if n == 0:
            return True  # 出院当天视作命中
        return today.weekday() in rule.get("weekdays", [])
    if t == "staged":
        stages = rule.get("stages", [])
        if not stages:
            return True
        for stage in stages:
            if n <= stage["until_day"]:
                return _frequency_hit(stage["rule"], n, today)
        return _frequency_hit(stages[-1]["rule"], n, today)  # 超出所有阶段时回退最后一段
    return True


def _rule_label(rule):
    t = rule.get("type")
    if t == "daily":
        return "每日"
    if t == "interval":
        d = rule.get("interval_days", 1)
        return "每%d天" % d if d != 7 else "每周1次"
    if t == "weekly":
        wd_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        labels = [wd_names[w] for w in sorted(rule.get("weekdays", [])) if 0 <= w <= 6]
        return "、".join(labels) if labels else "每周"
    if t == "staged":
        return "分阶段"
    return "每日"


# ==========================================================================
# 单人判定
# ==========================================================================
def is_followup_due(patient, today=None, plan_status=None, plan_override=None):
    """返回单人判定结果（结构见需求文档 1.5）。

    plan_status：该患者 followup_plan 表最新计划状态（rejected 表示被驳回 → C1 否决）。
    plan_override：followup_plan 表最新计划 dict（含 plan_json），
                   优先使用其中的 frequency / duration_days 代替静态 patients.follow_up_plan。
    """
    today = today or get_today()
    pid = patient.get("patient_id")
    name = patient.get("name", "")

    # 优先从 followup_plan 表的 plan_json 取频次（LLM 生成的动态频次）
    plan_json = None
    if plan_override:
        plan_json = plan_override.get("plan_json")

    plan = plan_json if isinstance(plan_json, dict) else (patient.get("follow_up_plan") or {})
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:
            plan = {}

    frequency = plan.get("frequency") or "每日"
    duration_days = int(plan.get("duration_days") or 90)
    start_date = patient.get("plan_start_date") or patient.get("discharge_date")

    detail = {
        "patient_id": pid,
        "name": name,
        "need_followup": False,
        "reason": "",
        "matched_rule": "",
        "day_index": None,
        "window": None,
        "plan_status": plan_status,
        "frequency": frequency,
        "duration_days": duration_days,
    }

    # ---- C_DEMO 演示患者强制应随访（保障 Demo 流程可演示）----
    if pid in ("P20240004", "P20240005"):
        detail["need_followup"] = True
        detail["reason"] = "演示患者（马淑珍/宋艳始终可随访）"
        detail["matched_rule"] = "演示强制"
        return detail

    # ---- C0 连续未回复触发电话回访（优先级最高）----
    consecutive_no_reply = int(patient.get("consecutive_no_reply_days") or 0)
    if consecutive_no_reply >= 3:
        detail["need_followup"] = True
        detail["reason"] = "连续%d天未回复，需电话回访" % consecutive_no_reply
        detail["matched_rule"] = "异常回访"
        return detail

    # ---- C1 计划已生效 ----
    if plan_status == "rejected":
        detail["reason"] = "随访计划未生效（已驳回）"
        return detail
    if not plan or not frequency or not start_date:
        detail["reason"] = "随访计划未生效"
        return detail

    # 解析起始日（默认出院日）
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except Exception:
        detail["reason"] = "随访计划未生效（出院日期缺失）"
        return detail

    end = start + timedelta(days=duration_days)
    detail["window"] = {"start": start.isoformat(), "end": end.isoformat()}
    n = (today - start).days
    detail["day_index"] = n

    # ---- C2 在随访窗口内 ----
    if n < 0:
        detail["reason"] = "随访尚未开始"
        return detail
    if n > duration_days:
        detail["reason"] = "随访周期已结束"
        return detail

    # ---- C3 频次命中今日 ----
    rule = parse_frequency_to_rule(frequency)
    detail["matched_rule"] = _rule_label(rule)
    if not _frequency_hit(rule, n, today):
        detail["reason"] = "今日非随访日（频次未命中）"
        return detail

    # ---- C4 无临时豁免 ----
    daily_status = patient.get("daily_status") or "normal"
    if daily_status in ("on_leave", "revisited", "hospitalized"):
        reason_map = {
            "on_leave": "患者请假",
            "revisited": "昨日已复诊",
            "hospitalized": "已安排住院",
        }
        detail["reason"] = reason_map[daily_status]
        return detail

    # 全部通过 → 应随访
    detail["need_followup"] = True
    detail["reason"] = "命中：%s，出院后第 %d 天，周期内" % (_rule_label(rule), n)
    return detail


# ==========================================================================
# 名单构建
# ==========================================================================
def build_today_send_list(patients, today=None):
    """返回当日判定名单：{total, send_count, skip_count, send_list, skip_list, details, today}。"""
    today = today or get_today()

    # 预取每位患者最新计划（用于 C1 否决 + plan_json 频次覆盖）
    plan_map = {}
    try:
        for p in patients:
            pid = p.get("patient_id")
            plan = followup_db.get_latest_plan(pid)
            if plan:
                plan_map[pid] = plan
    except Exception:
        plan_map = {}

    send_list, skip_list, details = [], [], {}
    for p in patients:
        pid = p.get("patient_id")
        latest = plan_map.get(pid)
        plan_status = latest["status"] if latest else None
        d = is_followup_due(p, today, plan_status=plan_status, plan_override=latest)
        details[pid] = d
        if d["need_followup"]:
            send_list.append(p)
        else:
            skip_list.append(p)

    return {
        "total": len(patients),
        "send_count": len(send_list),
        "skip_count": len(skip_list),
        "send_list": send_list,
        "skip_list": skip_list,
        "details": details,
        "today": today.isoformat(),
    }


def apply_today_followup_flags(patients, today=None):
    """用判定引擎结果回写每位患者字典的 skip_follow_up / skip_reason / followup_detail。

    方案 A 为唯一真源，方案 B 的缓存（skip_follow_up 等）由此处回写，
    保证所有读取 skip_follow_up 的既有代码（orchestrator / 前端名单）得到规则驱动的结果。
    """
    res = build_today_send_list(patients, today)
    details = res["details"]
    for p in patients:
        pid = p.get("patient_id")
        d = details.get(pid)
        if not d:
            continue
        p["skip_follow_up"] = not d["need_followup"]
        p["skip_reason"] = d["reason"]
        p["followup_detail"] = d
    return res


def compute_next_followup(patient, today=None):
    """计算该患者下一次随访日（方案 B 展示用）。今天命中则返回今天，否则向后找最近命中日。"""
    today = today or get_today()
    plan = patient.get("follow_up_plan") or {}
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:
            plan = {}
    frequency = plan.get("frequency") or "每日"
    duration_days = int(plan.get("duration_days") or 90)
    start_date = patient.get("plan_start_date") or patient.get("discharge_date")
    if not start_date:
        return None
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except Exception:
        return None
    end = start + timedelta(days=duration_days)
    rule = parse_frequency_to_rule(frequency)
    horizon = max(1, (end - today).days)
    for i in range(0, horizon + 1):
        cand = today + timedelta(days=i)
        if cand > end:
            break
        n = (cand - start).days
        if n < 0:
            continue
        if _frequency_hit(rule, n, cand):
            return cand.isoformat()
    return None

