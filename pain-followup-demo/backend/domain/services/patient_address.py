"""生成随访对话中的自然患者称呼，避免直接使用患者全名。"""
from __future__ import annotations

import re

_COMPOUND_SURNAMES = {
    "欧阳", "司马", "上官", "诸葛", "东方", "独孤", "南宫", "尉迟",
    "皇甫", "令狐", "慕容", "夏侯", "长孙", "宇文", "轩辕", "公孙",
}


def build_patient_address(name: str = "", age=None, gender: str = "") -> str:
    """按年龄和性别生成日常称呼。

    年龄未满 40 岁使用“女士/先生”；40 岁及以上使用“阿姨/叔叔”；
    年龄或性别不明确时使用“老师”。
    """
    clean_name = "".join(str(name or "").split())
    if not clean_name:
        return "您"

    surname = clean_name[:2] if clean_name[:2] in _COMPOUND_SURNAMES else clean_name[0]
    normalized_gender = str(gender or "").strip().lower()
    is_female = normalized_gender in {"女", "女性", "female", "f", "woman"}
    is_male = normalized_gender in {"男", "男性", "male", "m", "man"}

    try:
        numeric_age = int(age) if age is not None else None
    except (TypeError, ValueError):
        numeric_age = None

    if numeric_age is not None and numeric_age < 40:
        if is_female:
            return f"{surname}女士"
        if is_male:
            return f"{surname}先生"
        return f"{surname}老师"

    if numeric_age is not None and numeric_age >= 40:
        if is_female:
            return f"{surname}阿姨"
        if is_male:
            return f"{surname}叔叔"
        return f"{surname}老师"

    return f"{surname}老师"


def sanitize_patient_address(text: str, name: str = "", address: str = "") -> str:
    """替换患者全名及错误性别称谓，保证对话中使用统一称呼。"""
    content = str(text or "").strip()
    clean_name = "".join(str(name or "").split())
    replacement = address or build_patient_address(clean_name)
    if clean_name and replacement and clean_name != replacement:
        content = re.sub(
            rf"{re.escape(clean_name)}(?:女士|先生|小姐|阿姨|叔叔|老师)?",
            replacement,
            content,
        )
        surname = (
            clean_name[:2]
            if clean_name[:2] in _COMPOUND_SURNAMES
            else clean_name[0]
        )
        if surname:
            # LLM 可能只输出“陈先生/陈阿姨”，没有带患者全名，
            # 因此还要校正姓氏后的性别称谓。
            content = re.sub(
                rf"{re.escape(surname)}(?:女士|先生|小姐|女生|男生|阿姨|叔叔|老师)",
                replacement,
                content,
            )
    return content


def remove_leading_patient_address(text: str, address: str = "") -> str:
    """移除追问/告别语开头重复的称呼，开场白仍保留称呼。"""
    content = str(text or "").strip()
    if not address or address == "您":
        return content
    return re.sub(
        rf"^{re.escape(address)}(?:您好|你好)?[，,：:\s]*",
        "",
        content,
        count=1,
    ).strip()
