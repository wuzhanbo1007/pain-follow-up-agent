"""
全局配置 — 从 .env 文件和环境变量读取
"""
import os
from pathlib import Path

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv 未安装时直接读系统环境变量


# ===== LLM 配置 =====
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "20"))        # LLM 调用超时（秒），超时后降级
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))  # 失败重试次数（消化限流/瞬时空响应）

# ===== 服务配置（FastAPI）=====
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "5000"))
API_DEBUG = os.getenv("API_DEBUG", "true").lower() == "true"

# ===== 数据库配置 =====
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "history.db"))

# ===== Demo 日期固化 =====
# 设置后，随访判定、"今天"等逻辑均以此日期为准，保证跨天演示数据一致。
# 留空则默认使用系统当天日期。
DEMO_TODAY = os.getenv("DEMO_TODAY", "")
