from __future__ import annotations

import os

API_HOST = os.environ.get("BOOSTER_API_HOST", "booster.example.com")
BIND_HOST = os.environ.get("BOOSTER_BIND", "127.0.0.1")
BIND_PORT = int(os.environ.get("BOOSTER_PORT", "18084"))

ASSET_BASE_URL = os.environ.get("BOOSTER_ASSET_URL", f"https://{API_HOST}/cdn").rstrip("/")
NA_BASE_URL = os.environ.get("BOOSTER_NA_URL", f"https://{API_HOST}/baas")
WEBVIEW_URL = os.environ.get("BOOSTER_WEBVIEW_URL", f"https://{API_HOST}/support")
IZUMO_HOST = os.environ.get("BOOSTER_IZUMO_HOST", "realtime.example.com")
IZUMO_PORT = int(os.environ.get("BOOSTER_IZUMO_PORT", "11401"))
IZUMO_URL = f"{IZUMO_HOST}:{IZUMO_PORT}"

def constants() -> list[tuple[str, str]]:
    return [("na_base_url", NA_BASE_URL), ("asset_base_url", ASSET_BASE_URL),
            ("webview_url", WEBVIEW_URL), ("izumo_url", IZUMO_URL)]
