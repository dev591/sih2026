import yaml
from pathlib import Path

def load_engine_profile(filename: str = "engine_vrde_180.yaml") -> dict:
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    config_path = config_dir / filename
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
