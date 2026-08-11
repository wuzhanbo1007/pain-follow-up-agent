# -*- coding: utf-8 -*-
"""
PainSmart 9.0 — MySQL 连接配置
远端库默认配置（如与默认不同，改 backend/.env 即可，无需动代码）：
  DB_HOST     默认 192.168.1.135
  DB_PORT     默认 3306
  DB_USER     默认 canal
  DB_PASSWORD 默认 canal
  DB_NAME     默认 pain-followup
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv 未安装时直接读系统环境变量

DB_HOST = os.getenv("DB_HOST", "192.168.1.135")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "canal")
DB_PASSWORD = os.getenv("DB_PASSWORD", "canal")
DB_NAME = os.getenv("DB_NAME", "pain-followup")

# SQLAlchemy 连接串（mysql+pymysql 驱动）
DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    f"?charset=utf8mb4"
)
