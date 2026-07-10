# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Dev Commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run dev server (auto-reload)
uvicorn app:app --reload
# Open: http://127.0.0.1:8000
```

No test suite exists. No linter is configured.

## Architecture

### Backend (`app.py`)

FastAPI app. Loads two data files at startup via `asynccontextmanager` lifespan:

- `data/world_data.json` → `COUNTRIES` (ISO A3 → country metadata) and `LANG_TO_ISO3` (normalized language name → set of ISO A3 codes)
- `data/lingogrid_languages.json` → `LINGOGRID_LANGUAGES` (list of 90 language dicts)

**Routes:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page |
| GET | `/geolingo` | GeoLingo map app |
| GET | `/lingogrid` | LingoGrid puzzle |
| GET | `/api/lingogrid/puzzle` | Daily puzzle data (rows, cols, cell difficulty) |
| POST | `/api/lingogrid/guess` | Validate a language guess, return score |
| GET | `/api/country_info` | Full COUNTRIES dict |
| POST | `/api/coverage` | Language → covered ISO A3 codes, population, speakers |

**LingoGrid puzzle generation** — `_get_daily_puzzle(date)`:
- Seeds `random.Random` from MD5 hash of the ISO date string
- Shuffles 30 categories, picks 3 rows + 3 cols avoiding `_EXCL_GROUPS` conflicts
- Validates all 9 cells have at least one valid language answer
- Falls back to a hardcoded safe puzzle after 500 failed attempts

**Scoring** — `_cell_score(name, valid_langs)`: rank within valid answers sorted by `native_m` descending; rarest = 100 pts, most common = 10 pts.

### Frontend Templates

All templates use inline CSS and JS (no build step).

- **`templates/landing.html`** — Brand header, 2-column hero, game cards (GeoLingo + LingoGrid), footer with contacts.
- **`templates/index.html`** — GeoLingo: Leaflet.js map + sidebar with language search (autocomplete chips), stats panel, Guide modal. Map geometry from `static/world_50m.geojson`.
- **`templates/lingogrid.html`** — LingoGrid: 3×3 category grid, guess modal with autocomplete, archive modal (50 puzzles), category description popovers, results modal. Game state persisted in `localStorage` keyed by date.

### Data Files

- **`data/world_data.json`** — schema requires `countries_by_iso_a3` key. Each country has `official_languages`, `population`, `speakers_by_language`.
- **`data/lingogrid_languages.json`** — `{"languages": [...]}`. Each entry: `{name, native_m, family, subfamily, scripts[], continents[], countries, tonal, clicks, un_official}`.

### Static Files

- `world_50m.geojson` — Natural Earth map geometry
- `location.png` — GeoLingo icon
- `lang-enjoyer.svg` — Brand icon (used in landing page header)
- `og-image.png` — Open Graph image

### Deployment

Railway via `railway.json`. Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
