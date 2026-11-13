# OpenTour

Private server for [Mario Kart Tour](https://en.wikipedia.org/wiki/Mario_Kart_Tour), frozen on the last official season.
The official service ended on September 30, 2026.

No game assets and no keys are included, you need your own copy of the game.
The only game data in the repo are official server responses: `Setting.bin` (season end moved to 2089) and the pipe lotteries.

## Requirements

- `keys/keys.json` with the game keys (see `keys/keys.sample.json` for the shape; the server needs them to decrypt the game data).
- Python 3.11
- A Linux server with nginx and a domain with a Let's Encrypt certificate
- A client built for your server (host, TLS and end-of-service handled on the client side; not part of this repo)

```
pip install -r requirements.txt
```

## Setup

1. Get a Linux server with a domain and a Let's Encrypt certificate (nginx config in `deploy/`).
2. Put your `keys/keys.json` in place.
3. Run, in order:

```
python tools/fetch_resources.py --minimal
python tools/season_title.py
```

- `fetch_resources.py --minimal` downloads only the files the tools need into `resources/cdn/`, the server redirects the rest to the official CDN. Without `--minimal` it downloads everything.
- `season_title.py` makes the endless season and names it "OpenTour". It is required: without it the game crashes while loading.

4. `cp .env.example .env`, put your domains in it, and start the three services (see Running).
5. Give your players a client built for your server. Each player enters a username on first launch.
6. Optional: run `rotate.py` every day (see Rotations) and put the ghost pool from the releases in `server/data/ghost_pool/`.

## Keys

`keys/keys.json` holds the game's own keys (see `keys/keys.sample.json` for the shape): the Sakasho
cipher keys, the item-payload AES key/IV, and the Nabe asset keys (catalog salts, zstd dictionary).
They decrypt everything the game exchanges: the API payloads, the save inventory, and the asset
catalog and packs. They live inside your own copy of the game (the APK's native libraries and
il2cpp metadata) and are read out of it; the extraction is not part of this repo. The keys are
needed both to **download** the data (`fetch_resources.py` decrypts the catalog) and to **serve**
it, so put `keys/keys.json` in place before anything else.

## Game data

The game downloads its data from your server (`https://<your domain>/cdn`):

- the files the tools regenerate (catalog, MasterData, texts) are built on your server from your own download and served from `resources/cdn/`;
- every other file is redirected to the official CDN.

If the official CDN goes down, run `fetch_resources.py` without `--minimal` while it still answers: the server then serves everything itself.

## Running

| Service   | Command                                                | Port |
|-----------|--------------------------------------------------------|------|
| Booster   | `python server/run.py`                                 | 18084 |
| Realtime  | `python server/tools/izumo_listen.py --udp-port 11401` | 18401 TCP, 11401 UDP |
| NAT check | `python server/natcheck.py`                            | 33334, 34543-34545 UDP |

nginx config: see `deploy/`.

Mii: open `https://booster.example.com/baas/mii` to import a Mii QR code.

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
