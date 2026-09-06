import json
import os

DATA_FILE = "data/config.json"


def _ensure():
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({}, f)


def load():
    _ensure()
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save(data):
    _ensure()
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_guild_config(guild_id):
    data = load()
    return data.get(str(guild_id), {"embed": {}, "categories": []})


def set_guild_config(guild_id, config):
    data = load()
    data[str(guild_id)] = config
    save(data)

