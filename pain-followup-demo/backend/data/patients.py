"""
Mock 患者数据生成器 — 200个模拟患者
"""
import json
import random
from datetime import datetime, timedelta

# 姓氏列表
SURNAMES = [
    "张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "马", "朱", "胡", "郭", "何", "高", "林", "罗",
    "郑", "梁", "谢", "宋", "唐", "韩", "冯", "于", "董", "萧",
    "程", "曹", "袁", "邓", "许", "傅", "沈", "曾", "彭", "吕",
    "苏", "卢", "蒋", "蔡", "贾", "丁", "魏", "薛", "叶", "阎"
]

# 名字列表
GIVEN_NAMES = [
    "建国", "爱华", "志强", "秀英", "伟", "芳", "娜", "敏", "静", "丽",
    "强", "磊", "军", "洋", "勇", "艳", "杰", "涛", "明", "超",
    "秀兰", "桂英", "秀珍", "玉兰", "桂芳", "秀云", "淑珍", "淑芳", "玉珍", "秀梅",
    "国栋", "志明", "文博", "思远", "浩然", "子涵", "宇轩", "梓豪", "一鸣", "天佑",
    "雨桐", "诗涵", "欣怡", "若溪", "紫萱", "语嫣", "晓萌", "佳琪", "梦瑶", "思琪",
    "建平", "志伟", "文华", "明哲", "俊杰", "家豪", "子轩", "昊天", "泽宇", "鹏飞",
    "慧芳", "美玲", "雅琴", "婉婷", "晓燕", "雪梅", "丽华", "瑞雪", "秋月", "春梅",
    "振华", "兴国", "卫国", "保国", "爱民", "志国", "建新", "新华", "国强", "富民"
]

# 诊断类型
DIAGNOSES = [
    "带状疱疹后神经痛",
    "腰椎术后疼痛",
    "糖尿病周围神经痛",
    "癌性疼痛"
]

# 疼痛类型
PAIN_TYPES = [
    "神经病理性疼痛",
    "伤害感受性疼痛",
    "混合性疼痛",
    "中枢性疼痛"
]

# 医生列表
DOCTORS = [
    {"id": "D001", "name": "李医生"},
    {"id": "D002", "name": "王医生"},
    {"id": "D003", "name": "陈医生"},
    {"id": "D004", "name": "刘医生"},
    {"id": "D005", "name": "赵医生"},
]

# 睡眠质量选项
SLEEP_QUALITIES = ["好", "一般", "差", "很差"]

# 免随访患者硬编码
SKIP_PATIENTS = {
    "P20240005": "随访周期已结束",
    "P20240012": "昨日已复诊",
    "P20240023": "患者请假",
    "P20240031": "已安排住院",
    "P20240044": "随访周期已结束",
    "P20240058": "昨日已复诊",
    "P20240067": "患者请假",
    "P20240073": "随访周期已结束",
    "P20240089": "昨日已复诊",
    "P20240101": "已安排住院",
    "P20240118": "随访周期已结束",
    "P20240134": "患者请假",
    "P20240152": "昨日已复诊",
    "P20240178": "随访周期已结束",
}


def random_name():
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


def random_date_in_range(start_days=30, end_days=90):
    """生成随机的出院日期（在当前日期前30-90天）"""
    today = datetime.now()
    days_ago = random.randint(start_days, end_days)
    return (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def generate_history(days=7):
    """为患者生成历史随访记录"""
    history = []
    today = datetime.now()
    for i in range(days, 0, -1):
        date = today - timedelta(days=i)
        nrs = random.randint(1, 8)
        sleep = random.choice(SLEEP_QUALITIES)
        med = random.random() > 0.15  # 85% 按时用药

        reply_texts = [
            f"今天大概{nrs}分吧，睡眠{sleep}，药{'按时吃了' if med else '忘了吃'}",
            f"{nrs}分，{'睡得还行' if sleep in ['好', '一般'] else '没睡好'}",
            f"今天{'还不错' if nrs <= 3 else '还是疼' if nrs <= 5 else '疼得厉害'}，大概{nrs}分",
        ]

        history.append({
            "date": date.strftime("%Y-%m-%d"),
            "nrs_score": nrs,
            "sleep_quality": sleep,
            "medication_taken": med,
            "reply_text": random.choice(reply_texts)
        })
    return history


def generate_patients():
    """生成200个模拟患者"""
    patients = []
    used_names = set()

    for i in range(1, 201):
        pid = f"P2024{i:05d}"

        # 确保名字不重复
        name = random_name()
        while name in used_names:
            name = random_name()
        used_names.add(name)

        is_skip = pid in SKIP_PATIENTS
        doctor = random.choice(DOCTORS)
        diagnosis = random.choice(DIAGNOSES)
        age = random.randint(35, 80)
        discharge_date = random_date_in_range()
        history = generate_history(random.randint(3, 7))

        patients.append({
            "patient_id": pid,
            "name": name,
            "age": age,
            "diagnosis": diagnosis,
            "discharge_date": discharge_date,
            "doctor_id": doctor["id"],
            "doctor_name": doctor["name"],
            "skip_follow_up": is_skip,
            "skip_reason": SKIP_PATIENTS.get(pid),
            "follow_up_plan": {
                "frequency": "每日",
                "duration_days": 90,
                "pain_type": random.choice(PAIN_TYPES)
            },
            "history": history,
            "consecutive_no_reply_days": 0
        })

    return patients


if __name__ == "__main__":
    patients = generate_patients()
    with open("patients.json", "w", encoding="utf-8") as f:
        json.dump(patients, f, ensure_ascii=False, indent=2)
    print(f"生成了 {len(patients)} 个患者数据")
