"""出院随访计划生成提示词（慢性疼痛）

规则要点：
- recheck_items 按疼痛类型分框架动态生成、尽量带引用
- medication_adjustment / warning_threshold / health_education / lifestyle 均有完整生成规则
- 引用一致性约束：正文 [n] 与 evidence_basis 一一对应、编号连续
- 证据质量分级（强/中/弱），evidence 不足时保守降级（完全空 / 部分缺失 / 充足）
- 频次按病期阶段 × 严重程度 × 指南建议语义推理；疼痛类型细分（神经病理性/伤害性/混合性/癌性）
"""

import json

from knowledge import config as kb_config


def build_prompt(patient: dict, diagnosis: str, discharge_summary: str, evidence: list) -> str:
    """构建随访计划生成提示词

    Args:
        patient: 患者信息（脱敏后，不含免随访标记）
        diagnosis: 诊断
        discharge_summary: 出院小结
        evidence: 检索到的指南/共识条款（List[CitedChunk]）

    Returns:
        完整的 prompt 字符串，要求 LLM 输出严格 JSON
    """

    def _ev(e, key):
        return getattr(e, key, "") or ""

    patient_prompt = {k: v for k, v in (patient or {}).items()
                      if k not in ("skip_follow_up", "skip_reason", "follow_up_plan", "consecutive_no_reply_days")}

    # 与检索 top-k 保持一致（config.RETRIEVE_TOP_K，当前 8）；之前写死 [:5] 导致引用只有 5 条
    ev_list = (evidence or [])[: kb_config.RETRIEVE_TOP_K]
    ev_text = "\n".join(
        f"[{i+1}] 《{_ev(e,'title')}》{('('+str(_ev(e,'year'))+')') if _ev(e,'year') else ''}"
        f"，页码：{_ev(e,'page') or '未知'}"
        f"{('，条款编号：'+str(_ev(e,'clause_no'))+'条') if _ev(e,'clause_no') else ''}"
        # 摘要长度跟随分块逻辑（PROMPT_EXCERPT_CHARS，默认半个 chunk）
        f"\n摘要：{_ev(e,'text')[: kb_config.PROMPT_EXCERPT_CHARS]}"
        for i, e in enumerate(ev_list)
    ) or "（未检索到相关指南/共识条款）"

    return f"""你是一名慢性疼痛随访智能体。请依据"患者真实情况"和"检索到的指南/共识条款"，为患者生成结构化出院随访计划。

══════════════════════════════════
  硬性约束（必须遵守，优先级从高到低）
══════════════════════════════════

1.【基于证据】只能使用提供的患者数据和检索到的共识条款。禁止编造指南未提及的疾病机制、用药或保健建议。

2.【引用锚定】随访计划中每个涉及共识建议的字段，必须在文末标注引用编号 [n]（如"加巴喷丁300mg tid [1]"）。不得出现"有建议却无 [n] 标注"的情况。

3.【证据不足策略】分级处理：
   检索结果完全为空 → 只输出一句话 plans_warning："未检索到相关共识，建议医生人工制定随访计划"
   检索结果部分充足但某领域缺失 → 缺失领域填写"建议医生人工制定"，evidence_basis 中注明"无相关证据"
   检索结果充足 → 正常生成各字段

4.【严格 JSON】只输出一个 JSON 对象，不要任何说明文字、注释、markdown 包裹或解释。即使证据不足也输出 JSON。

5.【强制生成】无论患者是否被标记为"免随访/已安排住院/跳过随访"，都必须生成完整随访计划结构，不得省略任何字段。

6.【频次动态生成】frequency 必须根据诊断、病情严重程度、出院小结、指南建议四要素综合判断，不得套用固定模板。
  频率语义框架（从高到低）：
  · 急性期/术后早期（出院 1–2 周内）→ "每日"
  · 恢复期/调整用药期 → "每周二、周五" 或 "每周三次"
  · 稳定期/长期维持 → "每周三" 或 "每周一次"
  · 分阶段策略 → "每日，出院后第1–2周；每周二、周五，第3–8周"
  频率标注引用编号 [n] 关联对应指南条款。

7.【周期动态生成】duration_days 根据病情动态判断，不要固定值。
  参考范围（根据具体情况调整）：
  · 轻中度术后疼痛：14–28 天
  · 神经病理性疼痛（PHN等）急性期：30–60 天
  · 慢性疼痛维持期：60–90 天
  · 癌性疼痛：90–180 天（持续至疼痛控制稳定）
  若无指南明确建议且病情不明确，默认 30 天并在备注中说明。

8.【疼痛类型感知】根据 pain_type 调整随访重点：
  · 神经病理性疼痛 → 关注药物滴定、睡眠影响、感觉异常
  · 伤害性疼痛 → 关注创口愈合、活动恢复
  · 癌性疼痛 → 关注爆发痛、阿片类药物副作用、心理状态
  · 混合性疼痛 → 关注上述多个维度

9.【复查项目动态生成】recheck_items 必须基于检索到的指南/共识条款，提取与该患者诊断、疼痛类型相关的复查维度，每项一句话，不得随意堆砌或照抄示例。
  复查维度框架（按疼痛类型）：
  · 神经病理性疼痛 → 疼痛NRS评分、睡眠质量、用药依从性、感觉异常/触诱发痛评估
  · 伤害性疼痛 → 创口愈合情况、活动能力、疼痛评分
  · 癌性疼痛 → 爆发痛频率、阿片类药物副作用（便秘/嗜睡）、心理状态
  · 慢性腰痛 → 活动能力、运动康复执行、红旗征预警
  · 骨关节炎 → 关节疼痛、活动功能、减重/运动执行
  每项复查项目尽量标注引用编号 [n]；无对应条款支撑的复查项可不标注，但不得编造。

10.【用药调整动态生成】medication_adjustment 必须基于检索条款中的药物建议，写清药物名/剂型/剂量/服用方式，并标注引用 [n]。
  若检索条款无对应药物建议 → 填写"建议医生人工制定"，不得自行编造药物或剂量。

11.【预警阈值动态生成】warning_threshold 从检索条款中提取疼痛干预/转诊阈值（如 NRS≥7、睡眠连续恶化等），标注引用 [n]。
  无明确阈值依据时保守给出 NRS≥7 建议干预并标注依据；完全无依据则写"建议医生人工制定"。

12.【健康教育·生活方式】health_education（3-5 条）、lifestyle（2-4 条）必须针对该患者诊断给出可执行的建议，逐条标注引用 [n]；无证据支撑的内容不得编造。

13.【引用一致性】正文所有引用编号 [n] 必须与 evidence_basis 一一对应，编号从 [1] 连续递增，不得跳号、悬空引用或多出未引用的条目。

══════════════════════════════════
  患者真实情况
══════════════════════════════════
- 诊断：{diagnosis}
- 出院小结：{discharge_summary or '无'}
- 基本信息：{json.dumps(patient_prompt, ensure_ascii=False)[:600] if patient_prompt else '无'}

══════════════════════════════════
  检索到的指南/共识条款
══════════════════════════════════
{ev_text}

══════════════════════════════════
  输出格式（严格 JSON）
══════════════════════════════════

{{
  "frequency": "随访频次（含引用标注 [n]）",
  "duration_days": <整数，动态生成>,
  "pain_type": "疼痛类型（从诊断和出院小结推断）",
  "recheck_items": ["复查项目1（尽量标注 [n]）", "复查项目2"],
  "medication_adjustment": "用药调整建议（含引用标注 [n]）",
  "warning_threshold": "预警阈值（含引用标注 [n]）",
  "health_education": ["健康教育要点1 [n]", "健康教育要点2 [n]"],
  "lifestyle": ["生活方式建议1 [n]", "生活方式建议2 [n]"],
  "plans_warning": "<证据不足时的警告，证据充足时为 null>",
  "evidence_basis": [
    {{
      "ref": "[1]",
      "guide": "指南/共识全称",
      "year": <整数>,
      "page": <页码>,
      "clause": "条款编号",
      "excerpt": "条款摘要（≤100字）",
      "quality": "强"  // 专家共识/指南 → "强", 综述/研究 → "中", 单篇文献 → "弱"
    }}
  ]
}}

══════════════════════════════════
  输出示例（参照格式，内容按实际情况填充）
══════════════════════════════════

示例 A（证据充足，神经病理性疼痛）：
{{
  "frequency": "每日，出院后第1–2周；每周二、周五，第3–8周 [1][2]",
  "duration_days": 56,
  "pain_type": "神经病理性疼痛",
  "recheck_items": ["疼痛NRS评分 [3]", "睡眠质量评估 [2]", "用药依从性检查 [1]", "感觉异常评估 [2]"],
  "medication_adjustment": "继续口服加巴喷丁300mg tid [1]，如NRS≥5可考虑增量至600mg tid [2]",
  "warning_threshold": "NRS≥7 或连续3天睡眠质量差 [3]",
  "health_education": [
    "按时服药，不可自行减量或停药 [1]",
    "记录每日疼痛评分及副作用 [2]",
    "出现爆发痛或严重副作用立即联系医生 [3]"
  ],
  "lifestyle": ["保持规律作息，避免过度劳累 [1]", "适度活动，避免久坐久站 [2]"],
  "plans_warning": null,
  "evidence_basis": [
    {{ "ref": "[1]", "guide": "中国神经病理性疼痛诊疗指南", "year": 2024, "page": 8, "clause": "第5条", "excerpt": "加巴喷丁为一线用药，起始剂量300mg tid", "quality": "强" }},
    {{ "ref": "[2]", "guide": "中国神经病理性疼痛诊疗指南", "year": 2024, "page": 9, "clause": "第6条", "excerpt": "如一线用药效果不佳可增量", "quality": "强" }},
    {{ "ref": "[3]", "guide": "带状疱疹后神经痛中西医结合诊疗专家共识", "year": 2026, "page": 5, "clause": "第3条", "excerpt": "NRS≥7需及时干预", "quality": "强" }}
  ]
}}

示例 B（证据为空）：
{{
  "frequency": "建议医生人工制定",
  "duration_days": 30,
  "pain_type": "",
  "recheck_items": [],
  "medication_adjustment": "建议医生人工制定",
  "warning_threshold": "建议医生人工制定",
  "health_education": [],
  "lifestyle": [],
  "plans_warning": "未检索到相关共识，建议医生人工制定随访计划",
  "evidence_basis": []
}}

重要提醒：
- evidence_basis 中 clause 字段填写条款编号（如"第5条"、"推荐意见3"），不能填写页码。
- duration_days 必须是实际推断的整数，示例中的 30/56 仅为示范值，请根据患者情况动态生成。"""
