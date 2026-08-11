"""
LLM 个性化随访消息生成器
配置从 config.py 读取
支持超时降级：LLM 超时/异常时自动回退到模板生成，不阻塞流程
"""
import time
from datetime import datetime

from core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT
from llm.client import is_llm_available
from llm.model import achat_completion
from prompts.personalized_message import (
    build_farewell_prompt,
    build_greeting_prompt,
)
from prompts.clarification_message import build_prompt as build_clarification_prompt


class MessageGenerator:
    """用 LLM 根据患者历史生成个性化随访消息"""

    def __init__(self):
        self.api_key = LLM_API_KEY
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL
        self.use_llm = is_llm_available()
        self.timeout = LLM_TIMEOUT

        # force_fallback=True 时强制走模板生成（用于 LLM 超时异常演示）
        self.force_fallback = False

        # 最近一次 LLM 调用状态，供外部查询
        self.last_llm_status = None

        # LLM 调用统一走 llm.model.achat_completion（ChatOpenAI.ainvoke，底层 AsyncOpenAI），
        # 不再持有原生 OpenAI client 单例。

    async def generate_greeting(self, patient, risk_tendency=None):
        """生成纯开场白（不含任何提问），供 Service 层在一问一答模式中使用。

        Returns:
            str: 个性化问候文本，不含问题。
        """
        risk_tendency = risk_tendency or "medium_risk"
        days_since_discharge = self._calc_days(patient["discharge_date"])
        history_summary = self._summarize_history(patient.get("history", []))
        trend = self._detect_trend(patient.get("history", []))

        if self.use_llm and not self.force_fallback:
            greeting, _ = await self._safe_llm_greeting_generate(
                patient, days_since_discharge, history_summary, risk_tendency
            )
        else:
            # 模板降级：沿用 _template_greeting
            greeting = self._template_greeting(patient, days_since_discharge, trend, risk_tendency)

        return greeting

    async def _safe_llm_greeting_generate(self, patient, days_since_discharge, history_summary, risk_tendency):
        """安全的开场白 LLM 生成，带超时降级。"""
        try:
            prompt = build_greeting_prompt(patient, days_since_discharge, history_summary, risk_tendency)
            greeting = (await achat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=200,
            )).strip()
            return greeting, {"success": True, "fallback_used": False}
        except Exception as exc:
            fallback = self._template_greeting(
                patient, days_since_discharge,
                self._detect_trend(patient.get("history", [])), risk_tendency,
            )
            return fallback, {"success": False, "fallback_used": True, "reason": str(exc)}

    def _template_greeting(self, patient, days_since_discharge, trend, risk_tendency="medium_risk"):
        """模板降级：开场问候（自我介绍-就医背景-来意），以自然问句结尾（让患者愿意回应）。"""
        name = patient["name"]
        doctor = patient.get("doctor_name") or "随访医生"
        dept = patient.get("department_name") or "疼痛科"
        hospital = patient.get("hospital_name") or "协和医院"
        diagnosis = patient.get("diagnosis") or "疼痛"
        head = (f"{name}您好！我是{hospital}{dept}的医生{doctor}。"
                f"您此前因{diagnosis}在{hospital}住院，今天按随访计划跟您沟通一下恢复情况。")
        if risk_tendency == "high_risk":
            tail = "一直惦记着您，有点担心您的情况。今天感觉怎么样呀？"
        elif risk_tendency == "low_risk":
            tail = "看到您恢复得不错，真为您高兴。今天身体感觉如何？"
        elif trend == "恶化":
            tail = "听说您这几天有些不适，很关心您。今天情况还好吗？"
        elif trend == "好转":
            tail = "看到您一天比一天好，真为您开心。今天感觉怎么样呀？"
        else:
            tail = "一直惦记着您。今天感觉怎么样呀？"
        return f"{head}{tail}"

    async def generate_farewell(self, patient, risk_level, nrs_score=None,
                          sleep_quality=None, medication_status=None,
                          risk_tendency="medium_risk"):
        """生成个性化告别语，根据患者本次随访回复定制。

        Returns:
            str: 温暖的告别语文本。
        """
        if self.use_llm and not self.force_fallback:
            farewell, _ = await self._safe_llm_farewell_generate(
                patient, risk_level, nrs_score, sleep_quality, medication_status, risk_tendency
            )
        else:
            farewell = self._template_farewell(
                patient, risk_level, nrs_score, sleep_quality, medication_status, risk_tendency
            )
        return farewell

    async def _safe_llm_farewell_generate(self, patient, risk_level, nrs_score,
                                     sleep_quality, medication_status, risk_tendency):
        """安全的告别语 LLM 生成，带超时降级。"""
        try:
            prompt = build_farewell_prompt(
                patient, risk_level, nrs_score, sleep_quality, medication_status, risk_tendency
            )
            farewell = (await achat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=200,
            )).strip()
            return farewell, {"success": True, "fallback_used": False}
        except Exception as exc:
            fallback = self._template_farewell(
                patient, risk_level, nrs_score, sleep_quality, medication_status, risk_tendency
            )
            return fallback, {"success": False, "fallback_used": True, "reason": str(exc)}

    def _template_farewell(self, patient, risk_level, nrs_score=None,
                           sleep_quality=None, medication_status=None,
                           risk_tendency="medium_risk"):
        """模板降级告别语：根据风险等级和具体回复定制。"""
        name = patient["name"]
        parts = []

        # 根据疼痛等级回应
        if isinstance(nrs_score, (int, float)) and nrs_score >= 7:
            parts.append(f"{name}，疼痛{int(nrs_score)}分确实挺难受的，您辛苦了")
            parts.append("我已经把情况记下来了，会马上反馈给医生，您别太担心")
            parts.append("要是疼痛实在扛不住，随时联系医院，不要硬撑")
        elif isinstance(nrs_score, (int, float)) and nrs_score >= 4:
            parts.append(f"{name}，疼痛{int(nrs_score)}分我了解了")
            parts.append("记得按时休息，如果疼痛加重了随时跟我们说")
            parts.append("我会持续关注您的情况，您也多保重")
        elif isinstance(nrs_score, (int, float)):
            parts.append(f"{name}，疼痛才{int(nrs_score)}分，恢复得很不错呢")
            parts.append("为您感到高兴，继续保持")
            parts.append("有什么不舒服随时联系我，我一直都在")
        else:
            parts.append(f"{name}，您的情况我了解了")
            parts.append("谢谢您的配合，恢复路上一路有我们陪着")

        # 根据用药情况回应
        if medication_status and ("按时" in str(medication_status) or "吃了" in str(medication_status)):
            parts.append("药按时吃非常好，这对恢复很重要")

        return "。".join(parts) + "。"

    def _summarize_history(self, history):
        if not history:
            return "暂无历史随访数据。"

        recent = history[-5:]
        lines = []
        for h in recent:
            lines.append(
                f"  {h['date']}: NRS={h.get('nrs_score', '?')}, "
                f"睡眠={h.get('sleep_quality', '?')}, "
                f"用药={'是' if h.get('medication_taken') else '否'}"
            )

        nrs_values = [h.get("nrs_score") for h in recent if h.get("nrs_score") is not None]
        trend = ""
        if len(nrs_values) >= 3:
            if nrs_values[-1] > nrs_values[0]:
                trend = "（呈上升趋势，需关注）"
            elif nrs_values[-1] < nrs_values[0]:
                trend = "（呈下降趋势，恢复良好）"
            else:
                trend = "（保持稳定）"

        return f"近{len(recent)}条记录：\n" + "\n".join(lines) + f"\n趋势分析：{trend}"

    def _detect_trend(self, history):
        valid = [h for h in history if h.get("nrs_score") is not None]
        if len(valid) < 3:
            return "数据不足"
        recent_nrs = [h["nrs_score"] for h in valid[-3:]]
        if recent_nrs == sorted(recent_nrs) and len(set(recent_nrs)) > 1:
            return "恶化"
        if recent_nrs == sorted(recent_nrs, reverse=True) and len(set(recent_nrs)) > 1:
            return "好转"
        return "稳定"

    async def generate_clarification(self, patient, ambiguous_reply):
        """用 LLM 针对患者的模糊回复生成个性化澄清追问"""
        if not self.use_llm or self.force_fallback:
            return None

        history_summary = self._summarize_history(patient.get("history", []))
        prompt = build_clarification_prompt(patient, history_summary, ambiguous_reply)

        try:
            return (await achat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=200,
            )).strip()
        except Exception:
            return None

    def _calc_days(self, discharge_date):
        try:
            discharge = datetime.strptime(discharge_date, "%Y-%m-%d")
            days = (datetime.now() - discharge).days
            return max(1, days)
        except Exception:
            return 30
