# backend/data/clinical_llm.py
# -*- coding: utf-8 -*-
"""
基础病例叙述 —— 大模型生成（参照 virtual-hospital-agents 的分层设计）

设计分层（与参考项目 create_patients_agents.py 一致）：
- 底层基础信息（人口学 / 枚举 / 分数 / 日期 / ID）：由 factories_mysql.py
  用「枚举 + 随机选」确定（确定性、可复现）。
- 上层「基础病例叙述」（仅：主诉 / 现病史 / 既往史 / 诊断详述 / 治疗小结 /
  出院小结 / 出院医嘱 / 疼痛部位）：交给 LLM 生成，并用 Pydantic schema 约束
  结构化输出，保证字段合法、可入库。

⚠️ 职责边界（与运行时 Agent 的分工，2026-07-30 确认）：
- 本模块 / 播种器【只】生成「基础病例叙述」（admission + discharge 的叙述字段）。
- 随访计划（followup_plans）、随访对话（followup_records）、风险评估
  （risk_assessments）及其下游（session / review / alert / handoff / emotion /
  multimodal / call / guardrail）全部由运行时 A/B/C Agent 在前端触发后生成并写入
  数据库（见 routes/plan.py 的 A-planner 链路 + 评审/执行 Agent），播种器不负责。

调用链路：
- 走 llm.model.achat_completion（与项目其他 Agent 共用 LLM_API_KEY / base_url / model 配置）。
- 对 Qwen3 类推理模型通过 extra_body.chat_template_kwargs.enable_thinking=False 关闭思考，
  避免 <think> 标签污染 JSON；并用 parse_json_safe 兼容 ```json 代码块。

降级策略：
- 未配置 LLM_API_KEY / LLM 不可用 / 调用超时或返回非法 JSON → 自动降级到
  factories_mysql 的模板生成器，保证离线也能播种。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from llm.model import achat_completion, is_llm_available
from llm.client import parse_json_safe

import data.factories_mysql as fm  # 现有模板生成器（降级用）


# ===================== 结构化输出 schema =====================
class CaseClinical(BaseModel):
    chief_complaint: str = Field(description="主诉，含症状与持续时间，≤40字")
    present_illness: str = Field(description="现病史，连贯、与诊断自洽")
    past_history: str = Field(description="既往史（慢病/手术/长期用药等）")
    diagnosis_detail: str = Field(description="诊断详述，解释确诊依据与疼痛机制")
    treatment_summary: str = Field(description="治疗小结（手术/介入/用药/康复要点）")
    discharge_summary: str = Field(description="出院小结（一句话概括本次诊疗）")
    discharge_instructions: str = Field(description="出院医嘱与随访注意事项")
    pain_location: str = Field(description="疼痛部位描述（如 'L4-L5 棘突旁'）")


# ===================== prompt 构造 =====================
SYS_ROLE = (
    "你是 PainSmart 慢性疼痛随访系统的一名资深疼痛科主治医师，"
    "负责为随访数据库生成真实、连贯、符合临床逻辑的中文病例叙述。"
    "只输出符合给定 JSON schema 的对象，不要解释，不要用 Markdown 代码块包裹。"
)


def _case_messages(p: dict) -> List[dict]:
    user = (
        f"患者：{p['name']}（{p['gender']}，{p['age']}岁，{p.get('profession','居民')}）。\n"
        f"本次诊断：{p['diagnosis']}（疼痛机制：{p['pain_type']}）。\n"
        f"手术/介入：{p['surgery']}。\n"
        f"出院带药：{p['meds']}。\n"
        f"疼痛部位：{p['pain_location']}；疼痛持续：{p.get('pain_duration','数月')}；出院 NRS {p.get('nrs','')} 分。\n"
        "请生成该患者的病例叙述：主诉 / 现病史 / 既往史 / 诊断详述 / 治疗小结 / 出院小结 / 出院医嘱 / 疼痛部位。"
    )
    return [{"role": "system", "content": SYS_ROLE}, {"role": "user", "content": user}]


# ===================== 调用封装 =====================
# 适配纯推理模型（Qwen3.6-35B 等）：reasoning 会占用 token 预算，
# max_tokens 必须给足，否则正文 JSON 被截断为空 → 误判降级。
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# JSON 代码块围栏（部分模型即使 response_format 也输出 ```json 包裹）
_CODE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _extract_json(text: str) -> str:
    """从 LLM 原始输出中提取 JSON：优先取代码块，否则取整段。"""
    text = _strip_think(text or "")
    m = _CODE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


# 中文键 → 英文字段名映射（LLM 常输出中文键，Pydantic 用英文字段）
_CN_KEY_MAP = {
    "主诉": "chief_complaint",
    "现病史": "present_illness",
    "既往史": "past_history",
    "诊断详述": "diagnosis_detail",
    "治疗小结": "treatment_summary",
    "出院小结": "discharge_summary",
    "出院医嘱": "discharge_instructions",
    "疼痛部位": "pain_location",
}


async def _llm_json(messages, schema, *, temperature: float = 0.4, max_tokens: int = 12000):
    """调用 LLM 并解析为 schema 实例；任何失败返回 None（交由调用方降级）。

    max_tokens 默认 12000：推理模型思考(Reasoning)占用大量预算，
    预算过小会把正文 JSON 截断（finish_reason=length）→ 解析失败 → 误判降级模板。
    """
    if not is_llm_available():
        return None
    try:
        raw = await achat_completion(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
    except Exception:
        return None
    data = parse_json_safe(_extract_json(raw or ""))
    if not data or not isinstance(data, dict):
        return None
    # 中文键 → 英文字段名（LLM 输出中文键，Pydantic schema 用英文字段）
    normalized = {}
    for k, v in data.items():
        if k in _CN_KEY_MAP:
            normalized[_CN_KEY_MAP[k]] = v
        elif k in schema.model_fields:
            normalized[k] = v
    try:
        return schema(**normalized)
    except Exception:
        return None


# ===================== 降级模板 =====================
def _fallback_case(p: dict) -> CaseClinical:
    return CaseClinical(
        chief_complaint=f"{p['diagnosis']}相关疼痛反复{p.get('pain_duration','数月')}，近 1 周加重。",
        present_illness=fm.zh_text(),
        past_history=fm.zh_text(),
        diagnosis_detail=f"结合病史与查体，考虑{p['diagnosis']}（{p['pain_type']}）。",
        treatment_summary=f"予{p['surgery'] or '保守治疗'}及规范化镇痛方案，疼痛较前缓解。",
        discharge_summary=f"患者{p['name']}，{p['diagnosis']}，经治疗后好转出院。",
        discharge_instructions="规律服药、记录疼痛日记、定期复诊，NRS≥7 或爆发痛及时就诊。",
        pain_location=p.get("pain_location") or fm.zh_text(),
    )


# ===================== 病例叙述生成器（LLM 优先，模板降级）=====================
async def gen_case_clinical(p: dict) -> CaseClinical:
    obj = await _llm_json(_case_messages(p), CaseClinical, temperature=0.5, max_tokens=12000)
    return obj or _fallback_case(p)


# ===================== 单患者编排 =====================
async def generate_patient_clinical(p: dict, n_records: int = 0) -> Dict[str, Any]:
    """返回该患者的「基础病例叙述」，供 seed_mysql 写入 admission / discharge。

    注意：随访计划 / 随访对话 / 风险评估不在播种阶段生成，留给运行时
    A/B/C Agent 在前端触发后写入（见 routes/plan.py 的 A-planner 链路）。

    返回结构：
    {
      "case": CaseClinical,
    }
    """
    case = await gen_case_clinical(p)
    return {"case": case}


__all__ = [
    "CaseClinical",
    "generate_patient_clinical",
]
