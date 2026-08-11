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
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _to_num(s):
    """阿拉伯/中文数字字符串 → int；解析失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    return None


def _normalize_freq(f):
    """规范化频次文本：全角数字→半角、去引用标记/括号噪声、合并空白。提升对任意新写法的适配性。"""
    if not f:
        return ""
    f = re.sub(r"[０-９]", lambda m: str(ord(m.group()) - 0xFEE0), f)
    f = re.sub(r"[\[\]【】()（）]", " ", f)
    f = re.sub(r"\s+", " ", f).strip()
    return f


def parse_frequency_to_rule(frequency):
    """解析随访频次为结构化规则，供命中算法使用。

    返回结构（统一以 type 区分）：
      {"type": "daily"}                                 每日
      {"type": "interval", "interval_days": 7}          每 N 天 / 每 N 周 / 每 N 个月(30天)
      {"type": "once",    "day": 30}                    一次性（第 N 天随访一次）
      {"type": "weekly",  "weekdays": [0,3]}            每周 K 次（0=周一…6=周日）
      {"type": "staged",  "stages": [{"until_day":14,"rule":{...}}, ...]}  分阶段

    健壮性：任何文本都返回一个合法规则，绝不抛异常；无法解析 → 每日（保守：宁可多随访不漏）。
    """
    f = _normalize_freq(frequency)
    if not f:
        return {"type": "daily"}
    # 分阶段判定：含分号 / 第X周或月 / 连续N周 / 之后以后 / 稳定期（复合频次大多走这里）
    if ("；" in f or ";" in f or re.search(r"第\s*\d", f) or "连续" in f
            or "之后" in f or "以后" in f or "稳定" in f):
        try:
            stages = _parse_staged(f)
            if stages:
                return {"type": "staged", "stages": stages}
        except Exception:
            pass  # 分阶段解析异常 → 退回简单解析
    return _parse_simple_rule(f)


def _parse_simple_rule(f):
    """解析单一节奏（不含分阶段递归）。

    顺序很重要：
      ① 每周 K 次（每周一次/两次/1次/2次）→ 每 7/k 天 —— 必须在星期解析前，避免"每周一"被误读
      ② 具体星期（每周三/每周二、周五/周一至周五）
      ③ 每 N 周 / 每两周 → 7N 天
      ④ 每 N 个月 / 每月 → 30N 天
      ⑤ 每 N 天 → N 天
      ⑥ 首次 / 1周内 → 每日（出院初期密集随访兜底）
    """
    if re.search(r"每日|每天|每天1次|日1次", f):
        return {"type": "daily"}

    # ① 每周 K 次：每周一次/两次/三次/1次/2次 → interval 7//k
    m = re.search(r"每周\s*(一|二|三|两|\d+)\s*次", f)
    if m:
        k = _to_num(m.group(1)) or 1
        return {"type": "interval", "interval_days": max(1, 7 // k)}

    # ①b 每 N 周 …次：每4周一次/每2周一次 → interval 7N（防"周一次"的"周+一"被误当周一）
    m = re.search(r"每\s*(\d+|两|二|三|四|五|六|七|八|九|十)\s*周\s*(?:一|两|\d+)?\s*次", f)
    if m:
        n = _to_num(m.group(1))
        if n:
            return {"type": "interval", "interval_days": 7 * n}

    # ② 具体星期
    wd = _parse_weekdays(f)
    if wd:
        return {"type": "weekly", "weekdays": wd}

    # ③ 每 N 周 / 每两周 / 每1～2周
    m = re.search(r"每\s*(\d+|两|二|三|四|五|六|七|八|九|十)\s*[~～至到]?\s*(\d*)\s*周", f)
    if m:
        n = _to_num(m.group(1))
        if n:
            return {"type": "interval", "interval_days": 7 * n}
    if "每周" in f:
        return {"type": "interval", "interval_days": 7}

    # ④ 每 N 个月 / 每月 / 每1～2个月 → 30N 天
    m = re.search(r"每\s*(\d+|两|二|三|四|五|六|七|八|九|十)\s*[~～至到]?\s*(\d*)\s*个月?", f)
    if m:
        n = _to_num(m.group(1))
        if n:
            return {"type": "interval", "interval_days": 30 * n}
    if "每月" in f:
        return {"type": "interval", "interval_days": 30}

    # ⑤ 每 N 天
    m = re.search(r"每\s*(\d+)\s*天", f)
    if m:
        return {"type": "interval", "interval_days": int(m.group(1))}

    # ⑥ "1周内" → 出院初期密集随访（每日，宁可多随访不漏）
    if "1周内" in f:
        return {"type": "daily"}
    # ⑦ 首次/复诊/复查 + 明确时间点 → 一次性事件（只在指定当天随访一次）
    if "首次" in f or "复诊" in f or "复查" in f:
        m = re.search(r"(?<!每)(\d+)\s*个月", f)
        if m:
            return {"type": "once", "day": int(m.group(1)) * 30}
        m = re.search(r"第\s*(\d+)\s*周", f)
        if m:
            return {"type": "once", "day": int(m.group(1)) * 7}
        m = re.search(r"(?<!每)(\d+)\s*周", f)
        if m:
            return {"type": "once", "day": int(m.group(1)) * 7}
        m = re.search(r"(?<!每)(\d+)\s*天", f)
        if m:
            return {"type": "once", "day": int(m.group(1))}
        # 无明确时间点 → 不在此下结论，继续走 ⑧⑨ / 每日兜底
        # （如"每半年复查"应走 ⑨ → 180天；"按需复诊"走到底 → 每日）
    # ⑧ 隔天/隔日 → 每2天
    if re.search(r"隔\s*(?:天|日)", f):
        return {"type": "interval", "interval_days": 2}
    # ⑨ 通用"每 N 单位"兜底提取（年/半年/季/月/周/天/日），适配未预见的写法
    if "半年" in f:
        return {"type": "interval", "interval_days": 180}
    if "季度" in f or "每季" in f:
        return {"type": "interval", "interval_days": 90}
    m = re.search(r"每\s*(一|两|二|三|四|五|六|七|八|九|十|\d+)?\s*(年|个月|月|周|天|日)", f)
    if m:
        n = _to_num(m.group(1)) or 1
        unit = m.group(2)
        days = {"年": 365, "个月": 30, "月": 30, "周": 7, "天": 1, "日": 1}
        return {"type": "interval", "interval_days": days[unit] * n}
    return {"type": "daily"}


def _stage_until_day(part):
    """计算阶段末端天数（出院后第 N 天）。

    规则：
      · "之后/以后/稳定期 + 每X…"（开放节奏）→ 9999（无限）
      · "第X周 / 第X-Y周" → 7X 或 7Y
      · "第X个月 / 第X-Y个月" → 30X 或 30Y
      · "连续N周" → 7N；"N周内" → 7N
      · "出院后N个月"（非"每N个月"）→ 30N；"出院后N周" → 7N
      · 其余 → 9999（兜底，不截断）
    """
    if re.search(r"每\s*(\d+|一|两|二|三|四|五|六|七|八|九|十)\s*[~～至到]?\s*(\d*)\s*个?月", part):
        return 9999  # "每N个月/每月" 是开放节奏，无上界
    rng_w = re.search(r"第\s*(\d+)\s*[-~至到]?\s*(\d+)?\s*周", part)
    if rng_w:
        return (int(rng_w.group(2)) if rng_w.group(2) else int(rng_w.group(1))) * 7
    rng_m = re.search(r"第\s*(\d+)\s*[-~至到]?\s*(\d+)?\s*个月?", part)
    if rng_m:
        return (int(rng_m.group(2)) if rng_m.group(2) else int(rng_m.group(1))) * 30
    cont = re.search(r"连续\s*(\d+)\s*周", part)
    if cont:
        return int(cont.group(1)) * 7
    cont_m = re.search(r"连续\s*(\d+)\s*个月", part)
    if cont_m:
        return int(cont_m.group(1)) * 30
    first = re.search(r"(\d+)\s*周内", part)  # "1周内" → 7天
    if first:
        return int(first.group(1)) * 7
    m = re.search(r"(?<!每)(\d+)\s*个月", part)  # "出院后N个月"/"N个月首次"（非"每N个月"）
    if m:
        return int(m.group(1)) * 30
    w = re.search(r"(?<!每)(\d+)\s*周", part)
    if w:
        return int(w.group(1)) * 7
    return 9999


def _split_comma_stages(p):
    """在逗号处切分阶段，仅当逗号后是"新阶段起点"：
      a) "第X周/月 + 频率"（第3–8周每2周一次）
      b) 直接以频率开头（每/每周/每日/每天）

    避免拆散：
      · "每周一次，出院后第1–2周"（逗号后是"出院后"）
      · "每周一次，第3–8周"（"第X周"单独只是时间范围，不是新阶段）
    """
    out = []
    cur = p
    while True:
        m = re.search(
            r"[，,]\s*(?=(?:第[0-9一二三四五六七八九十]+[~～至到–-]?"
            r"[0-9一二三四五六七八九十]*(?:周|个?月)[^，,；;]*(?:每|每周|每日|每天))"
            r"|(?:每|每周|每日|每天))",
            cur,
        )
        if not m:
            out.append(cur.strip())
            break
        out.append(cur[:m.start()].strip())
        cur = cur[m.start() + 1:].strip()
    return [x for x in out if x]


def _parse_staged(f):
    """解析"第1-2周每日；第3-4周每周2次；之后每2周1次"为阶段列表。

    支持写法（可混用）：
      · "第1-2周…" / "第3周…" / "第2-6个月…" / "第4周门诊复诊" → 按周/月取末端
      · "连续4周…"                 → 用"连续N周"取 N 周为末端
      · "之后…" / "以后…" / "稳定期…" / "病情稳定后…" → 末端置 9999（无限）
      · 逗号后接"第X/每X" → 逗号也作为阶段边界（如"…每周2次，第3–8周…"）
    任一阶段若未给出明确边界，默认末端 9999（兜底，不截断）。
    """
    # 统一把"之后/以后"前加分号，使其成为阶段边界
    f2 = re.sub(r"(之后|以后)", r"；\g<0>", f)
    raw = [p.strip() for p in re.split(r"[；;]", f2) if p.strip()]
    parts = []
    for p in raw:
        # 段内若含"稳定期/病情稳定/稳定后"等边界词，在其前再切一刀
        m = re.search(r"(稳定期|病情稳定|症状稳定|疼痛稳定|稳定后)", p)
        segs = []
        if m and p[:m.start()].strip():
            segs.append(p[:m.start()].strip())
            segs.append(p[m.start():].strip())
        else:
            segs.append(p)
        # 每段内再按"逗号 + 新阶段起点"切分
        for seg in segs:
            parts.extend(_split_comma_stages(seg))
    stages = []
    for part in parts:
        stages.append({
            "until_day": _stage_until_day(part),
            "rule": _parse_simple_rule(part),
        })
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
    #    匹配完整星期名称 + 中文数字缩写；裸单字需排除"一次/一月/每天/第X"等计数/时间单位误抓
    parts = re.finditer(r"(周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期天|[一二三四五六日天])", f)
    seen = set()
    weekdays = []
    for m in parts:
        p = m.group(1)
        if len(p) > 1:
            w = _WEEKDAY_MAP.get(p)
        else:
            prev = f[m.start() - 1] if m.start() > 0 else ""
            nxt = f[m.end()] if m.end() < len(f) else ""
            # "一次/一月/每天/每28天/第X周/上周/隔天" 里的裸单字不是星期 → 跳过
            if (nxt in ("次", "月", "天", "周", "个", "年")
                    or prev in ("每", "第", "上", "下", "隔") or prev.isdigit()):
                continue
            w = _WEEKDAY_MAP.get(p)
        if w is not None and w not in seen:
            seen.add(w)
            weekdays.append(w)
    return sorted(weekdays) if weekdays else None

    return None


# ==========================================================================
# 命中算法
# ==========================================================================
def _frequency_hit(rule, n, today):
    """n = (today - start).days（出院后第 n 天），判断今天是否为随访日。

    健壮性：rule 缺失/未知类型 → 保守命中（宁可多随访不漏）；绝不抛异常。
    """
    rule = rule or {}
    t = rule.get("type")
    if t == "daily":
        return True
    if t == "interval":
        interval = int(rule.get("interval_days") or 1)
        return interval > 0 and n % interval == 0
    if t == "once":
        # 一次性事件（如"1个月首次复查/第4周门诊复诊"）：只在指定当天随访
        return n == int(rule.get("day") or 0)
    if t == "weekly":
        if n == 0:
            return True  # 出院当天视作命中
        return today.weekday() in (rule.get("weekdays") or [])
    if t == "staged":
        stages = rule.get("stages") or []
        if not stages:
            return True
        for stage in stages:
            if n <= int(stage.get("until_day") or 9999):
                return _frequency_hit(stage.get("rule"), n, today)
        return _frequency_hit(stages[-1].get("rule"), n, today)  # 超出所有阶段时回退最后一段
    return True  # 未知类型 → 保守命中


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

