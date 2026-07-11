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

FastAPI app. Loads data files at startup via `asynccontextmanager` lifespan, from `GEKKOLINGO_DATA_DIR` (defaults to the bundled `data/` dir locally; points at a Railway Volume in production so game/answer data never has to live in the public repo):

- `data/world_data.json` → `COUNTRIES` (ISO A3 → country metadata) and `LANG_TO_ISO3` (normalized language name → set of ISO A3 codes)
- `data/lingogrid_languages.json` → `LINGOGRID_LANGUAGES` (list of 90 language dicts)
- `data/lingoguess_texts.json` → `LINGOGUESS_TEXTS_BY_LANG` (language name → list of sample texts)

None of these three files are tracked in git (see `.gitignore`) — they're seeded/updated at runtime via the admin endpoints below.

**Routes:**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page |
| GET | `/geolingo` | GeoLingo map app |
| GET | `/lingogrid` | LingoGrid puzzle |
| GET | `/lingoguess` | LingoGuess text-guessing challenge |
| GET | `/api/lingogrid/puzzle` | Daily puzzle data (rows, cols, cell difficulty) |
| POST | `/api/lingogrid/guess` | Validate a language guess, return score |
| GET | `/api/lingoguess/puzzle` | Daily rounds (text + shuffled language options) |
| POST | `/api/lingoguess/guess` | Validate a language guess for a round, return score |
| GET | `/api/country_info` | Full COUNTRIES dict |
| POST | `/api/coverage` | Language → covered ISO A3 codes, population, speakers |
| POST | `/admin/data/{name}` | Token-gated (`X-Admin-Token` vs `ADMIN_TOKEN` env var) upload that overwrites and hot-reloads one of the three data files. `name` ∈ `world_data`, `lingogrid_languages`, `lingoguess_texts` |
| GET | `/admin/data/status` | Token-gated: counts of currently loaded data, for verifying an upload |

**LingoGrid puzzle generation** — `_get_daily_puzzle(date)`:
- Seeds `random.Random` from MD5 hash of the ISO date string
- Shuffles 39 categories, picks 3 rows + 3 cols avoiding `_EXCL_GROUPS` conflicts
- Validates all 9 cells have at least one valid language answer
- Falls back to a hardcoded safe puzzle after 500 failed attempts

**Scoring** — `_cell_score(name, valid_langs)`: rank within valid answers sorted by `native_m` descending; rarest = 100 pts, most common = 10 pts.

**LingoGuess daily rounds** — `_lingoguess_daily_rounds(date)`:
- Seeds `random.Random` from MD5 hash of `"{date}-lingoguess"` (separate seed space from LingoGrid)
- Picks `LINGOGUESS_ROUNDS` (5) languages that have sample texts. The text for each language is *not* picked with `random.choice` — texts are shuffled once per language at load time (`_reload_lingoguess_data`, seeded from the language name) and then indexed by `days-since-2026-03-25 % len(texts)`, so a language's full text pool cycles through exactly once before any text repeats (a repeat can only happen when two picks are an exact multiple of that language's pool size apart)
- Distractor options (3 per round) are sampled from the full `LINGOGRID_LANGUAGES` name pool, not just languages with texts
- Correct language is kept server-side only (`round["language"]`) — `/api/lingoguess/puzzle` strips it, returning just `text` + shuffled `options`
- Scoring reuses `_cell_score(correct_language, LINGOGRID_LANGUAGES)` (rarity rank across all 90 languages); Hard Mode multiplies the result by 1.5

### Frontend Templates

All templates use inline CSS and JS (no build step).

- **`templates/landing.html`** — Brand header, 2-column hero, game cards (GeoLingo + LingoGrid + LingoGuess), footer with contacts.
- **`templates/index.html`** — GeoLingo: Leaflet.js map + sidebar with language search (autocomplete chips), stats panel, Guide modal. Map geometry from `static/world_50m.geojson`.
- **`templates/lingogrid.html`** — LingoGrid: 3×3 category grid, guess modal with autocomplete, archive modal (50 puzzles), category description popovers, results modal. Game state persisted in `localStorage` keyed by date.
- **`templates/lingoguess.html`** — LingoGuess: 5-round daily text-guessing challenge, multiple-choice or Hard Mode (free-text with autocomplete, locked once the first round starts), archive modal (50 puzzles), results modal. Game state persisted in `localStorage` keyed by date.

### Data Files

- **`data/world_data.json`** — schema requires `countries_by_iso_a3` key. Each country has `official_languages`, `population`, `speakers_by_language`.
- **`data/lingogrid_languages.json`** — `{"languages": [...]}`. Each entry: `{name, native_m, family, subfamily, scripts[], continents[], countries, tonal, clicks, un_official}`.
- **`data/lingoguess_texts.json`** — `{"texts": [...]}`. Each entry: `{language, text}`. `language` must match a `name` in `lingogrid_languages.json` (reused for scoring/distractors). AI-generated starter set: 150 texts across 30 languages (5 each) — expand via `/admin/data/lingoguess_texts` any time.

### Static Files

- `world_50m.geojson` — Natural Earth map geometry
- `location.png` — GeoLingo icon
- `gecko-logo.png` — Brand icon (used in landing page header)
- `og-image.png` — Open Graph image

### Deployment

Railway via `railway.json`. Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
