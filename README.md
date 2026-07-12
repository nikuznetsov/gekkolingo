# GekkoLingo

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal)
![Leaflet](https://img.shields.io/badge/Leaflet-Map-green)
![License](https://img.shields.io/badge/License-MIT-lightgrey)
![Railway](https://img.shields.io/badge/Deployed%20on-Railway-purple)

**GekkoLingo** is a small platform of language games and tools for people who love exploring how languages work across the world — where they're spoken, how they're related, and how many people use them.

🔗 **Live:** https://gekkolingo.online

---

## Games

### 🗺️ GeoLingo
Pick any language and instantly see every country where it's officially spoken — with population and speaker statistics on an interactive world map.

### 🧩 LingoGrid
A daily language grid puzzle. Find a language that fits both a row and column category — scored by rarity. New puzzle every day, with a 50-game archive.

### 💬 LingoGuess
Read a short text and guess which language it's written in. Five rounds a day, scored by rarity — or switch to Hard Mode and type the answer yourself instead of picking from options.

---

## Tech Stack

- **Backend:** Python, FastAPI, Jinja2 — a single-file app (`app.py`), no ORM, no build step
- **Frontend:** Server-rendered HTML with inline CSS and vanilla JavaScript (no framework, no bundler)
- **Map Engine:** Leaflet.js + Natural Earth 50m GeoJSON
- **Deployment:** Railway, with a persistent Volume for game data

---

## Run Locally

```bash
git clone https://github.com/nikuznetsov/gekkolingo.git
cd gekkolingo

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
uvicorn app:app --reload
# Open: http://127.0.0.1:8000
```

The three JSON files under `data/` (world/country data, LingoGrid languages, LingoGuess texts) are not committed — see [Game Data](#game-data) below for how to seed them locally.

---

## Project Structure

```
app.py                        # FastAPI app: routes, puzzle generation, scoring, data loading
data/                         # Not tracked in git — seeded locally or via the admin API
  world_data.json             # Country + language dataset (ISO A3 keyed)
  lingogrid_languages.json    # 90 languages with metadata for LingoGrid
  lingoguess_texts.json       # Sample texts per language for LingoGuess
templates/
  landing.html                # Home page (/)
  index.html                  # GeoLingo (/geolingo)
  lingogrid.html              # LingoGrid (/lingogrid)
  lingoguess.html             # LingoGuess (/lingoguess)
static/
  world_50m.geojson           # Map geometry (Natural Earth)
  location.png                # GeoLingo icon
  gecko-logo.png              # Brand icon
  og-image.png                # Open Graph image
```

---

## Game Data

None of the three data files above ship in the public repo — game/puzzle content (and the daily answer keys derived from it) intentionally stays out of git history. Instead:

- Locally, drop your own copies at `data/*.json` matching the schemas described in `CLAUDE.md`.
- In production, `GEKKOLINGO_DATA_DIR` points the app at a Railway Volume, and a token-gated `POST /admin/data/{name}` endpoint (guarded by an `ADMIN_TOKEN` environment variable) uploads and hot-reloads each file without a redeploy.

See `CLAUDE.md` for the full route table, data schemas, and puzzle-generation details.

---

## Contacts

- 💼 [LinkedIn](https://www.linkedin.com/in/nikuznetsoff)
- 📧 [Email](mailto:nikuznetsoff@gmail.com)
- 🐙 [GitHub](https://github.com/nikuznetsov)

---

## Disclaimer

All numerical values (population and speakers) are approximate and derived from open-source/publicly available data. Geographic boundaries are based on Natural Earth and related sources and may not reflect official political positions.

If you find inaccurate data, please open an Issue or submit a PR with credible sources.

---

## License

Released under the [MIT License](LICENSE).
