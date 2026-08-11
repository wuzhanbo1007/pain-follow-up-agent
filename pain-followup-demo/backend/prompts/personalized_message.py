"""个性化随访消息生成提示词（慢性疼痛）

- 开场白带"趋势感知"（好转/恶化/波动/首次），统一风险标签映射
- 告别语覆盖副作用提醒、用药忘服提醒等具体场景
- 语气引导用语义示例而非形容词；空历史优雅降级
"""

from typing import Optional


def _risk_tendency_label(risk_tendency: str) -> str:
    """统一风险倾向 → 中文标签映射"""
    mapping = {
        "high_risk":    "高风险（病情较重或恶化趋势，需格外关切）",
        "low_risk":     "低风险（病情平稳或好转，可多鼓励）",
        "medium_risk":  "中风险（保持关注，常规随访）",
        "ambiguous":    "信息模糊（需引导具体描述）",
        "improving":    "好转趋势（表达鼓励和肯定）",
        "worsening":    "恶化趋势（表达关切但不引起恐慌）",
        "stable":       "平稳趋势（保持常规关心）",
        "first_time":   "首次随访（建立信任，温和引导）",
    }
    return mapping.get(risk_tendency, "一般风险")


def _tone_hint(risk_tendency: str) -> str:
    """风险倾向 → 语气行为语义指导（用语义而非形容词）"""
    hints = {
        "high_risk":
            "像一位真诚担心的护士：表达关切（'一直惦记着您，有点担心您的情况'），"
            "不催促、不制造恐慌，让患者感到被认真对待。",
        "low_risk":
            "像一位为患者高兴的护士：先表达鼓励（'看到您恢复得不错，真为您高兴'），"
            "语气轻快温暖，鼓励继续保持，但不用复述具体评分。",
        "improving":
            "捕捉到好转迹象并表达真诚喜悦，让患者感到恢复被看见，但不用复述具体评分。",
        "worsening":
            "表达真实关切但不制造恐慌，强调陪伴和持续关注，不让患者觉得被放弃。",
        "first_time":
            "像一个初次见面的护士，温暖地介绍自己并建立信任，不要太亲昵但要有温度。",
    }
    return hints.get(risk_tendency, "像一位日常随访的护士，亲切自然地询问恢复情况。")


# ====================================================================
# 旧版合并生成（保留兼容）
# ====================================================================

# ====================================================================
# 新版纯开场白（当前随访流程实际使用；以自然问句结尾，让患者愿意回应）
# ====================================================================

def build_greeting_prompt(patient: dict, days_since_discharge: int,
                          history_summary: str, risk_tendency: str = "medium_risk") -> str:
    """构建开场白生成提示词

    与 build_prompt（旧版，开场白+问题一起生成，已无调用方）的区别：
    本函数只输出个性化问候与关怀，结尾用一句自然问句邀请患者回应。
    """

    risk_label = _risk_tendency_label(risk_tendency)
    tone = _tone_hint(risk_tendency)

    return f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访开场白。

## 患者信息
- 姓名：{patient['name']}
- 年龄：{patient['age']}
- 诊断：{patient['diagnosis']}
- 医院：{patient.get('hospital_name', '协和医院')}
- 主治医生：{patient.get('doctor_name', '随访医生')}
- 科室：{patient.get('department_name', '疼痛科')}
- 出院天数：第 {days_since_discharge} 天
- 疼痛类型：{patient.get('follow_up_plan', {}).get('pain_type', '未知')}
- 风险倾向：{risk_label}

## 历史数据
{history_summary if history_summary else '（首次随访，无历史数据）'}

## 语气行为指导
{tone}

## 生成要求
1. 根据年龄选择称呼：年轻用"您"，年长（>50岁）可用"阿姨""叔叔"等亲切称呼
2. 结构固定（前段按自我介绍-就医背景-来意）：
   ① 称呼患者（"××您好！"）
   ② 自我介绍（"我是{{医院}}{{科室}}的医生{{主治医生}}。"）——医院用上方的「医院」，科室用上方的「科室」，医生用上方的「主治医生」
   ③ 就医背景（"您此前因{{诊断}}在我院就诊/住院"）——用上方的「诊断」
   ④ 说明来意（"今天按随访计划跟您沟通一下恢复情况"）
   ⑤ 一句人文关怀
   ⑥ 以一句自然的问句结尾，询问患者今天的情况，让患者有回答的意愿
3. 人文关怀要具体不空洞：表达惦记与牵挂（如"一直惦记着您""特意来问候您""很关心您恢复得怎么样"），
   让患者感到被关心，而不是复述数据
4. 不要引用历史评分/睡眠等具体数据——关心的是"现在的您"，复述数据反而显得机械
5. 根据风险/趋势微调语气：
   - 高风险/恶化 → 真诚关切（"有点担心您的情况"），不制造恐慌
   - 好转/低风险 → 温暖鼓励（"真为您高兴"）
   - 平稳/首次 → 自然关心
6. 控制在 70~100 字，简洁但有温度
7. 结尾必须是问句：用自然的口语化问句询问今天状况（如"今天感觉怎么样呀？""今天身体感觉如何？""今天情况还好吗？"），
   避免机械重复同一个问句，让患者愿意回应
8. 不要使用表情符号，用自然有温度的文字传达情感

## 输出格式
只返回开场白文本，不要加引号或标记。"""


# ====================================================================
# 个性化告别语
# ====================================================================

def build_farewell_prompt(patient: dict, risk_level: str, nrs_score,
                          sleep_quality: Optional[str], medication_status: Optional[str],
                          side_effects: Optional[str] = None,
                          risk_tendency: str = "medium_risk") -> str:
    """构建个性化告别语提示词

    新增 side_effects 参数，让告别语能针对副作用给出具体叮嘱。

    Args:
        patient: 患者信息
        risk_level: 风险等级 high_risk / low_risk / medium_risk
        nrs_score: 本次疼痛评分
        sleep_quality: 本次睡眠质量
        medication_status: 本次用药情况
        side_effects: 本次报告的副作用（"无"/"否认"/具体症状字符串/None）
        risk_tendency: 风险趋势

    Returns:
        完整的 prompt 字符串
    """

    risk_label = _risk_tendency_label(risk_level)
    tendency_hint = _tone_hint(risk_tendency)

    nrs_str = str(nrs_score) if nrs_score is not None else "未提及"
    sleep_str = sleep_quality or "未提及"
    med_str = medication_status or "未提及"
    se_str = side_effects or "未提及"

    return f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访告别语。

## 患者信息
- 姓名：{patient['name']}
- 年龄：{patient['age']}
- 诊断：{patient['diagnosis']}
- 疼痛类型：{patient.get('follow_up_plan', {}).get('pain_type', '未知')}

## 本次随访情况
- 疼痛评分（NRS）：{nrs_str} 分（0-10分）
- 睡眠情况：{sleep_str}
- 用药情况：{med_str}
- 副作用情况：{se_str}
- 风险评估：{risk_label}

## 语气行为指导
{tendency_hint}

## 生成要求

1.【回应患者本次反馈】先对患者今天说的内容做简短回应，让患者感觉你说的话是基于她今天的情况，不是套模板。
   · 如果 NRS ≤ 3：表达高兴和鼓励，"看到您疼痛控制得很好，真为您高兴"
   · 如果 NRS 4-6：表达关心，"疼痛还在，但我们在关注，多注意休息"
   · 如果 NRS ≥ 7：表达格外关切，"已经记下来了，会反馈给医生，您别太担心"
   · 如果 NRS 未提及：自然跳过，不强行提及

2.【睡眠反馈】如果睡眠一般/差/很差 → "睡眠不太好的话，白天可以适当活动，晚上更容易入睡"
   如果睡眠好 → 简单肯定即可

3.【用药叮嘱】如果 medication=false → "药还是要按时吃的，忘记一次没关系，但不要连续忘"
   如果 medication=partial → "尽量每天同一时间服药，设个闹钟会有帮助"
   如果 medication=true → 简单肯定即可

4.【副作用叮嘱】如果副作用是具体症状 → 根据症状给简短关怀/建议
   如"便秘可以多喝温水、适当吃些蔬菜水果"
   如"头晕的话起身时慢一点，防止摔倒"
   如果副作用="无" → 不提
   如果副作用="否认" → 不提（系统后续会单独追问）

5.【收尾】自然温暖，像护士告别时的叮嘱。让患者感到安心和被关怀。

6.【格式约束】
   - 不要使用表情符号
   - 控制在 50~90 字
   - 绝对不要提问（这是告别语）
   - 不要用"呢""哦""呀"等过于口语化的语气词

## 输出格式
只返回告别语文本，不要加引号或标记。"""
