import json
import os
from config import GROQ_API_KEY as DEFAULT_GROQ_KEY

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

def get_groq_api_key():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                if "GROQ_API_KEY" in data and data["GROQ_API_KEY"]:
                    return data["GROQ_API_KEY"]
        except Exception:
            pass
    return DEFAULT_GROQ_KEY

def set_groq_api_key(key: str):
    data = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            pass
    data["GROQ_API_KEY"] = key
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)
