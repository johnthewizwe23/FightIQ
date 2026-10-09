"""Paths and source URLs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODEL_PATH = ROOT / "models" / "fightiq_model.json"
OUTPUT_DIR = ROOT / "output"

# Daily-refreshed scrape of ufcstats.com (https://github.com/Greco1899/scrape_ufc_stats).
GRECO_BASE = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main"
GRECO_FILES = [
    "ufc_event_details.csv",
    "ufc_fight_results.csv",
    "ufc_fight_stats.csv",
    "ufc_fighter_tott.csv",
]

# Historical betting odds, used only to benchmark the model against the market
# (https://github.com/shortlikeafox/ultimate_ufc_dataset).
ODDS_URL = "https://raw.githubusercontent.com/shortlikeafox/ultimate_ufc_dataset/master/ufc-master.csv"
ODDS_FILE = "ufc-master.csv"

UFCSTATS_UPCOMING = "http://ufcstats.com/statistics/events/upcoming"
