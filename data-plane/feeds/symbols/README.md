# Symbol lists

Drop a symbols file here (one symbol per line, or comma-separated) and point a feed
at it — the folder mounts read-only into the feed containers at `/symbols`.

- Synthetic sim (any count, no provider limits): set `SIM_SYMBOL_COUNT=1000` in
  `.env` — bpipe-sim generates that many symbols automatically. No file needed.
- Real provider from a file: put e.g. `universe.txt` here and set
  `PROVIDER_SYMBOLS_FILE=/symbols/universe.txt` in `.env`.

Note: real providers cap concurrent streaming symbols by plan tier — a 1000-symbol
live stream needs a paid plan. The synthetic sim has no such limit.
