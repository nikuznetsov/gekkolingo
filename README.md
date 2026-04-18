# LangEnjoyer

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-teal)
![Leaflet](https://img.shields.io/badge/Leaflet-Map-green)
![License](https://img.shields.io/badge/License-MIT-lightgrey)
![Railway](https://img.shields.io/badge/Deployed%20on-Railway-purple)

**LangEnjoyer** is a platform of language games and tools for people who love exploring how languages work across the world.

🔗 **Live:** https://geolingo.world

---

## Games

### GeoLingo
Pick any language and instantly see every country where it's officially spoken — with population and speaker statistics on an interactive world map.

### LingoGrid
A daily language grid puzzle. Find a language that fits both a row and column category — scored by rarity. New puzzle every day, with a 50-game archive.

---

## Tech Stack

- **Backend:** Python, FastAPI, Jinja2
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Map Engine:** Leaflet.js + Natural Earth 50m GeoJSON
- **Deployment:** Railway

---

## Run Locally

```bash
git clone https://github.com/nikuznetsov/geolingo.git
cd geolingo

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
uvicorn app:app --reload
# Open: http://127.0.0.1:8000
```

---

## Project Structure

```
app.py                        # FastAPI application
data/
  world_data.json             # Country + language dataset (ISO A3 keyed)
  lingogrid_languages.json    # 90 languages with metadata for LingoGrid
templates/
  landing.html                # Home page (/)
  index.html                  # GeoLingo (/geolingo)
  lingogrid.html              # LingoGrid (/lingogrid)
static/
  world_50m.geojson           # Map geometry
  location.png                # GeoLingo icon
  lang-enjoyer.svg            # Brand icon
  og-image.png                # Open Graph image
```

---

## Contacts

- 💼 [LinkedIn](https://www.linkedin.com/in/nikita-kuznetsov-ab196a245/)
- 📧 [Email](mailto:nikuznetsoff@gmail.com)
- 🐙 [GitHub](https://github.com/nikuznetsov)

---

## Disclaimer

All numerical values (population and speakers) are approximate and derived from open-source/publicly available data. Geographic boundaries are based on Natural Earth and related sources and may not reflect official political positions.

If you find inaccurate data, please open an Issue or submit a PR with credible sources.

---

MIT License
