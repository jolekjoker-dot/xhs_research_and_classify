import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )
    output_dir: Path = field(default_factory=lambda: Path("output/knowledge_base"))
    config_dir: Path = field(default_factory=lambda: Path("config"))

    # DeepSeek API
    api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"
        )
    )
    api_key: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    )
    api_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "deepseek-chat")
    )

    # Search defaults
    search_max_results: int = 20
    search_sort: str = "general"  # general, time_descending, popularity

    # Scrape defaults
    scrape_min_delay: float = 3.0
    scrape_max_delay: float = 8.0
    scrape_max_concurrent: int = 1
    scrape_timeout: int = 30

    # Knowledge base
    kb_template: str = "default"

    def load_categories(self) -> dict[str, Any]:
        """加载分类体系配置"""
        path = self.config_dir / "categories.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("categories", {})

    def load_classification_prompt(self) -> str:
        """加载 LLM 分类提示词"""
        path = self.config_dir / "categories.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("classification_prompt", "")

    def get_categories_text(self) -> str:
        """生成分类体系的文本描述，用于 LLM prompt"""
        categories = self.load_categories()
        lines = []
        for cat_name, cat_info in categories.items():
            lines.append(f"## {cat_info['label']}")
            for sub in cat_info.get("subcategories", []):
                lines.append(f"  - {sub}")
        return "\n".join(lines)

    def ensure_output_dir(self) -> Path:
        """确保输出目录存在"""
        p = self.project_root / self.output_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


_config_instance: Config | None = None


def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance