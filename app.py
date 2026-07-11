import hashlib
import hmac
import json
import os
import random
import re
from contextlib import asynccontextmanager
from datetime import date as date_cls
from functools import lru_cache
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
            f"Missing {DATA_PATH}. Seed it via POST /admin/data/world_data "
            f"(see CLAUDE.md) or place a world_data.json file at that path."
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
    "Indo-Aryan language":            lambda l: l["subfamily"] == "Indo-Aryan",
    "Bantu language":                 lambda l: l["subfamily"] == "Bantu",
    "Oghuz language":                 lambda l: l["subfamily"] == "Oghuz",
    "Austroasiatic family":           lambda l: l["family"] == "Austroasiatic",
    "Tai-Kadai family":               lambda l: l["family"] == "Tai-Kadai",
    "Written in Geez script":         lambda l: "Geez" in l["scripts"],
    "Official language in Oceania":   lambda l: "Oceania" in l["continents"],
    "50M+ native speakers":           lambda l: l["native_m"] >= 50,
    "Official in only 1 country":     lambda l: l["countries"] == 1,
}

# Categories that must not coexist in the same puzzle (subsets / siblings)
_EXCL_GROUPS: List[Set[str]] = [
    {"Romance language", "Germanic language", "Slavic language", "Indo-European family",
     "Semitic language", "Iranian language", "Indo-Aryan language"},
    {"100M+ native speakers", "10M–99M native speakers", "Under 10M native speakers",
     "50M+ native speakers"},
    {"Official in 5+ countries", "Official in 3+ countries", "Official in only 1 country"},
    {"Afro-Asiatic family", "Semitic language"},
    {"Niger-Congo family", "Bantu language"},
    {"Turkic language", "Oghuz language"},
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


@lru_cache(maxsize=256)
def _get_daily_puzzle(target: date_cls) -> Tuple[List[str], List[str]]:
    seed = int(hashlib.md5(target.isoformat().encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    cat_keys = list(LINGOGRID_CATEGORIES.keys())

    # 3000 attempts: with 39 categories in the pool, the narrower ones (e.g.
    # 2-language families) push the failure rate high enough at 500 attempts
    # to hit the hardcoded fallback noticeably often — measured ~0% at 2000+.
    for _ in range(3000):
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

    # Hardcoded safe fallback (all 9 cells verified non-empty — the previous
    # version paired "Tonal language" with "Romance language", which has zero
    # overlap and produced an unplayable cell whenever this path was hit)
    return (
        ["Official language in Europe", "Official language in Asia", "Written in Latin script"],
        ["Official language in Africa", "Official language in Americas", "100M+ native speakers"],
    )


# ── LingoGuess data ────────────────────────────────────────────────────────────

LINGOGUESS_ROUNDS = 5
LINGOGUESS_TEXTS_BY_LANG: Dict[str, List[str]] = {}

# Reference date for the per-language text rotation below. Arbitrary — just
# needs to be fixed so the same date always maps to the same text.
_LINGOGUESS_TEXT_EPOCH = date_cls(2026, 3, 25)


@lru_cache(maxsize=256)
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
    day_index = (target - _LINGOGUESS_TEXT_EPOCH).days

    rounds = []
    for lang in chosen:
        # Cycle through this language's (pre-shuffled) text pool by day index
        # rather than picking uniformly at random each time, so every text is
        # shown once before any of them repeat — a random .choice() each day
        # could otherwise replay the same text within just a handful of picks.
        texts = LINGOGUESS_TEXTS_BY_LANG[lang["name"]]
        text = texts[day_index % len(texts)]
        distractor_pool = [n for n in all_names if n != lang["name"]]
        distractors = rng.sample(distractor_pool, k=min(3, len(distractor_pool)))
        options = distractors + [lang["name"]]
        rng.shuffle(options)
        rounds.append({"text": text, "language": lang["name"], "options": options})
    return rounds


# Fields read by LINGOGRID_CATEGORIES / _cell_score / _lingoguess_daily_rounds.
# Validated on every admin upload so a malformed payload is rejected before
# it ever reaches disk or the in-memory puzzle generator.
_LINGOGRID_LANGUAGE_REQUIRED_FIELDS = (
    "name", "native_m", "family", "subfamily", "scripts",
    "continents", "countries", "tonal", "clicks", "un_official",
)


def _build_world_data(data: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Set[str]], List[str]]:
    if "countries_by_iso_a3" not in data:
        raise ValueError("world_data.json has unexpected schema (missing countries_by_iso_a3).")
    countries = data["countries_by_iso_a3"]
    if not isinstance(countries, dict):
        raise ValueError("countries_by_iso_a3 must be an object keyed by ISO A3 code.")
    lang_to_iso3 = _build_lang_index(countries)
    known_languages = _collect_known_languages(countries)
    return countries, lang_to_iso3, known_languages


def _build_lingogrid_data(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "languages" not in payload:
        raise ValueError("lingogrid_languages.json has unexpected schema (missing 'languages').")
    languages = payload["languages"]
    if not isinstance(languages, list):
        raise ValueError("'languages' must be a list.")
    for entry in languages:
        if not isinstance(entry, dict):
            raise ValueError("each language entry must be an object.")
        missing = [f for f in _LINGOGRID_LANGUAGE_REQUIRED_FIELDS if f not in entry]
        if missing:
            raise ValueError(f"language entry {entry.get('name', '?')!r} missing fields: {missing}")
    return languages


def _build_lingoguess_data(payload: Dict[str, Any]) -> Dict[str, List[str]]:
    if "texts" not in payload:
        raise ValueError("lingoguess_texts.json has unexpected schema (missing 'texts').")
    texts = payload["texts"]
    if not isinstance(texts, list):
        raise ValueError("'texts' must be a list.")
    by_lang: Dict[str, List[str]] = {}
    for item in texts:
        if not isinstance(item, dict) or "language" not in item or "text" not in item:
            raise ValueError("each text entry must be an object with 'language' and 'text'.")
        by_lang.setdefault(item["language"], []).append(item["text"])
    # Deterministic per-language shuffle so the daily rotation in
    # _lingoguess_daily_rounds doesn't just replay the source-file order.
    for lang, texts_for_lang in by_lang.items():
        random.Random(f"lingoguess-order-{lang}").shuffle(texts_for_lang)
    return by_lang


def _assign_world_data(built: Tuple[Dict[str, Dict[str, Any]], Dict[str, Set[str]], List[str]]) -> None:
    global COUNTRIES, LANG_TO_ISO3, KNOWN_LANGUAGES
    COUNTRIES, LANG_TO_ISO3, KNOWN_LANGUAGES = built


def _assign_lingogrid_data(built: List[Dict[str, Any]]) -> None:
    global LINGOGRID_LANGUAGES
    LINGOGRID_LANGUAGES = built
    # Both caches derive puzzles from LINGOGRID_LANGUAGES — stale entries
    # would otherwise keep serving pre-reload puzzles/rounds.
    _get_daily_puzzle.cache_clear()
    _lingoguess_daily_rounds.cache_clear()


def _assign_lingoguess_data(built: Dict[str, List[str]]) -> None:
    global LINGOGUESS_TEXTS_BY_LANG
    LINGOGUESS_TEXTS_BY_LANG = built
    _lingoguess_daily_rounds.cache_clear()


def _reload_world_data() -> None:
    _assign_world_data(_build_world_data(_load_world_data()))


def _reload_lingogrid_data() -> None:
    if not LINGOGRID_DATA_PATH.exists():
        raise FileNotFoundError(f"Missing {LINGOGRID_DATA_PATH}")
    with open(LINGOGRID_DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    _assign_lingogrid_data(_build_lingogrid_data(payload))


def _reload_lingoguess_data() -> None:
    if not LINGOGUESS_DATA_PATH.exists():
        raise FileNotFoundError(f"Missing {LINGOGUESS_DATA_PATH}")
    with open(LINGOGUESS_DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    _assign_lingoguess_data(_build_lingoguess_data(payload))


# name -> (file path, build function, assign function, required top-level key in uploaded payload)
_ADMIN_DATASETS = {
    "world_data": (DATA_PATH, _build_world_data, _assign_world_data, "countries_by_iso_a3"),
    "lingogrid_languages": (LINGOGRID_DATA_PATH, _build_lingogrid_data, _assign_lingogrid_data, "languages"),
    "lingoguess_texts": (LINGOGUESS_DATA_PATH, _build_lingoguess_data, _assign_lingoguess_data, "texts"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tolerate a freshly mounted, still-empty volume (e.g. right after the
    # first deploy, before the data has been seeded via /admin/data), and
    # tolerate a present-but-corrupt file rather than crash-looping forever
    # with no way to reach the seeding endpoint to fix it.
    try:
        _reload_world_data()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[startup] failed to load world_data: {e}")
    try:
        _reload_lingogrid_data()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[startup] failed to load lingogrid_languages: {e}")
    try:
        _reload_lingoguess_data()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[startup] failed to load lingoguess_texts: {e}")
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
    path, build_fn, assign_fn, required_key = _ADMIN_DATASETS[name]

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict) or required_key not in payload:
        raise HTTPException(status_code=400, detail=f"Payload must be a JSON object with a '{required_key}' key")

    # Validate (and build the in-memory representation) *before* touching
    # disk, so a malformed upload never overwrites the last-known-good file
    # — a corrupt file on disk would otherwise crash-loop the app on the
    # next restart, since only a missing file is tolerated at startup.
    try:
        built = build_fn(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid data: {e}")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp_path, path)

    assign_fn(built)

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
        speakers_by_norm = {norm_text(k): v for k, v in speakers_by_language.items()}
        for ln in lang_norms:
            covered_speakers += _safe_int(speakers_by_norm.get(ln, 0))

    return {
        "input_languages": langs,
        "unknown_languages": unknown,
        "covered_iso_a3": sorted(covered_iso3),
        "covered_count": len(covered_iso3),
        "covered_population": covered_population,
        "covered_speakers_in_countries": covered_speakers,
    }
