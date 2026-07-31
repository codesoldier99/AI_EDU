"""全局配置。

所有阈值一律做成配置（见 DEVELOPMENT_PLAN 第九节"待教师团队拍板的事项"），
代码里禁止出现魔法数字。配置优先级：环境变量 > config.yaml > 默认值。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # 可选依赖，缺失时退化为纯默认值
    except Exception:  # pragma: no cover
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class TeachingConfig:
    """教学参数：初值由教师经验给定，代码只负责可配置。"""

    # 掌握度阈值：低于该值判定"未掌握"、进入缺口
    mastery_threshold: float = 0.75
    # 一次最多推送几个缺口知识点（拉取式：只给最挡路的 1-2 个）
    gap_push_limit: int = 2
    # 证据条数达到多少时置信度接近饱和
    confidence_saturation: float = 5.0
    # 证据"新鲜度"半衰期（天）：越久远的证据置信度越低
    confidence_halflife_days: float = 45.0
    # 追问降级阈值：连续尝试多少次无进展后降一级
    escalation_attempts: int = 3
    # 挫败信号阈值：一次会话内重复求助多少次判定为挫败
    frustration_repeats: int = 2
    # 中断多少天后重新活跃算 comeback
    comeback_gap_days: int = 3
    # 冷启动保护：证据少于该条数时报告标注"数据不足"
    min_evidence_for_report: int = 3
    # BKT 课程级默认参数（冷启动）
    bkt_default: dict = field(
        default_factory=lambda: {
            "p_init": 0.15,
            "p_transit": 0.20,
            "p_slip": 0.10,
            "p_guess": 0.20,
        }
    )
    # 五类项目信号 -> M1..M8 权重矩阵（教师团队标定，此处为待议初值）
    signal_weights: dict = field(
        default_factory=lambda: {
            "code_commit": {"M2": 0.5, "M7": 0.5},
            "build_test": {"M7": 0.6, "M2": 0.4},
            "runtime": {"M5": 0.5, "M6": 0.3, "M3": 0.2},
            "doc_delivery": {"M8": 0.8, "M7": 0.2},
            "collaboration": {"M7": 0.7, "M8": 0.3},
        }
    )


@dataclass
class Config:
    db_url: str = f"sqlite:///{ROOT / 'var' / 'aiedu.db'}"
    host: str = "127.0.0.1"
    port: int = 8900
    # 大模型（可替换件）。无 Key 时自动降级为离线确定性表达器。
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen2.5-72b-instruct"
    llm_timeout: int = 60
    embed_model: str = "hashed-char-bigram-tfidf"
    embed_version: str = "v1"
    embed_dim: int = 256
    teaching: TeachingConfig = field(default_factory=TeachingConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config() -> Config:
    raw = _load_yaml(ROOT / "config.yaml")
    cfg = Config()
    for key, val in raw.items():
        if key == "teaching" and isinstance(val, dict):
            for tk, tv in val.items():
                if hasattr(cfg.teaching, tk):
                    setattr(cfg.teaching, tk, tv)
        elif hasattr(cfg, key):
            setattr(cfg, key, val)
    # 环境变量覆盖
    env_map = {
        "AIEDU_DB_URL": "db_url",
        "AIEDU_HOST": "host",
        "AIEDU_PORT": "port",
        "LLM_BASE_URL": "llm_base_url",
        "LLM_API_KEY": "llm_api_key",
        "LLM_MODEL": "llm_model",
    }
    for env, attr in env_map.items():
        if os.environ.get(env):
            cur = getattr(cfg, attr)
            setattr(cfg, attr, type(cur)(os.environ[env]))
    return cfg


CONFIG = load_config()
