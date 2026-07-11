import hashlib
import hmac
import json
import os
import random
import re
from contextlib import asynccontextmanager
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent

# In production (Railway) this points at a mounted Volume so the actual
# game/answer data never has to live in the (public) git repo. Falls back to
# the bundled data/ dir for local development.
DATA_DIR = Path(os.environ.get("GEKKOLINGO_DATA_DIR", BASE_DIR / "data"))
DATA_PATH = DATA_DIR / "world_data.json"
LINGOGRID_DATA_PATH = DATA_DIR / "lingogrid_languages.json"
LINGOGUESS_DATA_PATH = DATA_DIR / "lingoguess_texts.json"

# Shared secret required to push/update data at runtime via /admin/data.
# Admin endpoints are disabled (always 403) if this is not set.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

_APOSTROPHE_RE = re.compile(r"[''`]")
_WHITESPACE_RE = re.compile(r"\s+")


def norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = _APOSTROPHE_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def _get_langs(country: Dict[str, Any]) -> List[str]:
    langs = country.get("official_languages") or []
    if isinstance(langs, str):
        langs = [langs]
    return [lang for lang in langs if lang]


def _build_lang_index(countries: Dict[str, Dict[str, Any]]) -> Dict[str, Set[str]]:
    index: Dict[str, Set[str]] = {}
    for iso3, country in countries.items():
        for lang in _get_langs(country):
            index.setdefault(norm_text(lang), set()).add(iso3)
    return index


def _collect_known_languages(countries: Dict[str, Dict[str, Any]]) -> List[str]:
    all_langs: Set[str] = set()
    for country in countries.values():
        all_langs.update(_get_langs(country))
    return sorted(all_langs, key=str.lower)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_date(date_str: Optional[str]) -> date_cls:
    """Parse an ISO date param, defaulting to today. Future dates are clamped
    to today so puzzles never become playable before their day (also blocks
    guessing against a not-yet-published puzzle's answer key)."""
    try:
        target = date_cls.fromisoformat(date_str) if date_str else date_cls.today()
    except ValueError:
        target = date_cls.today()
    return min(target, date_cls.today())


def _file_response(filename: str, media_type: str) -> FileResponse:
    path = BASE_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(path), media_type=media_type)


def _load_world_data() -> Dict[str, Any]:
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"Missing {DATA_PATH}. Run: python scripts/generate_world_data.py"
        )
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "countries_by_iso_a3" not in data:
        raise ValueError("world_data.json has unexpected schema (missing countries_by_iso_a3).")
    return data


COUNTRIES: Dict[str, Dict[str, Any]] = {}
LANG_TO_ISO3: Dict[str, Set[str]] = {}
KNOWN_LANGUAGES: List[str] = []

# ── LingoGrid data ─────────────────────────────────────────────────────────────

LINGOGRID_LANGUAGES: List[Dict[str, Any]] = []

LINGOGRID_CATEGORIES: Dict[str, Any] = {
    "Written in Latin script":        lambda l: "Latin" in l["scripts"],
    "Written in Arabic script":       lambda l: "Arabic" in l["scripts"],
    "Written in Cyrillic script":     lambda l: "Cyrillic" in l["scripts"],
    "Written in Devanagari script":   lambda l: "Devanagari" in l["scripts"],
    "Written in Chinese characters":  lambda l: "Hanzi" in l["scripts"],
    "Official language in Europe":    lambda l: "Europe" in l["continents"],
    "Official language in Africa":    lambda l: "Africa" in l["continents"],
    "Official language in Asia":      lambda l: "Asia" in l["continents"],
    "Official language in Americas":  lambda l: "Americas" in l["continents"],
    "Official in 5+ countries":       lambda l: l["countries"] >= 5,
    "Official in 3+ countries":       lambda l: l["countries"] >= 3,
    "100M+ native speakers":          lambda l: l["native_m"] >= 100,
    "10M–99M native speakers":        lambda l: 10 <= l["native_m"] < 100,
    "Under 10M native speakers":      lambda l: l["native_m"] < 10,
    "Indo-European family":           lambda l: l["family"] == "Indo-European",
    "Romance language":               lambda l: l["subfamily"] == "Romance",
    "Germanic language":              lambda l: l["subfamily"] == "Germanic",
    "Slavic language":                lambda l: l["subfamily"] == "Slavic",
    "Semitic language":               lambda l: l["subfamily"] == "Semitic",
    "Sino-Tibetan family":            lambda l: l["family"] == "Sino-Tibetan",
    "Austronesian family":            lambda l: l["family"] == "Austronesian",
    "Turkic language":                lambda l: l["family"] == "Turkic",
    "Dravidian language":             lambda l: l["family"] == "Dravidian",
    "Afro-Asiatic family":            lambda l: l["family"] == "Afro-Asiatic",
    "Niger-Congo family":             lambda l: l["family"] == "Niger-Congo",
    "Tonal language":                 lambda l: l["tonal"],
    "UN official language":           lambda l: l["un_official"],
    "Has click consonants":           lambda l: l["clicks"],
    "Uralic language":                lambda l: l["family"] == "Uralic",
    "Iranian language":               lambda l: l["subfamily"] == "Iranian",
}

# Categories that must not coexist in the same puzzle (subsets / siblings)
_EXCL_GROUPS: List[Set[str]] = [
    {"Romance language", "Germanic language", "Slavic language", "Indo-European family",
     "Semitic language", "Iranian language"},
    {"100M+ native speakers", "10M–99M native speakers", "Under 10M native speakers"},
    {"Official in 5+ countries", "Official in 3+ countries"},
    {"Afro-Asiatic family", "Semitic language"},
    {"Niger-Congo family"},
]


def _lang_matches(lang: Dict, cat: str) -> bool:
    fn = LINGOGRID_CATEGORIES.get(cat)
    return bool(fn and fn(lang))


def _valid_langs(row_cat: str, col_cat: str) -> List[Dict]:
    return [l for l in LINGOGRID_LANGUAGES
            if _lang_matches(l, row_cat) and _lang_matches(l, col_cat)]


def _conflicts(chosen: List[str], new_cat: str) -> bool:
    for group in _EXCL_GROUPS:
        if new_cat in group and any(c in group for c in chosen):
            return True
    return False


def _cell_difficulty(n: int) -> str:
    if n == 0:   return "impossible"
    if n == 1:   return "orange"
    if n <= 3:   return "yellow"
    if n <= 7:   return "green"
    return "white"


def _cell_score(chosen_name: str, valid: List[Dict]) -> int:
    """Rarity score: rarer answer → higher score (10–100)."""
    sorted_valid = sorted(valid, key=lambda l: l["native_m"], reverse=True)
    n = len(sorted_valid)
    for i, l in enumerate(sorted_valid):
        if l["name"] == chosen_name:
            if n == 1:
                return 50
            return round(10 + 90 * (i / (n - 1)))
    return 0


def _get_daily_puzzle(target: date_cls) -> Tuple[List[str], List[str]]:
    seed = int(hashlib.md5(target.isoformat().encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    cat_keys = list(LINGOGRID_CATEGORIES.keys())

    for _ in range(500):
        shuffled = cat_keys.copy()
        rng.shuffle(shuffled)

        rows: List[str] = []
        for c in shuffled:
            if len(rows) == 3:
                break
            if not _conflicts(rows, c):
                rows.append(c)

        if len(rows) < 3:
            continue

        cols: List[str] = []
        for c in shuffled:
            if len(cols) == 3:
                break
            if c not in rows and not _conflicts(cols, c) and not _conflicts(rows + cols, c):
                cols.append(c)

        if len(cols) < 3:
            continue

        if all(_valid_langs(r, c) for r in rows for c in cols):
            return rows, cols

    # Hardcoded safe fallback
    return (
        ["Official language in Europe", "100M+ native speakers", "Tonal language"],
        ["Romance language", "Written in Cyrillic script", "Official language in Africa"],
    )


# ── LingoGuess data ────────────────────────────────────────────────────────────

LINGOGUESS_ROUNDS = 5
LINGOGUESS_TEXTS_BY_LANG: Dict[str, List[str]] = {}


def _lingoguess_daily_rounds(target: date_cls) -> List[Dict[str, Any]]:
    """Returns LINGOGUESS_ROUNDS rounds of {text, language, options}. 'language'
    is the correct answer and must never be sent to the client directly —
    only via the (shuffled) 'options' list."""
    seed = int(hashlib.md5(f"{target.isoformat()}-lingoguess".encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)

    eligible = [l for l in LINGOGRID_LANGUAGES if l["name"] in LINGOGUESS_TEXTS_BY_LANG]
    if not eligible:
        return []

    chosen = rng.sample(eligible, k=min(LINGOGUESS_ROUNDS, len(eligible)))
    all_names = [l["name"] for l in LINGOGRID_LANGUAGES]

    rounds = []
    for lang in chosen:
        text = rng.choice(LINGOGUESS_TEXTS_BY_LANG[lang["name"]])
        distractor_pool = [n for n in all_names if n != lang["name"]]
        distractors = rng.sample(distractor_pool, k=min(3, len(distractor_pool)))
        options = distractors + [lang["name"]]
        rng.shuffle(options)
        rounds.append({"text": text, "language": lang["name"], "options": options})
    return rounds


def _reload_world_data() -> None:
    global COUNTRIES, LANG_TO_ISO3, KNOWN_LANGUAGES
    data = _load_world_data()
    COUNTRIES = data["countries_by_iso_a3"]
    LANG_TO_ISO3 = _build_lang_index(COUNTRIES)
    KNOWN_LANGUAGES = _collect_known_languages(COUNTRIES)


def _reload_lingogrid_data() -> None:
    global LINGOGRID_LANGUAGES
    if not LINGOGRID_DATA_PATH.exists():
        raise FileNotFoundError(f"Missing {LINGOGRID_DATA_PATH}")
    with open(LINGOGRID_DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if "languages" not in payload:
        raise ValueError("lingogrid_languages.json has unexpected schema (missing 'languages').")
    LINGOGRID_LANGUAGES = payload["languages"]


def _reload_lingoguess_data() -> None:
    global LINGOGUESS_TEXTS_BY_LANG
    if not LINGOGUESS_DATA_PATH.exists():
        raise FileNotFoundError(f"Missing {LINGOGUESS_DATA_PATH}")
    with open(LINGOGUESS_DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if "texts" not in payload:
        raise ValueError("lingoguess_texts.json has unexpected schema (missing 'texts').")
    by_lang: Dict[str, List[str]] = {}
    for item in payload["texts"]:
        by_lang.setdefault(item["language"], []).append(item["text"])
    LINGOGUESS_TEXTS_BY_LANG = by_lang


# name -> (file path, reload function, required top-level key in uploaded payload)
_ADMIN_DATASETS = {
    "world_data": (DATA_PATH, _reload_world_data, "countries_by_iso_a3"),
    "lingogrid_languages": (LINGOGRID_DATA_PATH, _reload_lingogrid_data, "languages"),
    "lingoguess_texts": (LINGOGUESS_DATA_PATH, _reload_lingoguess_data, "texts"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tolerate a freshly mounted, still-empty volume (e.g. right after the
    # first deploy, before the data has been seeded via /admin/data).
    # Otherwise the app would crash-loop with no way to reach the seeding
    # endpoint.
    try:
        _reload_world_data()
    except FileNotFoundError:
        pass
    try:
        _reload_lingogrid_data()
    except FileNotFoundError:
        pass
    try:
        _reload_lingoguess_data()
    except FileNotFoundError:
        pass
    yield


app = FastAPI(title="GekkoLingo", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class CoverageRequest(BaseModel):
    languages: List[str] = Field(default_factory=list)


class GuessRequest(BaseModel):
    row: int
    col: int
    language: str
    date: Optional[str] = None


class LingoGuessRequest(BaseModel):
    round: int
    language: str
    hard_mode: bool = False
    date: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/geolingo", response_class=HTMLResponse)
def geolingo(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "known_languages": KNOWN_LANGUAGES},
    )


@app.get("/lingogrid", response_class=HTMLResponse)
def lingogrid(request: Request):
    return templates.TemplateResponse("lingogrid.html", {"request": request})


@app.get("/lingoguess", response_class=HTMLResponse)
def lingoguess(request: Request):
    return templates.TemplateResponse("lingoguess.html", {"request": request})


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return _file_response("sitemap.xml", "application/xml")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return _file_response("robots.txt", "text/plain")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon_ico():
    return _file_response("favicon.ico", "image/x-icon")


@app.api_route("/favicon.svg", methods=["GET", "HEAD"], include_in_schema=False)
def favicon_svg():
    return _file_response("favicon.svg", "image/svg+xml")


@app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"], include_in_schema=False)
def apple_touch_icon():
    return _file_response("apple-touch-icon.png", "image/png")


@app.api_route("/site.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
def webmanifest():
    return _file_response("site.webmanifest", "application/manifest+json")


@app.api_route("/android-chrome-192x192.png", methods=["GET", "HEAD"], include_in_schema=False)
def android_192():
    return _file_response("android-chrome-192x192.png", "image/png")


@app.api_route("/android-chrome-512x512.png", methods=["GET", "HEAD"], include_in_schema=False)
def android_512():
    return _file_response("android-chrome-512x512.png", "image/png")


@app.get("/api/lingogrid/puzzle")
def lingogrid_puzzle(date: Optional[str] = None):
    target = _parse_date(date)

    rows, cols = _get_daily_puzzle(target)

    cells = []
    for r in rows:
        row_cells = []
        for c in cols:
            valid = _valid_langs(r, c)
            row_cells.append({
                "valid_count": len(valid),
                "difficulty": _cell_difficulty(len(valid)),
            })
        cells.append(row_cells)

    return {
        "date": target.isoformat(),
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "language_names": sorted(l["name"] for l in LINGOGRID_LANGUAGES),
    }


@app.post("/api/lingogrid/guess")
def lingogrid_guess(payload: GuessRequest):
    if payload.row not in (0, 1, 2) or payload.col not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="row and col must be 0–2")

    lang_name = payload.language.strip()
    if not lang_name:
        raise HTTPException(status_code=400, detail="language is required")

    target = _parse_date(payload.date)

    rows, cols = _get_daily_puzzle(target)
    row_cat = rows[payload.row]
    col_cat = cols[payload.col]

    valid = _valid_langs(row_cat, col_cat)
    match = next((l for l in valid if l["name"].lower() == lang_name.lower()), None)

    if not match:
        return {"valid": False, "score": 0, "valid_count": len(valid)}

    score = _cell_score(match["name"], valid)
    return {
        "valid": True,
        "canonical_name": match["name"],
        "score": score,
        "valid_count": len(valid),
        "native_m": match["native_m"],
        "family": match["family"],
    }


@app.get("/api/lingoguess/puzzle")
def lingoguess_puzzle(date: Optional[str] = None):
    target = _parse_date(date)

    rounds = _lingoguess_daily_rounds(target)
    return {
        "date": target.isoformat(),
        "rounds": [{"text": r["text"], "options": r["options"]} for r in rounds],
        "language_names": sorted(l["name"] for l in LINGOGRID_LANGUAGES),
    }


@app.post("/api/lingoguess/guess")
def lingoguess_guess(payload: LingoGuessRequest):
    target = _parse_date(payload.date)

    rounds = _lingoguess_daily_rounds(target)
    if payload.round < 0 or payload.round >= len(rounds):
        raise HTTPException(status_code=400, detail="Invalid round index")

    lang_name = payload.language.strip()
    if not lang_name:
        raise HTTPException(status_code=400, detail="language is required")

    correct_lang = rounds[payload.round]["language"]
    is_correct = lang_name.lower() == correct_lang.lower()

    score = 0
    if is_correct:
        score = _cell_score(correct_lang, LINGOGRID_LANGUAGES)
        if payload.hard_mode:
            score = round(score * 1.5)

    return {
        "valid": is_correct,
        "correct_language": correct_lang,
        "score": score,
    }


@app.get("/api/country_info")
def country_info():
    return {"countries_by_iso_a3": COUNTRIES}


def _require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if not ADMIN_TOKEN or not x_admin_token or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/data/{name}", dependencies=[Depends(_require_admin)], include_in_schema=False)
async def upload_data(name: str, request: Request):
    if name not in _ADMIN_DATASETS:
        raise HTTPException(status_code=404, detail="Unknown dataset")
    path, reload_fn, required_key = _ADMIN_DATASETS[name]

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict) or required_key not in payload:
        raise HTTPException(status_code=400, detail=f"Payload must be a JSON object with a '{required_key}' key")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_path, path)

    try:
        reload_fn()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Data written but reload failed: {e}")

    return {"status": "ok", "dataset": name}


@app.get("/admin/data/status", dependencies=[Depends(_require_admin)], include_in_schema=False)
def data_status():
    return {
        "data_dir": str(DATA_DIR),
        "countries_loaded": len(COUNTRIES),
        "lingogrid_languages_loaded": len(LINGOGRID_LANGUAGES),
        "lingoguess_languages_loaded": len(LINGOGUESS_TEXTS_BY_LANG),
        "lingoguess_texts_loaded": sum(len(v) for v in LINGOGUESS_TEXTS_BY_LANG.values()),
    }


@app.post("/api/coverage")
def coverage(payload: CoverageRequest):
    langs = [x.strip() for x in (payload.languages or []) if x and x.strip()]
    lang_norms = [norm_text(x) for x in langs]

    covered_iso3: Set[str] = set()
    unknown: List[str] = []

    for raw, ln in zip(langs, lang_norms):
        iso3s = LANG_TO_ISO3.get(ln)
        if iso3s:
            covered_iso3.update(iso3s)
        else:
            unknown.append(raw)

    covered_population = sum(
        _safe_int(COUNTRIES[i].get("population", 0)) for i in covered_iso3
    )

    covered_speakers = 0
    for iso3 in covered_iso3:
        speakers_by_language = COUNTRIES.get(iso3, {}).get("speakers_by_language") or {}
        for raw in langs:
            covered_speakers += _safe_int(speakers_by_language.get(raw, 0))

    return {
        "input_languages": langs,
        "unknown_languages": unknown,
        "covered_iso_a3": sorted(covered_iso3),
        "covered_count": len(covered_iso3),
        "covered_population": covered_population,
        "covered_speakers_in_countries": covered_speakers,
    }
