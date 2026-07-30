"""个性化随访消息生成提示词（慢性疼痛 V10.0）

V10.0 升级：
- 统一风险标签与趋势感知映射，消除三处重复逻辑
- 开场白新增"趋势感知"维度（好转/恶化/波动/首次），让 LLM 感知病情变化方向
- 告别语新增副作用提醒 + 用药忘服提醒等具体场景
- 所有语气引导从"形容词"升级为"语义示例"，让 LLM 真正理解"温暖"的含义
- 空历史优雅降级
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
            "像一个真正担心的护士：先说'看到您这几天的评分，我有点担心'，再问情况。"
            "不催促，不轻描淡写，让患者感觉有人在认真对待她的痛苦。",
        "low_risk":
            "像一个为患者高兴的护士：先说'看到您恢复得不错，真为您高兴'，再问今天的情况。"
            "语气可以轻快温暖，鼓励继续保持。",
        "improving":
            "捕捉到好转迹象并表达真诚喜悦：'看到您比之前好多了，真为您开心'。"
            "让患者感受到她努力恢复的过程被看见了。",
        "worsening":
            "表达真实关切但不制造恐慌：'看起来这几天不太舒服，我来帮您看看'。"
            "强调陪伴和持续关注，不要让患者觉得被放弃。",
        "first_time":
            "像一个初次见面的护士，温暖地介绍自己并建立信任：'我是负责您术后恢复随访的护士'。"
            "不要太亲昵，但要有温度。",
    }
    return hints.get(risk_tendency, "像一位日常随访的护士，亲切自然地询问恢复情况。")


# ====================================================================
# 旧版合并生成（保留兼容）
# ====================================================================

def build_prompt(patient: dict, days_since_discharge: int,
                 history_summary: str, risk_tendency: str = "medium_risk") -> str:
    """构建个性化随访消息生成提示词（包含开场白+问题，旧版兼容）

    Args:
        patient: 患者信息（含 name, age, diagnosis, follow_up_plan.pain_type）
        days_since_discharge: 出院天数
        history_summary: 近期历史数据摘要（含趋势分析）
        risk_tendency: 风险倾向

    Returns:
        完整的 prompt 字符串
    """
    risk_label = _risk_tendency_label(risk_tendency)
    tone = _tone_hint(risk_tendency)

    return f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访消息。

## 患者信息
- 姓名：{patient['name']}
- 年龄：{patient['age']}
- 诊断：{patient['diagnosis']}
- 出院天数：第 {days_since_discharge} 天
- 疼痛类型：{patient.get('follow_up_plan', {}).get('pain_type', '未知')}
- 风险倾向：{risk_label}

## 历史数据
{history_summary if history_summary else '（首次随访，无历史数据）'}

## 语气行为指导
{tone}

## 生成要求
1. 根据年龄选择称呼：年轻用"您"，年长（>50岁）可用"阿姨""叔叔"
2. 必须引用患者的具体历史数据（评分趋势、睡眠变化、用药情况），用数据体现"我一直记得您的情况"
3. 无历史数据时，表达初次见面的温暖，介绍自己并说明来意
4. 风险倾向为「高风险」时，语气格外关切但不制造恐慌
5. 风险倾向为「低风险/好转」时，表达为患者高兴
6. 必须包含三个标准问题：①今天疼痛几分（0-10）②昨晚睡眠怎样 ③药按时吃了吗
7. 控制在 80~130 字
8. 不要使用表情符号，用自然文字传达温度，不要用"呢""哦""呀"等过于口语化的语气词

## 输出格式
只返回消息文本，不要加引号或标记。"""


# ====================================================================
# 新版纯开场白（不含提问，问题由 Service 层逐条发送）
# ====================================================================

def build_greeting_prompt(patient: dict, days_since_discharge: int,
                          history_summary: str, risk_tendency: str = "medium_risk") -> str:
    """构建纯开场白生成提示词

    与 build_prompt 的区别：本函数只输出个性化问候与关怀，不含任何标准问题。
    """

    risk_label = _risk_tendency_label(risk_tendency)
    tone = _tone_hint(risk_tendency)

    return f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访开场白。

## 患者信息
- 姓名：{patient['name']}
- 年龄：{patient['age']}
- 诊断：{patient['diagnosis']}
- 出院天数：第 {days_since_discharge} 天
- 疼痛类型：{patient.get('follow_up_plan', {}).get('pain_type', '未知')}
- 风险倾向：{risk_label}

## 历史数据
{history_summary if history_summary else '（首次随访，无历史数据）'}

## 语气行为指导
{tone}

## 生成要求
1. 根据年龄选择称呼：年轻用"您"，年长（>50岁）可用"阿姨""叔叔"等亲切称呼
2. 语气亲切温暖，像一位真心惦记着患者的护士在说话
3. 必须引用患者的具体历史数据，用数据体现"我一直记得您的恢复情况"：
   不要说"希望您一切顺利"（空洞），要说"昨天您说疼痛已经降到3分了，真为您高兴"（具体）
4. 如果历史数据显示情况好转 → 表达真诚的喜悦和鼓励
5. 如果历史数据显示情况恶化 → 表达真切的关心但不引起恐慌，强调会持续关注
6. 无历史数据 → 温暖自介 + 说明来意 + 建立信任
7. 禁止包含任何提问——只做开场问候和关怀，问题由系统后续单独发送
8. 控制在 50~100 字，简短但有温度
9. 不要使用表情符号，用自然有温度的文字传达情感。避免"呢""哦""呀"等过于口语化的语气词

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

    V10.0 新增 side_effects 参数，让告别语能针对副作用给出具体叮嘱。

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
