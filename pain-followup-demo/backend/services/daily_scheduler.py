"""
B 号 · 当日随访判定（非 Agent，纯规则引擎判定薄封装）

职责：对 engine/followup_scheduler.is_followup_due 做轻量封装，
提供单患者判定和批量判定两个接口。判定逻辑 100% 规则驱动，
无 LLM 依赖。

对外接口：
  - decide_today(patient, plans) → dict: 单例判定
  - decide_today_list(patients, plans) → list[dict]: 批量判定
"""
from engine.followup_scheduler import is_followup_due


async def decide_today(patient, plan=None, today=None):
    """单患者判定：调用规则引擎 is_followup_due。

    Returns:
        dict: {need_followup, reason, matched_rule, source: "rule"}
    """
    result = is_followup_due(patient, today=today)
    result["source"] = "rule"
    return result


async def decide_today_list(patients, today=None):
    """批量判定，返回结构同引擎 build_today_send_list。"""
    from engine.followup_scheduler import build_today_send_list
    return build_today_send_list(patients, today=today)
