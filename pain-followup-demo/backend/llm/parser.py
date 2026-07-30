"""
LLM 调用封装 — 回复解析
支持 OpenAI 兼容接口（DeepSeek / GPT 等），配置从 config.py 读取
LLM 异常（超时/网络错误）自动降级到关键词匹配，不阻塞流程
"""
import json
import re
import time

from core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT
from llm.client import is_llm_available
from llm.model import achat_completion
from prompts.reply_parsing import build_prompt as build_parsing_prompt


class ReplyParser:
    """用 LLM 解析患者自然语言回复"""

    def __init__(self):
        self.api_key = LLM_API_KEY
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL
        self.use_llm = is_llm_available()
        self.timeout = LLM_TIMEOUT

        # 最近一次 LLM 调用状态
        self.last_llm_status = None

        # LLM 调用统一走 llm.model.achat_completion（ChatOpenAI.ainvoke，底层 AsyncOpenAI）

    async def parse(self, reply_text, history_context="", force_keyword=False):
        """解析回复文本
        force_keyword=True 时强制使用关键词解析（绕过 LLM，用于批量场景）
        返回统一数据结构，包含 llm_status 字段标记是否降级
        """
        if self.use_llm and not force_keyword:
            result, llm_status = await self._safe_llm_parse(reply_text, history_context)
        else:
            result = self._keyword_parse(reply_text)
            llm_status = {
                "success": False,
                "fallback_used": True,
                "reason": "force_keyword" if force_keyword else "llm_unavailable",
                "elapsed_ms": 0,
            }

        result["llm_status"] = llm_status
        self.last_llm_status = llm_status
        return result

    async def _safe_llm_parse(self, reply_text, history_context=""):
        """带超时和异常捕获的 LLM 解析——失败时降级到关键词"""
        start = time.time()
        try:
            result = await self._llm_parse(reply_text, history_context)
            elapsed = time.time() - start
            return result, {
                "success": True,
                "fallback_used": False,
                "elapsed_ms": int(elapsed * 1000),
                "reason": None,
            }
        except Exception as e:
            elapsed = time.time() - start
            is_timeout = elapsed >= self.timeout or "timeout" in str(e).lower()
            fallback = self._keyword_parse(reply_text)
            return fallback, {
                "success": False,
                "fallback_used": True,
                "elapsed_ms": int(elapsed * 1000),
                "reason": "timeout" if is_timeout else str(e),
            }

    async def _llm_parse(self, reply_text, history_context=""):
        prompt = build_parsing_prompt(reply_text, history_context)

        # 统一走 llm.model.achat_completion（await ChatOpenAI.ainvoke）
        content = (await achat_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )).strip()

        if not content:
            return self._keyword_parse(reply_text)

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return self._keyword_parse(reply_text)

    def _keyword_parse(self, reply_text):
        """降级方案：基于关键词规则解析"""
        result = {
            "nrs_score": None,
            "sleep_quality": None,
            "medication_taken": None,
            "summary": "关键词解析"
        }

        nrs_match = re.search(r'(\d+)\s*分', reply_text)
        if nrs_match:
            score = int(nrs_match.group(1))
            if 0 <= score <= 10:
                result["nrs_score"] = score

        if any(w in reply_text for w in ["睡得好", "睡得挺好", "睡眠好"]):
            result["sleep_quality"] = "好"
        elif any(w in reply_text for w in ["一夜没睡", "睡不着"]):
            result["sleep_quality"] = "很差"
        elif any(w in reply_text for w in ["没睡好", "睡不好", "睡得不好"]):
            result["sleep_quality"] = "差"
        elif "睡" in reply_text:
            result["sleep_quality"] = "一般"

        if any(w in reply_text for w in ["没吃药", "忘了吃", "没吃", "不吃"]):
            result["medication_taken"] = False
        elif any(w in reply_text for w in ["药吃了", "按时吃", "吃了药"]):
            result["medication_taken"] = True

        result["summary"] = reply_text[:10]
        return result
