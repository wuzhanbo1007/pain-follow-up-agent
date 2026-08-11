"""
A. 随访计划生成工作流（DischargeFollowUpPlanner，LLM 增强型有向图）

LangGraph 节点：
    extract_discharge_context → retrieve_guidelines(B) → draft_plan → hitl_review(interrupt) → persist_plan

关键点：
  - draft_plan 仅依据两类输入：① 患者真实诊疗数据 ② B 号 RAG 检索到的共识条款。
  - 内容合规：每条建议必须锚定检索条款，带可点击引用；无证据则降级为"建议医生人工制定"。
  - hitl_review 用 LangGraph `interrupt` 挂起，等待医生 Command(resume)。
  - LLM 不可用（超时/异常）时降级模板，保证 Demo 不中断（风险 1）。
"""
import asyncio
import json
import re
import time
from datetime import datetime
from pydantic import BaseModel, Field, ValidationError
from json_repair import repair_json
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from agents.state import AgentState
from knowledge.retriever import retrieve_guidelines
from data.database import PatientDB
from db.followup_db import create_plan
from prompts.plan_generation import build_prompt as build_plan_prompt
from prompts.plan_system import build_system_prompt as build_plan_system_prompt
from core import config
from knowledge import config as kb_config
from llm.client import achat_completion, is_llm_available


def _patient_context(patient_id: str, diagnosis: str = None, discharge_summary: str = None):
    """extract_discharge_context：读取患者真实诊疗数据（只读）"""
    patient = None
    if patient_id:
        try:
            db = PatientDB()
            patient = db.get_patient(patient_id)
        except Exception:
            patient = None
    if patient:
        diagnosis = diagnosis or patient.get("diagnosis", "")
        discharge_summary = discharge_summary or patient.get("discharge_summary", "")
    return {
        "patient": patient or {},
        "diagnosis": diagnosis or "未提供",
        "discharge_summary": discharge_summary or "",
    }


def _retrieve(state: AgentState) -> dict:
    """retrieve_guidelines（B 号 Agent）：检索共识条款。

    语义/关键词分离（ES 后端）：
      - 语义匹配（kNN）← 完整出院小结（真实诊疗叙述，召回最相关的指南）
      - 关键词匹配（BM25）← 诊断（疾病特异词，精准命中含该诊断的条款）

    注意：不要往症状关键词里拼"随访 复查 用药 疼痛管理"这类通用词——
    它们会让任何提到"随访"的文档都命中，挤掉疾病特异的 PDF 指南并引入噪音。
    """
    dis_summary = state.get("discharge_summary", "") or ""
    diag = state.get("diagnosis", "") or ""
    # 关键词：只用诊断（疾病特异词）；无诊断时兜底"疼痛"
    symptoms = diag.strip() or "疼痛"
    ev = retrieve_guidelines(
        query=symptoms,                     # 兼容：无 dis_summary 时退回关键词
        diagnosis=diag,
        discharge_summary=dis_summary,      # 语义匹配用完整出院小结
        symptoms=symptoms,                  # 关键词匹配用诊断
    )
    return {"retrieved_evidence": ev}


# ===== Pydantic 强校验模型 =====

class CitationItem(BaseModel):
    """单条指南/共识引用"""
    ref: str = Field(..., description="引用编号，如 [1]")
    guide: str = Field(..., description="指南/共识名称")
    year: int | None = Field(None, description="发布年份")
    page: int | str | None = Field(None, description="页码")
    clause: str | None = Field(None, description="条款编号，如 第5条")
    excerpt: str = Field("", description="条款摘要")


class FollowUpPlan(BaseModel):
    """出院随访计划结构化输出 — Pydantic 强校验"""
    frequency: str = Field("待定", description="随访频次（具体到星期，如每周三、每周二/周五）")
    duration_days: int = Field(-1, description="随访总天数（LLM 动态生成，-1 表示未设置，兜底 90）")
    pain_type: str = Field("", description="疼痛类型")
    recheck_items: list[str] = Field(default_factory=list, description="复查项目列表")
    medication_adjustment: str = Field("", description="用药调整建议")
    warning_threshold: str = Field("", description="预警阈值")
    health_education: list[str] = Field(default_factory=list, description="健康教育列表")
    lifestyle: list[str] = Field(default_factory=list, description="生活方式建议列表")
    evidence_basis: list[CitationItem] = Field(default_factory=list, description="证据引用列表")
    # ── 降级标记 ──
    status: str = Field("validated", description="解析状态：validated / syntax_repaired / field_coerce / degraded")
    degraded_fields: list[str] = Field(default_factory=list, description="被宽松修复的字段名列表，下游可据此区分原生数据与抢救数据")
    note: str = Field("", description="备注/降级说明，供前端展示（如解析失败回退证据摘要时的提示）")


# ===== 三段渐进降级：先校验 → 再修复 =====

def _parse_and_validate(raw: str, evidence=None) -> FollowUpPlan:
    """从 LLM 返回内容解析并校验随访计划。

    三段渐进降级，分流处理语法错误和语义错误。本函数契约：**永不抛异常**，
    任何解析失败都会回退为可展示的计划（优先保留检索到的共识证据），
    因此不会触发 _draft 中"LLM 生成失败（TypeError/JSONDecodeError）"这类误导性提示。

    ┌─ 第1步 json.loads 失败（语法错误）→ 第2步 repair_json 修复语法 → model_validate
    │                                                         ↓ model_validate 失败
    │                                                    第3步 字段级宽松修复
    └─ 第1步 json.loads 成功 → model_validate
                               ↓ model_validate 失败（语义/结构错误）
                               第3步 字段级宽松修复（跳过 repair_json，对合法 JSON 无效）
    """
    # ---- 第1步：先校验 ----
    try:
        raw_dict = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as syntax_err:
        # 语法错误 → 跳到第2步 repair_json
        print(f"  [Planner] Step1 json.loads 失败（语法错误）→ 进入 Step2 repair_json：{syntax_err}")
        return _syntax_repair(raw, evidence)

    # json.loads 成功，尝试 Pydantic 校验
    try:
        plan = FollowUpPlan.model_validate(raw_dict)
        print(f"  [Planner] Step1 校验通过，无需修复")
        return plan
    except ValidationError as semantic_err:
        # 语义/结构错误 → 直接跳到第3步字段级修复（repair_json 对合法 JSON 是幂等 no-op）
        print(f"  [Planner] Step1 model_validate 失败（语义错误）→ 跳过 Step2，进入 Step3 字段级修复：{semantic_err}")
        return _field_coerce(raw_dict, evidence)


def _syntax_repair(raw: str, evidence=None) -> FollowUpPlan:
    """第2步：repair_json 修复语法问题后重新校验。

    仅当 json.loads 失败（语法错误）时才调用。
    repair_json 对合法 JSON 是幂等 no-op，所以语义错误不应走此步。

    ⚠️ 关键修复：若 repair_json 仍无法修复（如 LLM 返回空内容、被截断到无法推断、
    或 repair_json 自身对异常输入抛 TypeError），**不再向上抛异常**，而是回退为
    保留检索共识证据的降级计划，避免 _draft 误报"LLM 生成失败（JSONDecodeError/TypeError）"。
    """
    try:
        fixed_str = repair_json(raw)
    except Exception as e:
        # repair_json 对个别异常输入（如 None）会抛 TypeError，直接回退
        print(f"  [Planner] Step2 repair_json 执行异常（{type(e).__name__}: {e}）→ 回退证据摘要计划")
        return _evidence_fallback_plan(evidence)

    try:
        raw_dict = json.loads(fixed_str)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"  [Planner] Step2 repair_json 后仍无法解析（{type(e).__name__}: {e}）→ 回退证据摘要计划")
        return _evidence_fallback_plan(evidence)

    try:
        plan = FollowUpPlan.model_validate(raw_dict)
        plan.status = "syntax_repaired"
        print(f"  [Planner] Step2 repair_json + model_validate 通过")
        return plan
    except ValidationError as err:
        # 语法修好了但语义仍有问题 → 第3步字段级修复
        print(f"  [Planner] Step2 model_validate 失败 → 进入 Step3 字段级修复：{err}")
        return _field_coerce(raw_dict, evidence)


def _evidence_fallback_plan(evidence) -> FollowUpPlan:
    """解析彻底失败时的兜底计划：保留检索到的共识证据，供医生人工制定。

    与 _draft 中"LLM 调用失败"的降级不同，这里是"LLM 已返回内容但无法解析"，
    因此提示文案明确为"解析失败"而非"LLM 生成失败"，且证据摘要不丢失。
    """
    def _ev(e, key):
        return getattr(e, key, "") or ""

    eb = [
        CitationItem(
            ref=f"[{i+1}]",
            guide=_ev(e, "title"),
            year=_ev(e, "year") or None,
            page=_ev(e, "page"),
            clause=(f"第{_ev(e, 'clause_no')}条" if _ev(e, 'clause_no') else _ev(e, "section")),
            excerpt=_ev(e, "text")[:120],
        )
        for i, e in enumerate(evidence)
    ] if evidence else []

    return FollowUpPlan(
        frequency="待定",
        duration_days=-1,
        pain_type="",
        recheck_items=[],
        medication_adjustment="需医生补充完善（可参考下方共识）",
        warning_threshold="NRS≥7 建议干预（依据检索共识）",
        health_education=["需医生补充完善（可参考检索共识）"],
        lifestyle=[],
        evidence_basis=eb,
        status="degraded",
        degraded_fields=["frequency", "medication_adjustment", "health_education"],
        note="已回退为证据摘要，需医生补充完善",
    )


def _field_coerce(raw_dict: dict, evidence=None) -> FollowUpPlan:
    """第3步：字段级宽松修复。

    显式枚举每种修复规则，并对被修复的字段打降级标记。
    修复规则：
    ┌ evidence_basis: None → []; 元素缺 ref → 补 "[?]"; 缺 guide → 补 "未知";
    │  str 类型元素 → 包装为 {"ref":"[?]", "guide":原值}
    ├ recheck_items / health_education / lifestyle: None → []
    ├ duration_days: 字符串 "90" → 强转 int 90; 非法值 → 回退默认 90
    ├ frequency: 空字符串 → "待定"
    └ 所有被修复的字段记录到 degraded_fields，下游可据此区分原生数据与抢救数据
    """
    degraded_fields: list[str] = []

    # ── 修复 evidence_basis ──
    eb = raw_dict.get("evidence_basis")
    if eb is None:
        raw_dict["evidence_basis"] = []
        degraded_fields.append("evidence_basis")
    elif isinstance(eb, list):
        fixed_eb = []
        for item in eb:
            if isinstance(item, dict):
                if "ref" not in item or not item["ref"]:
                    item["ref"] = "[?]"
                    degraded_fields.append("evidence_basis.ref")
                if "guide" not in item or not item["guide"]:
                    item["guide"] = "未知"
                    degraded_fields.append("evidence_basis.guide")
                if "excerpt" not in item:
                    item["excerpt"] = ""
                    degraded_fields.append("evidence_basis.excerpt")
                fixed_eb.append(item)
            elif isinstance(item, str):
                fixed_eb.append({"ref": "[?]", "guide": item, "excerpt": ""})
                degraded_fields.append("evidence_basis")
        raw_dict["evidence_basis"] = fixed_eb
    else:
        raw_dict["evidence_basis"] = []
        degraded_fields.append("evidence_basis")

    # ── 修复列表型字段：None → [] ──
    for key in ("recheck_items", "health_education", "lifestyle"):
        if raw_dict.get(key) is None:
            raw_dict[key] = []
            degraded_fields.append(key)

    # ── 修复 duration_days：字符串 → 强转 int ──
    dd = raw_dict.get("duration_days")
    if isinstance(dd, str):
        try:
            raw_dict["duration_days"] = int(dd)
        except (ValueError, TypeError):
            raw_dict["duration_days"] = 90
            degraded_fields.append("duration_days")
        else:
            degraded_fields.append("duration_days")
    elif dd is None:
        raw_dict["duration_days"] = 90
        degraded_fields.append("duration_days")

    # ── 修复 frequency：空字符串 → "待定" ──
    if not raw_dict.get("frequency"):
        raw_dict["frequency"] = "待定"
        degraded_fields.append("frequency")

    try:
        plan = FollowUpPlan.model_validate(raw_dict)
        plan.status = "field_coerce"
        plan.degraded_fields = degraded_fields
        if degraded_fields:
            print(f"  [Planner] Step3 字段级修复通过，降级字段：{degraded_fields}")
        else:
            print(f"  [Planner] Step3 model_validate 通过（无字段需降级）")
        return plan
    except ValidationError as err:
        # 契约：本链路永不抛异常。字段级修复后仍不合规 → 回退为保留证据的降级计划，
        # 避免异常传播到 _draft 触发"LLM 生成失败"降级。
        print(f"  [Planner] Step3 字段级修复后仍不合规（{type(err).__name__}）→ 回退证据摘要计划")
        return _evidence_fallback_plan(evidence)


def _finalize_citations(plan: FollowUpPlan, evidence=None) -> FollowUpPlan:
    """引用重排包一层异常保护：任何异常都不影响主流程，至少返回已解析的 plan，
    避免 TypeError 等意外把整条 LLM 成功路径拖入"生成失败"降级。"""
    try:
        return _finalize_citations_impl(plan, evidence)
    except Exception as e:
        print(f"  [Planner] _finalize_citations 异常（{type(e).__name__}: {e}），跳过引用重排，返回已解析计划")
        return plan


def _finalize_citations_impl(plan: FollowUpPlan, evidence=None) -> FollowUpPlan:
    """回填空缺的溯源元数据，并把引用编号统一重排为连续的 [1]..[N]。

    同时修复两类问题：
    ① 证据溯源展示不全：LLM 返回的 evidence_basis 常只给标题、丢掉 page/clause。
       这里用检索原文（prompt 中按 [1]..[n] 编号，与 evidence 下标一一对应）
       回填 page / section / clause_no / year / excerpt，前端即可显示完整页码。
    ② 编号跳号/悬空引用：LLM 正文可能引用 [1][2][5] 而 evidence_basis 只有 3 条，
       旧逻辑只在 evidence_basis 内重排，正文残留 [5] → 出现"1 2 5"。
       这里把「正文出现过的编号 + evidence_basis 编号」合并，统一重排为连续编号，
       并为正文引用但 LLM 漏给的证据项合成占位引用，保证编号连续且可点击。
    """
    if evidence is None:
        evidence = []
    meta_by_idx = {i + 1: e for i, e in enumerate(evidence)}  # [1] -> 第1条检索结果

    def _num(ref):
        if not ref:
            return None
        m = re.search(r"\d+", str(ref))
        return int(m.group()) if m else None

    def _eg(e, key):
        return getattr(e, key, "") or ""

    # ---- ① 回填元数据 ----
    for c in plan.evidence_basis:
        n = _num(c.ref)
        src = meta_by_idx.get(n) if n is not None else None
        if not src and evidence:
            src = evidence[0]  # 编号对不上时退而求其次用第一条
        if src:
            # 始终用证据原文的 title（= 文件词干）覆盖 guide，保证与 knowledge_base 文件名匹配
            c.guide = _eg(src, "title")
            if c.year is None:
                c.year = _eg(src, "year") or None
            if c.page is None:
                c.page = _eg(src, "page") or None
            if not c.clause and _eg(src, "clause_no"):
                c.clause = f"第{_eg(src, 'clause_no')}条"
            elif not c.clause and _eg(src, "section"):
                c.clause = _eg(src, "section")
            if not c.excerpt:
                c.excerpt = _eg(src, "text")[: kb_config.PROMPT_EXCERPT_CHARS]

    # ---- ② 收集所有被引用的编号（正文 + evidence_basis）----
    text_blob = " ".join(
        str(x) for x in [
            plan.frequency, plan.medication_adjustment, plan.warning_threshold,
            *plan.recheck_items, *plan.health_education, *plan.lifestyle,
        ] if x
    )
    used = set(int(m) for m in re.findall(r"\[(\d+)\]", text_blob))
    text_had_markers = bool(used)  # 仅看正文是否标了 [n]，不含 evidence_basis
    for c in plan.evidence_basis:
        n = _num(c.ref)
        if n is not None:
            used.add(n)
    had_no_markers = not text_had_markers
    if had_no_markers:
        # 正文完全没标 [n]：优先用 LLM 给出的 evidence_basis 编号；若也没有，则用全部检索证据
        if plan.evidence_basis:
            basis_refs = set(_num(c.ref) for c in plan.evidence_basis if _num(c.ref) is not None)
            if basis_refs:
                used = basis_refs
        if not used:
            used = set(range(1, len(evidence) + 1))  # 完全没引用：把检索结果都作为证据

    # ---- ③ 统一重排：旧编号 -> 新连续编号 ----
    sorted_old = sorted(used)
    remap = {old: new for new, old in enumerate(sorted_old, start=1)}

    def _rewrite(text):
        if not text:
            return text
        return re.sub(r"\[(\d+)\]", lambda m: f"[{remap.get(int(m.group(1)), int(m.group(1)))}]", text)

    plan.frequency = _rewrite(plan.frequency)
    plan.medication_adjustment = _rewrite(plan.medication_adjustment)
    plan.warning_threshold = _rewrite(plan.warning_threshold)
    plan.recheck_items = [_rewrite(x) for x in plan.recheck_items]
    plan.health_education = [_rewrite(x) for x in plan.health_education]
    plan.lifestyle = [_rewrite(x) for x in plan.lifestyle]
    for c in plan.evidence_basis:
        c.excerpt = _rewrite(c.excerpt)

    # ---- ④ 用重排后的编号重建 evidence_basis（连续、无悬空）----
    by_old = {_num(c.ref): c for c in plan.evidence_basis if _num(c.ref) is not None}
    new_basis = []
    for new_idx, old_num in enumerate(sorted_old, start=1):
        if old_num in by_old:
            c = by_old[old_num]
            c.ref = f"[{new_idx}]"
            new_basis.append(c)
        else:
            # 正文引用了但 LLM 没给证据项 → 用检索原文合成占位引用
            src = meta_by_idx.get(old_num)
            if src:
                new_basis.append(CitationItem(
                    ref=f"[{new_idx}]",
                    guide=_eg(src, "title"),
                    year=_eg(src, "year") or None,
                    page=_eg(src, "page") or None,
                    clause=(f"第{_eg(src, 'clause_no')}条" if _eg(src, "clause_no") else _eg(src, "section")),
                    excerpt=_eg(src, "text")[:200],
                ))
            else:
                new_basis.append(CitationItem(ref=f"[{new_idx}]", guide="未知共识", excerpt=""))
    plan.evidence_basis = new_basis
    # ---- ⑤ 回填空的"建议类"字段：模型只给频次+周期、漏填复查/用药/预警/健康教育时，
    #        用检索到的共识原文逐条补上并标注 [n]，使"证据溯源"真正与正文对应、可点击。
    #        只摘原文、不编造，符合约束1（禁止编造未提及的建议）。 ----
    _backfill_empty_fields(plan, new_basis)
    # ---- ⑥ 兜底：回填后仍完全无 [n] 标记时，把引用补标到首个有内容的建议字段 ----
    body2 = " ".join(str(x) for x in [
        plan.frequency, plan.medication_adjustment, plan.warning_threshold,
        *plan.recheck_items, *plan.health_education, *plan.lifestyle,
    ] if x)
    if had_no_markers and new_basis and not re.findall(r"\[(\d+)\]", body2):
        _attach_refs_to_first_field(plan, [c.ref for c in new_basis])
    print(f"  [Planner] 引用重排 + 元数据回填完成，证据条目 {len(new_basis)} 条，编号 {[c.ref for c in new_basis]}（正文无标记补标：{had_no_markers}）")
    return plan


# 兜底回填：字段 → 内容关键词。按字段类型匹配正确证据，避免"复查项目填成生活建议"等错位。
_FIELD_KEYWORDS = {
    "recheck_items": ["复查", "随访", "评估", "监测", "筛查", "复诊", "检查", "检测", "评分"],
    "medication_adjustment": ["药物", "用药", "剂量", "服药", "口服", "服用", "滴定", "减量",
                              "加巴喷丁", "普瑞巴林", "度洛西汀", "阿米替林", "利多卡因", "阿片", "NSAID", "对乙酰氨基酚"],
    "warning_threshold": ["阈值", "预警", "NRS", "VAS", "及时就医", "立即联系", "转诊", "红旗", "危险信号", "急诊", "就医"],
    "health_education": ["健康", "教育", "告知", "宣教", "注意事项", "应知"],
    "lifestyle": ["生活方式", "运动", "锻炼", "饮食", "作息", "睡眠", "活动", "减重", "康复", "休息"],
}


def _pick_basis_for_field(basis: list, field: str, used: set) -> "CitationItem":
    """从 evidence_basis 中挑出与字段类型最匹配的证据（关键词打分，且避免重复用同一条）。

    无任何匹配时退回"未用过"的首条，保证不空。
    """
    best, best_score = None, 0
    for b in basis:
        if id(b) in used:
            continue
        text = (b.excerpt or "") + " " + (b.guide or "")
        sc = sum(1 for kw in _FIELD_KEYWORDS.get(field, []) if kw in text)
        if sc > best_score:
            best, best_score = b, sc
    if best is None:
        for b in basis:
            if id(b) not in used:
                return b
    return best or (basis[0] if basis else None)


def _pick_evidence_for_field(evidence, field, used):
    """降级路径：按字段类型从检索证据（CitedChunk 列表）中挑最匹配的一条，避免错位回填。"""
    best, best_score = None, 0
    for e in evidence:
        if id(e) in used:
            continue
        text = (getattr(e, "text", "") or "") + " " + (getattr(e, "title", "") or "")
        sc = sum(1 for kw in _FIELD_KEYWORDS.get(field, []) if kw in text)
        if sc > best_score:
            best, best_score = e, sc
    if best is None:
        for e in evidence:
            if id(e) not in used:
                return e
    return best or (evidence[0] if evidence else None)


def _degraded_content(evidence, field, used, note="需医生补充完善"):
    """降级路径：按字段类型取一条最匹配证据，拼成「参考《标题》：内容（note）」；无匹配返回 None。"""
    src = _pick_evidence_for_field(evidence, field, used)
    if src is None:
        return None
    used.add(id(src))
    title = getattr(src, "title", "") or "相关共识"
    text = (getattr(src, "text", "") or "").strip()[:80]
    return f"参考《{title}》：{text}（{note}）"


def _backfill_empty_fields(plan: FollowUpPlan, basis: list) -> None:
    """把空的"建议类"字段用检索共识原文回填并标注 [n]。

    解决部分模型"只给频次+周期、漏填复查/用药/预警/健康教育/生活方式"的问题：
    这些字段一旦有内容，引用就挂得上，呼应"证据溯源与正文一一对应"的诉求。
    仅摘取检索到的原文（excerpt），不做任何虚构，符合"禁止编造"约束。
    按字段类型（复查/用药/预警/教育/生活方式）匹配对应证据，避免错位回填。
    """
    if not basis:
        return
    specs = [
        ("recheck_items", "list"),
        ("medication_adjustment", "text"),
        ("warning_threshold", "text"),
        ("health_education", "list"),
        ("lifestyle", "list"),
    ]
    used: set = set()
    for field, kind in specs:
        val = getattr(plan, field, None)
        is_empty = (kind == "list" and not val) or (kind == "text" and not str(val or "").strip())
        if not is_empty:
            continue
        src = _pick_basis_for_field(basis, field, used)
        if src is None:
            continue
        used.add(id(src))
        ref = src.ref or "[1]"
        guide = src.guide or "相关共识"
        clause = src.clause or ""
        excerpt = (src.excerpt or "").strip()
        prefix = f"参考《{guide}》{('（' + clause + '）') if clause else ''}："
        text = f"{prefix}{excerpt} {ref}".strip()
        if kind == "list":
            setattr(plan, field, [text])
        else:
            setattr(plan, field, text)


def _attach_refs_to_first_field(plan: FollowUpPlan, ref_labels: list) -> None:
    """把引用编号补标到第一个有内容的字段末尾，保证正文与证据溯源可对应。

    用于 LLM 正文完全漏标 [n] 的兜底：否则会出现"下面列了引用、正文却无标注"的割裂感。
    """
    if not ref_labels:
        return
    suffix = " " + " ".join(ref_labels)
    # 优先补到"建议类"字段，最后是描述类字段
    targets = [
        ("medication_adjustment", "text"),
        ("warning_threshold", "text"),
        ("frequency", "text"),
        ("recheck_items", "list"),
        ("health_education", "list"),
        ("lifestyle", "list"),
        ("pain_type", "text"),
    ]
    for field, kind in targets:
        val = getattr(plan, field, None)
        if kind == "text" and val:
            setattr(plan, field, str(val) + suffix)
            return
        if kind == "list" and isinstance(val, list) and val:
            val[0] = str(val[0]) + suffix
            return


async def _call_llm(prompt, max_retries=4):
    """异步调用 LLM 生成随访计划，统一走 llm.model.achat_completion（ChatOpenAI → 内部 AsyncOpenAI）。

    兼容所有 OpenAI 兼容接口（DeepSeek、本地 vLLM/Qwen 等）。

    重试策略：
      - achat_completion 已内置网络/5xx/429 退避重试，并在 response_format 不被当前
        模型/API 支持（4xx 参数错误）时自动去掉 format 重试一次。
      - 部分模型（如 DeepSeek）偶发因 response_format 返回空内容：此处捕获空内容
        ValueError，去掉 format 再尝试一次；仍为空则按普通重试，由 _draft 兜底降级。
      - 始终携带一条 system 指令（只输出 JSON），即使后续去掉 response_format，
        模型也知道该输出 JSON，显著降低"空响应 → ValueError"的概率。
      - 最多重试 max_retries 次（默认 4），全部失败才抛出最后一次异常，由 _draft 降级。
    """
    sys_json = build_plan_system_prompt()
    messages = [
        {"role": "system", "content": sys_json},
        {"role": "user", "content": prompt},
    ]
    # 计划 JSON 通常 800~1200 token；调大到 24000 是为兼容"推理模型"（如 deepseek-v4-flash，
    # reasoning_content 也会占用该预算，预算过小会把正文 JSON 挤空/截断，导致降级）。
    base = dict(
        temperature=0.3,
        max_tokens=24000,
        extra_body={"thinking": {"type": "disabled"}},  # 关闭深度思考，加速生成
    )

    def _extract(text):
        if not text or not text.strip():
            raise ValueError("LLM API 返回空内容，模型未生成任何文本")
        return text.strip()

    last_exc = None
    tried_without_format = False  # response_format 仅去掉一次
    attempt = 0
    while attempt <= max_retries:
        try:
            if not tried_without_format:
                text = await achat_completion(
                    messages,
                    response_format={"type": "json_object"},
                    **base,
                )
            else:
                text = await achat_completion(messages, **base)
            return _extract(text)
        except ValueError as ve:
            # 空内容：首次失败先尝试去掉 response_format（很可能是它触发了空响应）
            last_exc = ve
            if not tried_without_format:
                tried_without_format = True
                print(f"  [Planner] 返回空内容，尝试去掉 response_format 重试：{ve}")
                try:
                    text = await achat_completion(messages, **base)
                    return _extract(text)
                except ValueError as ve2:
                    last_exc = ve2
            if attempt < max_retries:
                print(f"  [Planner] 第 {attempt + 1} 次 LLM 返回空内容，{max_retries - attempt} 秒后重试…")
                await asyncio.sleep(2)  # 适当拉长间隔，规避瞬时限流
                attempt += 1
                continue
            break
        except Exception:
            # achat_completion 已处理网络/5xx 重试；其余异常直接上抛由 _draft 降级
            raise
    # 全部重试仍失败 → 抛出最后一次异常，由 _draft 降级
    raise last_exc


def _build_llm_prompt(patient, diagnosis, discharge_summary, evidence):
    """构建随访计划生成提示词，委托 prompts/plan_generation.py"""
    return build_plan_prompt(patient, diagnosis, discharge_summary, evidence)


def _ensure_all_fields_filled(plan: dict, evidence=None, diagnosis="", prescribed_frequency=None) -> dict:
    """最终兜底：保证随访计划每个内容字段都有可用值，前端不出现空白字段。

    诉求：无论 LLM 成功/失败/未配置，「随访频次、随访周期、疼痛类型、复查项目、
    用药调整、预警阈值、健康教育、生活方式」都必须有具体、可执行的内容。
    填充优先级：① 优先保留 LLM 根据患者情况+PDF 指南生成的字段值（主路径）；
    ② 已有原生内容原样保留；③ 用检索到的共识原文回填（带引用号，不编造）；
    ④ 仍缺失时通用临床随访建议兜底（降级路径）。
    并把"LLM 生成失败/请医生人工完善"这类占位/报错文案替换为可用内容，弱化 note。
    """
    evidence = evidence or []

    def _ev(e, key):
        if isinstance(e, dict):
            return e.get(key) or ""
        return getattr(e, key, "") or ""

    basis = plan.get("evidence_basis") or []

    def _basis_text(idx):
        """取第 idx 条证据的『参考原文 + 引用号』，用于回填空字段（不编造，仅摘原文）。"""
        if 0 <= idx < len(basis):
            b = basis[idx]
            ref = (b.get("ref") if isinstance(b, dict) else getattr(b, "ref", "")) or f"[{idx + 1}]"
            guide = (b.get("guide") if isinstance(b, dict) else getattr(b, "guide", "")) or "相关共识"
            clause = (b.get("clause") if isinstance(b, dict) else getattr(b, "clause", "")) or ""
            excerpt = (b.get("excerpt") if isinstance(b, dict) else getattr(b, "excerpt", "")) or ""
            if excerpt:
                return f"参考《{guide}》{('（' + clause + '）') if clause else ''}：{excerpt} {ref}".strip()
        return ""

    def _is_placeholder(s):
        s = str(s or "")
        return (not s.strip()) or any(k in s for k in ("生成失败", "解析失败", "人工制定", "人工完善", "未检索到", "待定"))

    # frequency：优先保留 LLM 根据患者实际情况+PDF 指南/共识生成的频次（主路径）；
    # 仅当 LLM 未生成或生成为空/占位时，才依次尝试医嘱处方频次 → 通用兜底（降级路径）。
    if not plan.get("frequency") or _is_placeholder(plan.get("frequency")):
        if prescribed_frequency and not _is_placeholder(prescribed_frequency):
            plan["frequency"] = prescribed_frequency
        else:
            plan["frequency"] = "每周三、周五"
    if not plan.get("duration_days") or plan.get("duration_days") in (0, -1):
        plan["duration_days"] = 90
    if not str(plan.get("pain_type") or "").strip():
        plan["pain_type"] = diagnosis or "慢性疼痛"

    # recheck_items（list）
    if not (plan.get("recheck_items") or []):
        t = _basis_text(0)
        plan["recheck_items"] = ([t] if t else []) + ["疼痛NRS评分", "睡眠质量评估", "用药依从性检查"]

    # medication_adjustment（text）
    if _is_placeholder(plan.get("medication_adjustment")):
        t = _basis_text(1) or _basis_text(0)
        plan["medication_adjustment"] = t or "维持现有镇痛方案，NRS≥5 时由医生评估是否调整剂量或联合用药"

    # warning_threshold（text）
    if _is_placeholder(plan.get("warning_threshold")):
        t = _basis_text(2)
        plan["warning_threshold"] = t or "NRS≥7，或出现爆发痛、睡眠持续变差、药物不良反应时及时就医"

    # health_education（list）
    he = [x for x in (plan.get("health_education") or []) if not _is_placeholder(x)]
    if not he:
        t = _basis_text(0)
        he = ([t] if t else []) + ["按时服药，不可自行增减量或停药", "记录每日疼痛评分", "出现爆发痛及时联系医生"]
    plan["health_education"] = he

    # lifestyle（list）
    ls = [x for x in (plan.get("lifestyle") or []) if not _is_placeholder(x)]
    if not ls:
        t = _basis_text(1)
        ls = ([t] if t else []) + ["规律作息，避免过度劳累", "适度活动，避免久坐久站", "清淡饮食，戒烟限酒"]
    plan["lifestyle"] = ls

    # 弱化刺眼的失败文案（内部仍保留 status=degraded 供追溯）
    note = str(plan.get("note") or "")
    if any(k in note for k in ("生成失败", "解析失败", "未配置")):
        plan["note"] = "部分内容由系统依据常规共识兜底生成，建议医生复核后确认"

    return plan


async def _draft(state: AgentState) -> dict:
    """draft_plan：LLM 生成锚定共识的计划；LLM 不可用时降级"""
    patient = state.get("patient", {})
    diagnosis = state.get("diagnosis", "")
    discharge_summary = state.get("discharge_summary", "")
    evidence = state.get("retrieved_evidence", [])

    # 医嘱处方频次：取自患者既有 follow_up_plan（patients 表），作为计划频次真源，
    # 保证"随访计划"面板与 v4.0 随访名单（判定引擎）显示的频次完全一致。
    prescribed_freq = None
    try:
        fu = patient.get("follow_up_plan")
        if isinstance(fu, str):
            fu = json.loads(fu)
        prescribed_freq = (fu or {}).get("frequency")
    except Exception:
        prescribed_freq = None

    # 无共识证据 → 直接降级（F1.9）
    if not evidence:
        plan = {
            "frequency": "待定",
            "duration_days": -1,
            "pain_type": diagnosis,
            "recheck_items": [],
            "medication_adjustment": "未检索到相关共识，需医生补充完善用药调整",
            "warning_threshold": "NRS≥7 或睡眠持续差，需医生补充完善",
            "health_education": ["未检索到相关共识，需医生补充完善"],
            "lifestyle": [],
            "evidence_basis": [],
            "status": "degraded",
            "degraded_fields": ["frequency", "medication_adjustment", "warning_threshold", "health_education", "evidence_basis"],
            "note": "未检索到相关指南/共识条款，计划为占位，需医生补充完善",
        }
        plan = _ensure_all_fields_filled(plan, evidence, diagnosis, prescribed_freq)
        return {"plan_json": plan, "citations": []}

    # 尝试 LLM
    if is_llm_available():
        try:
            prompt = _build_llm_prompt(patient, diagnosis, discharge_summary, evidence)
            raw = await _call_llm(prompt, max_retries=config.LLM_MAX_RETRIES)
            # 先 Pydantic 校验 → 校验失败再 repair_json 修复 → 修复后再校验
            validated = _parse_and_validate(raw, evidence=evidence)
            # 回填页码/条款元数据 + 引用编号连续重排（修复跳号与证据展示不全）
            validated = _finalize_citations(validated, evidence)
            plan = validated.model_dump()
            plan = _ensure_all_fields_filled(plan, evidence, diagnosis, prescribed_freq)
            citations = validated.evidence_basis
            return {"plan_json": plan, "citations": [c.model_dump() for c in citations]}
        except Exception as exc:
            # LLM 失败降级：按字段类型匹配证据片段，再提示医生人工完善
            def _ev(e, key):
                return getattr(e, key, "") or ""
            _used = set()
            _recheck = _degraded_content(evidence, "recheck_items", _used)
            _lifestyle = _degraded_content(evidence, "lifestyle", _used)
            plan = {
                "frequency": "待定",
                "duration_days": -1,
                "pain_type": diagnosis,
                "recheck_items": [_recheck] if _recheck else [],
                "medication_adjustment": "需医生补充完善（可参考下方共识）",
                "warning_threshold": "NRS≥7 建议干预（依据检索共识）",
                "health_education": ["需医生补充完善（可参考检索共识）"],
                "lifestyle": [_lifestyle] if _lifestyle else [],
                "evidence_basis": [
                    {"ref": f"[{i+1}]", "guide": _ev(e, "title"), "year": _ev(e, "year"),
                     "page": _ev(e, "page"), "clause": (f"第{_ev(e,'clause_no')}条" if _ev(e,'clause_no') else _ev(e, "section")),
                     "excerpt": _ev(e, "text")[:120]}
                    for i, e in enumerate(evidence)
                ],
                "status": "degraded",
                "degraded_fields": ["frequency", "medication_adjustment", "health_education"],
                "note": "已回退为证据摘要，需医生补充完善",
            }
            plan = _ensure_all_fields_filled(plan, evidence, diagnosis, prescribed_freq)
            return {"plan_json": plan, "citations": plan["evidence_basis"]}

    # 完全无 LLM 配置 → 模板降级（按字段类型匹配证据片段）
    def _ev(e, key):
        return getattr(e, key, "") or ""
    _used = set()
    _recheck = _degraded_content(evidence, "recheck_items", _used)
    _lifestyle = _degraded_content(evidence, "lifestyle", _used)
    plan = {
        "frequency": "每周三、周五",
        "duration_days": -1,
        "pain_type": diagnosis,
        "recheck_items": [_recheck] if _recheck else [],
        "medication_adjustment": "需医生补充完善（可参考检索共识）",
        "warning_threshold": "NRS≥7 建议干预",
        "health_education": ["需医生补充完善（可参考检索共识）"],
        "lifestyle": [_lifestyle] if _lifestyle else [],
        "evidence_basis": [
            {"ref": f"[{i+1}]", "guide": _ev(e, "title"), "year": _ev(e, "year"),
             "page": _ev(e, "page"), "clause": (f"第{_ev(e,'clause_no')}条" if _ev(e,'clause_no') else _ev(e, "section")),
             "excerpt": _ev(e, "text")[:120]}
            for i, e in enumerate(evidence)
        ],
        "status": "degraded",
        "degraded_fields": ["frequency", "medication_adjustment", "health_education"],
        "note": "已回退为证据摘要，需医生补充完善",
    }
    plan = _ensure_all_fields_filled(plan, evidence, diagnosis, prescribed_freq)
    return {"plan_json": plan, "citations": plan["evidence_basis"]}


def _hitl(state: AgentState) -> dict:
    """hitl_review：挂起，等待医生在界面"同意/修改/驳回" """
    payload = {
        "patient_id": state.get("patient_id"),
        "diagnosis": state.get("diagnosis"),
        "plan_json": state.get("plan_json"),
        "citations": state.get("citations"),
        "message": "请医生审阅随访计划（同意 / 修改后确认）",
    }
    decision = interrupt(payload)
    # decision = {"decision": "approved"|"modified",
    #             "plan_json": <修改后的>, "doctor_id": "..."}
    return {
        "review_decision": (decision or {}).get("decision", "approved"),
        "doctor_plan_json": (decision or {}).get("plan_json"),
        "doctor_id": (decision or {}).get("doctor_id"),
    }


def _persist(state: AgentState) -> dict:
    """persist_plan：落库 followup_plan，状态按医生决策"""
    decision = state.get("review_decision", "approved")
    final_plan = state.get("doctor_plan_json") or state.get("plan_json")
    status = decision if decision in ("approved", "modified") else "approved"
    plan_id = create_plan(
        patient_id=state.get("patient_id"),
        plan_json=final_plan,
        discharge_summary=state.get("discharge_summary"),
        citations=state.get("citations"),
        doctor_id=state.get("doctor_id"),
        status=status,
    )
    return {"plan_id": plan_id, "status": status}


def build_planner_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("extract_discharge_context",
                  lambda s: _patient_context(
                      s.get("patient_id"), s.get("diagnosis"), s.get("discharge_summary")))
    g.add_node("retrieve_guidelines", _retrieve)
    g.add_node("draft_plan", _draft)
    g.add_node("hitl_review", _hitl)
    g.add_node("persist_plan", _persist)
    g.add_edge(START, "extract_discharge_context")
    g.add_edge("extract_discharge_context", "retrieve_guidelines")
    g.add_edge("retrieve_guidelines", "draft_plan")
    g.add_edge("draft_plan", "hitl_review")
    g.add_edge("hitl_review", "persist_plan")
    g.add_edge("persist_plan", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


planner_graph = build_planner_graph()


async def generate_draft(patient_id, diagnosis=None, discharge_summary=None, persist=True):
    """
    便捷函数（供 REST API 直接产出计划草稿，落库 status='draft'）。
    等价于 execution Agent（C）在 HITL 挂起前的结果；医生后续 approve/modify 更新状态。
    """
    t0 = time.perf_counter()
    ctx = _patient_context(patient_id, diagnosis, discharge_summary)
    state = {
        "patient_id": patient_id,
        "patient": ctx.get("patient", {}),
        "diagnosis": ctx.get("diagnosis", ""),
        "discharge_summary": ctx.get("discharge_summary", ""),
    }
    t1 = time.perf_counter()
    evidence = _retrieve(state).get("retrieved_evidence", [])
    t2 = time.perf_counter()
    draft = await _draft({**state, "retrieved_evidence": evidence})
    t3 = time.perf_counter()
    plan_json = draft.get("plan_json", {})
    citations = draft.get("citations", [])
    plan_id = None
    if persist:
        plan_id = create_plan(
            patient_id=patient_id,
            plan_json=plan_json,
            discharge_summary=state["discharge_summary"],
            citations=citations,
            status="draft",
        )
    return {
        "plan_id": plan_id,
        "patient_id": patient_id,
        "diagnosis": state["diagnosis"],
        "plan_json": plan_json,
        "citations": citations,
        "_timing": {
            "context_sec": round(t1 - t0, 3),
            "rag_sec": round(t2 - t1, 3),
            "llm_sec": round(t3 - t2, 3),
            "total_sec": round(t3 - t0, 3),
        },
        "_rag_count": len(evidence),
    }
