"""
自动回复引擎 — 为患者自动生成多轮自然语言回复
不修改数据库，所有回复在内存中生成
"""
import random
import json
from datetime import datetime


class AutoReplyEngine:
    """自动回复引擎：模拟35个患者的多轮自然语言回复"""

    def __init__(self, patients, reply_map, risk_engine):
        self.patients = patients  # 所有患者列表
        self.reply_map = reply_map  # 预置回复映射（从数据库读取）
        self.risk_engine = risk_engine

        # 多轮对话模板：按轮次组织
        self.round_templates = {
            # 第1轮：首次回复疼痛情况
            1: {
                "nrs_low": [
                    "今天感觉挺好的，大概{score}分吧，不怎么疼了。",
                    "还好，{score}分左右，比之前好多了。",
                    "今天不怎么疼，差不多{score}分，谢谢关心。",
                ],
                "nrs_mid": [
                    "今天{score}分，还是有感觉的，不过能忍受。",
                    "大概{score}分吧，没有太大变化。",
                    "还是{score}分，跟昨天差不多。",
                ],
                "nrs_high": [
                    "不太好，{score}分了，疼得挺厉害的。",
                    "今天{score}分，晚上睡觉都受影响，很难受。",
                    "{score}分，感觉比昨天还疼，真受不了。",
                ],
            },
            # 第2轮：回复睡眠情况
            2: {
                "sleep_good": [
                    "睡眠还行，昨晚睡了大概6个小时，中间没醒。",
                    "挺好的，一觉睡到天亮，比之前好多了。",
                ],
                "sleep_ok": [
                    "睡了5个小时左右，中间醒了一次，不过很快又睡着了。",
                    "一般吧，睡了4、5个小时，还行。",
                ],
                "sleep_bad": [
                    "睡得很差，一晚上醒了好几次，总共也就3、4个小时。",
                    "几乎没怎么睡着，疼得翻来覆去的。",
                ],
            },
            # 第3轮：回复用药情况
            3: {
                "med_yes": [
                    "按时吃了，早上和中午的都吃了，没落下。",
                    "都吃了，一天都没忘。",
                ],
                "med_partial": [
                    "早上的吃了，中午的差点忘了，后来补上了。",
                    "基本都按时吃了，就昨天晚上忘了一次。",
                ],
                "med_no": [
                    "今天没吃，感觉副作用太大了，有点恶心。",
                    "忘了吃，一忙就忘记了。",
                    "没吃，不想吃了，感觉没什么用还难受。",
                ],
            },
            # 第4轮：补充回答（副作用/其他）
            4: {
                "side_none": [
                    "没有不舒服的地方，都还好。",
                    "没什么特别的，就是正常的。",
                ],
                "side_yes": [
                    "有点恶心头晕，可能是药的副作用吧。",
                    "胃口不太好，吃不下饭，有时候还有点想吐。",
                    "这两天有点便秘，是不是药引起的？",
                ],
            },
        }

        # 患者个性信息：从历史数据推断
        self.patient_profiles = self._build_profiles()
        self._score_cache: dict[str, float] = {}  # 同会话内评分去重，避免 LLM 看到自相矛盾的评分

    def _build_profiles(self):
        """基于患者数据构建回复风格画像"""
        profiles = {}
        for p in self.patients:
            pid = p["patient_id"]
            history = p.get("history") or []
            nrs_list = [h.get("nrs_score", 5) for h in history if h.get("nrs_score") is not None]
            avg_nrs = sum(nrs_list) / len(nrs_list) if nrs_list else 5

            # 从预置回复中获取风险等级
            rdata = self.reply_map.get(pid, {})
            risk_tendency = rdata.get("risk_tendency", "medium_risk")

            profiles[pid] = {
                "patient_id": pid,
                "name": p["name"],
                "diagnosis": p.get("diagnosis", ""),
                "avg_nrs": round(avg_nrs, 1),
                "risk_tendency": risk_tendency,
                "nrs_trend": "improving" if len(nrs_list) >= 3 and nrs_list[-1] < nrs_list[-3] else "stable",
                "sleep_quality": random.choice(["good", "ok", "bad"]),
                "med_adherence": random.choice(["yes", "yes", "partial"]),
                "has_side_effects": random.random() < 0.2,
            }
        return profiles

    def generate_reply(self, patient_id, round_num, prev_reply=None):
        """为指定患者生成第 N 轮回复

        Args:
            patient_id: 患者ID
            round_num: 当前轮次（1-4）
            prev_reply: 上一轮护士的追问内容（影响回复方向）

        Returns:
            自然语言回复文本
        """
        profile = self.patient_profiles.get(patient_id)
        if not profile:
            return "今天还好，跟之前差不多。"

        if round_num == 1:
            return self._generate_round1(profile)
        elif round_num == 2:
            return self._generate_round2(profile)
        elif round_num == 3:
            return self._generate_round3(profile)
        elif round_num == 4:
            return self._generate_round4(profile)
        elif round_num == 5:
            return "都回答过了，没什么要补充的了，就这样吧。"
        else:
            return "没了没了，你说的这些我都回答过了呀，还有事吗？"

    def generate_opening_reply(self, patient_id):
        """开场白已一次性问了 ①②③（疼痛/睡眠/用药），这里返回对这三题的综合回复"""
        profile = self.patient_profiles.get(patient_id)
        if not profile:
            return "今天还好，跟之前差不多，药也按时吃了。"

        # 模糊患者：给出含糊但覆盖了三题的回复
        if profile.get("risk_tendency") == "ambiguous":
            return "还行吧，就那样，药好像也都吃了的。"

        pain = self._generate_round1(profile)
        sleep = self._generate_round2(profile)
        med = self._generate_round3(profile)
        return f"{pain} {sleep} {med}"

    def _generate_round1(self, profile):
        """第1轮：回复疼痛评分"""
        # 模糊回复患者：回复不明确的内容
        if profile.get("risk_tendency") == "ambiguous":
            fuzzy_replies = [
                "还行吧，就那样。",
                "差不多，跟之前一样。",
                "不知道怎么说，反正不太好。",
                "还好吧，没什么特别的感觉。",
            ]
            return random.choice(fuzzy_replies)

        avg = profile["avg_nrs"]
        # 同会话内去重：首次随机，后续复用，避免 LLM 看到自相矛盾的评分
        pid = profile.get("patient_id")
        if pid and pid in self._score_cache:
            score = self._score_cache[pid]
        else:
            score = round(avg + random.uniform(-1, 1), 1)
            score = max(0, min(10, score))
            score = int(score) if random.random() < 0.5 else score
            if pid:
                self._score_cache[pid] = score

        if score <= 3:
            category = "nrs_low"
        elif score <= 6:
            category = "nrs_mid"
        else:
            category = "nrs_high"

        templates = self.round_templates[1][category]
        text = random.choice(templates).format(score=score)
        return text

    def _generate_round2(self, profile):
        """第2轮：回复睡眠情况"""
        # 模糊患者：澄清后给出具体信息
        if profile.get("risk_tendency") == "ambiguous":
            return f"大概{random.randint(4,6)}分吧，睡得一般，{random.randint(4,6)}个小时左右。"

        quality = profile["sleep_quality"]
        templates = self.round_templates[2][f"sleep_{quality}"]
        return random.choice(templates)

    def _generate_round3(self, profile):
        """第3轮：回复用药情况"""
        # 模糊患者：澄清后给出具体信息
        if profile.get("risk_tendency") == "ambiguous":
            return random.choice(["药都按时吃了的。", "吃了吧，应该都吃了。"])

        adherence = profile["med_adherence"]
        templates = self.round_templates[3][f"med_{adherence}"]
        return random.choice(templates)

    def _generate_round4(self, profile):
        """第4轮：回复副作用/其他"""
        # 模糊患者：正常回复
        if profile.get("risk_tendency") == "ambiguous":
            return "没有什么特别不舒服的。"

        if profile["has_side_effects"]:
            templates = self.round_templates[4]["side_yes"]
        else:
            templates = self.round_templates[4]["side_none"]
        return random.choice(templates)

    def get_no_reply_patient(self, exclude_ids):
        """从患者中选一个作为三日未回复的异常患者（排除指定的患者ID）"""
        candidates = [p for p in self.patients if p["patient_id"] not in exclude_ids]
        if candidates:
            return random.choice(candidates)
        return None

    def get_demo_patients(self, existing_replies):
        """获取两个预留演示患者：一个高风险、一个模糊回复

        Args:
            existing_replies: 预置回复数据 {pid: {reply_text, risk_tendency}}

        Returns:
            (high_risk_patient, ambiguous_patient) 两个患者的ID
        """
        high_id = None
        ambig_id = None

        for pid, data in existing_replies.items():
            risk = data.get("risk_tendency", "")
            if risk == "high_risk" and high_id is None:
                high_id = pid
            elif risk == "ambiguous" and ambig_id is None:
                ambig_id = pid

        # 如果没找到，随便选两个
        all_pids = list(existing_replies.keys())
        if high_id is None and len(all_pids) > 0:
            high_id = all_pids[0]
        if ambig_id is None and len(all_pids) > 1:
            ambig_id = all_pids[1]
        elif ambig_id is None and len(all_pids) > 0:
            ambig_id = all_pids[0]

        return high_id, ambig_id
