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
python tools/fetch_resources.py --platform both
python tools/season_title.py
cp .env.example .env
```

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

## Rotations

```
python tools/rotate.py
```

Publishes the day's shop, cup challenges, 100/150cc, Team Rally, Ranked Cup and seasonal decorations as a new data version.
Run it every day at 06:00 UTC, restart the server after the first run:

```
1 6 * * * cd /path/to/OpenTour && python tools/rotate.py >> rotate.log 2>&1
```

## Tools

- `tools/setting_bin.py`: show or change the season dates.
- `tools/shop_rotation.py`, `cup_challenges.py`, `pvp_rules.py`, `festival_schedule.py`, `seasonal.py`, `weekly_rankings.py`: `plan` shows what `rotate.py` will do.
- `tools/tour_gifts.py`: longer Tour Gifts ladder (`rotate.py --gifts`).
- `tools/anonymize_ghost_pool.py`: anonymize a ghost pool.

The ghost pool is too big for git, it is available in the releases. Put it in `server/data/ghost_pool/`.

## License

GPL-3.0
