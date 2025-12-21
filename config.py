import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    bot_token: str
    manager_password: str

def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    pwd = os.getenv("MANAGER_PASSWORD", "").strip()

    if not token:
        raise RuntimeError("BOT_TOKEN is missing in environment variables")
    if not pwd:
        raise RuntimeError("MANAGER_PASSWORD is missing in environment variables")

    return Config(bot_token=token, manager_password=pwd)