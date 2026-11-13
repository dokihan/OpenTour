# OpenTour

Private server for [Mario Kart Tour](https://en.wikipedia.org/wiki/Mario_Kart_Tour), frozen on the last official season.
The official service ended on September 30, 2026.

No game files and no keys are included, you need your own copy of the game.
The only game file in the repo is `Setting.bin` (season end moved to 2089).

## Requirements

- Mario Kart Tour 4.0.0: the Android APK + `arm64-v8a` split (or the `.xapk`). An iOS IPA works for the client, but the keys come from the APK.
- Python 3.11
- A Linux server with nginx

```
pip install -r requirements.txt
```

## Setup

```
python tools/extract_keys.py --apk base.apk --split split_config.arm64_v8a.apk
python tools/fetch_resources.py --platform both
python tools/season_title.py
cp .env.example .env
```

- `extract_keys.py` reads the keys from your APK into `keys/keys.json`.
- `fetch_resources.py` downloads the game data from the official CDN into `resources/cdn/`.
- `season_title.py` renames the season to "OpenTour" (optional).
- Edit `.env` with your domains.

## Running

| Service   | Command                                                | Port |
|-----------|--------------------------------------------------------|------|
| Booster   | `python server/run.py`                                 | 18084 |
| Realtime  | `python server/tools/izumo_listen.py --udp-port 11401` | 18401 TCP, 11401 UDP |
| NAT check | `python server/natcheck.py`                            | 33334, 34543-34545 UDP |

nginx config: see `deploy/`.

## Client

```
python tools/build_apk.py --apk base.apk --split split_config.arm64_v8a.apk --host booster.example.com
python tools/build_ipa.py --ipa app.ipa --host booster.example.com -o OpenTour.ipa
```

The IPA is unsigned, sign it with AltStore, SideStore, Sideloadly or TrollStore.

## Tools

- `server/tools/setting_bin.py`: show or change the season dates.
- `server/tools/shop_rotation.py`: shop offers.
- `server/tools/seasonal.py`: seasonal decorations.
- `server/tools/anonymize_ghost_pool.py`: anonymize a ghost pool.

The ghost pool is too big for git, it is available in the releases. Put it in `server/data/ghost_pool/`.

## License

GPL-3.0
