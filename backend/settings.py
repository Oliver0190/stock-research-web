import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.environ.get("STOCK_AGENT_DB", str(ROOT / "data" / "stock-agent.sqlite3")))


def load_config():
    with (ROOT / "config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)
