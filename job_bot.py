import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

# ==================================================
# GLOBAL COMPUTER - SETTINGS
# ==================================================

BASE = Path(__file__).resolve().parent

BRAND_NAME = "GLOBAL COMPUTER"
ADDRESS = "Bank of India ની સામે, Paddhari"
MOBILE = "88666 30207"

SITES = {
    "Sarkari Result": "https://sarkariresult.com.cm/",
    "Maru Gujarat": "https://www.marugujarat.in/gujarat-state-jobs/",
}

DB_FILE = BASE / "jobs_database.json"
OUTPUT_FILE = BASE / "new_jobs.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
TIMEOUT = 25

# Dates without a published time are treated as ending at 23:59.
# The bot should run every 15 minutes using Windows Task Scheduler.
REMINDERS = [
    ("FOUND", "📢 નવી ભરતી મળી!"),
    ("AFTER_1_HOUR", "⏰ ભરતી મળ્યાને 1 કલાક થયો"),
    ("AFTER_1_DAY", "📅 ભરતી મળ્યાને 1 દિવસ થયો"),
    ("BEFORE_1_DAY", "⚠️ છેલ્લી તારીખ આવતીકાલે છે"),
    ("BEFORE_3_HOURS", "🚨 છેલ્લી તારીખમાં 3 કલાક બાકી"),
    ("BEFORE_1_HOUR", "🔴 છેલ્લી તારીખમાં 1 કલાક બાકી"),
]

JOB_WORDS = [
    "recruitment", "bharti", "vacancy", "apprentice",
    "teacher", "constable", "clerk", "operator",
    "assistant", "officer", "engineer", "stenographer",
    "forester", "police", "anganwadi", "group c",
    "apply online", "online form", "government job",
    "government jobs", "latest job", "ગુજરાત ભરતી",
    "ભરતી", "નોકરી"
]

IGNORE_WORDS = [
    "admit card", "answer key", "score card", "syllabus",
    "exam calendar", "exam date", "result", "cut off",
    "cutoff", "merit list", "old paper", "previous paper",
    "mock test", "dummy", "admission"
]

MONTHS = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|"
    r"Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

DATE_RE = re.compile(
    r"\b("
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}\.\d{1,2}\.\d{2,4}"
    rf"|\d{{1,2}}\s+(?:{MONTHS})\.?,?\s+\d{{4}}"
    rf"|(?:{MONTHS})\.?\s+\d{{1,2}},?\s+\d{{4}}"
    r")\b",
    re.IGNORECASE
)

START_LABELS = [
    "application start date",
    "starting date of application",
    "online application start date",
    "registration start date",
    "application begins",
    "apply online starts",
    "start date",
    "application starts"
]

END_LABELS = [
    "last date to apply",
    "last date for apply",
    "last date of application",
    "application last date",
    "last date",
    "closing date",
    "apply online last date",
    "registration last date",
    "online application last date",
    "last date for submission",
    "application ends",
    "application end date",
    "અરજી કરવાની છેલ્લી તારીખ",
    "છેલ્લી તારીખ"
]


# ==================================================
# DATABASE
# ==================================================

def load_db():
    if not DB_FILE.exists():
        return {}

    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        print("Warning: database વાંચી શકાયો નથી.")
        return {}


def save_db(db):
    DB_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def now():
    # Uses the Windows PC's local time.
    return datetime.now().replace(second=0, microsecond=0)


# ==================================================
# WEBSITE HELPERS
# ==================================================

def get_soup(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_url(url):
    return urldefrag(url)[0].rstrip("/")


def is_job(title):
    lower = title.lower()

    if any(word in lower for word in IGNORE_WORDS):
        return False

    return any(word in lower for word in JOB_WORDS)


def scan_listing(source, listing_url):
    soup = get_soup(listing_url)
    host = (urlparse(listing_url).hostname or "").lower()
    found = {}

    for a in soup.select("a[href]"):
        title = " ".join(a.get_text(" ", strip=True).split())
        url = clean_url(urljoin(listing_url, a["href"]))

        if len(title) < 5 or not is_job(title):
            continue

        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            continue

        if (parsed.hostname or "").lower() != host:
            continue

        if url == clean_url(listing_url):
            continue

        container = (
            a.find_parent(["article", "li", "tr"])
            or a.parent
        )
        context = " ".join(container.stripped_strings)

        start_date, deadline = extract_dates(context)

        found[url] = {
            "title": title,
            "url": url,
            "source": source,
            "start_date": start_date.isoformat() if start_date else None,
            "deadline": deadline.isoformat() if deadline else None,
        }

    return found


# ==================================================
# DATE EXTRACTION
# ==================================================

def parse_date(value):
    value = value.strip().replace(",", "")
    value = re.sub(
        r"(\d)(st|nd|rd|th)\b",
        r"\1",
        value,
        flags=re.IGNORECASE
    )

    formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        "%d-%m-%y", "%d/%m/%y", "%d.%m.%y",
        "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y"
    ]

    for fmt in formats:
        try:
            result = datetime.strptime(value, fmt).date()
            if 2000 <= result.year <= 2100:
                return result
        except ValueError:
            continue

    return None


def find_date(text, labels):
    lower = text.lower()
    candidates = []

    for label in sorted(labels, key=len, reverse=True):
        start = 0

        while True:
            position = lower.find(label, start)

            if position == -1:
                break

            section = text[position:position + 250]

            for match in DATE_RE.finditer(section):
                parsed = parse_date(match.group(1))
                if parsed:
                    candidates.append((position, parsed))
                    break

            start = position + len(label)

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def extract_dates(text):
    text = re.sub(r"\s+", " ", text)

    start_date = find_date(text, START_LABELS)
    deadline = find_date(text, END_LABELS)

    return start_date, deadline


def inspect_job(job):
    start = (
        datetime.fromisoformat(job["start_date"]).date()
        if job.get("start_date") else None
    )
    deadline = (
        datetime.fromisoformat(job["deadline"]).date()
        if job.get("deadline") else None
    )

    if start is None or deadline is None:
        try:
            soup = get_soup(job["url"])
            text = " ".join(soup.get_text(" ", strip=True).split())

            found_start, found_deadline = extract_dates(text)

            if start is None:
                start = found_start

            if deadline is None:
                deadline = found_deadline

        except requests.RequestException as error:
            print("Detail page error:", error)

    job["start_date"] = start.isoformat() if start else None
    job["deadline"] = deadline.isoformat() if deadline else None


# ==================================================
# MESSAGE FORMAT
# ==================================================

def read_date(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def deadline_datetime(job):
    value = job.get("deadline")

    if not value:
        return None

    try:
        day = datetime.fromisoformat(value).date()
        return datetime.combine(day, datetime.max.time()).replace(
            second=0, microsecond=0
        )
    except (ValueError, TypeError):
        return None


def message(job, heading, current_time):
    start = read_date(job.get("start_date"))
    deadline = read_date(job.get("deadline"))
    deadline_dt = deadline_datetime(job)

    if deadline_dt is None:
        remaining = "⏳ બાકી દિવસો: છેલ્લી તારીખ મળી નથી"
    else:
        seconds = int((deadline_dt - current_time).total_seconds())

        if seconds < 0:
            remaining = "❌ અરજીની છેલ્લી તારીખ પૂરી થઈ ગઈ"
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            remaining = f"⏳ બાકી: {hours} કલાક {minutes} મિનિટ"
        else:
            days = seconds // 86400
            remaining = f"⏳ બાકી દિવસો: {days} દિવસ"

    return "\n".join([
        heading,
        f"📌 ભરતી: {job['title']}",
        f"🌐 {BRAND_NAME}",
        f"📅 અરજી શરૂ: {start.strftime('%d-%m-%Y') if start else 'મળી નથી'}",
        f"📅 છેલ્લી તારીખ: {deadline.strftime('%d-%m-%Y') if deadline else 'મળી નથી'}",
        remaining,
        f"🔗 {job['url']}",
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"💻 {BRAND_NAME}",
        f"📍 સરનામું: {ADDRESS}",
        f"📞 સંપર્ક: {MOBILE}",
        "━━━━━━━━━━━━━━━━━━",
        "⚠️ અરજી પહેલાં સત્તાવાર નોટિફિકેશનમાં તારીખ ચકાસો.",
        ""
    ])


# ==================================================
# REMINDER SCHEDULER
# ==================================================

def due_time(key, found_at, deadline_dt):
    if key == "FOUND":
        return found_at

    if key == "AFTER_1_HOUR":
        return found_at + timedelta(hours=1)

    if key == "AFTER_1_DAY":
        return found_at + timedelta(days=1)

    if key == "BEFORE_1_DAY" and deadline_dt:
        return deadline_dt - timedelta(days=1)

    if key == "BEFORE_3_HOURS" and deadline_dt:
        return deadline_dt - timedelta(hours=3)

    if key == "BEFORE_1_HOUR" and deadline_dt:
        return deadline_dt - timedelta(hours=1)

    return None


def prepare_due_messages(db, current_time):
    output = []

    for url, job in db.items():
        found_at = read_date(job.get("found_at"))

        if found_at is None:
            continue

        deadline_dt = deadline_datetime(job)
        sent = job.setdefault("sent", [])

        for key, heading in REMINDERS:
            if key in sent:
                continue

            scheduled = due_time(key, found_at, deadline_dt)

            if scheduled is None or current_time < scheduled:
                continue

            # Deadline reminders are not useful after expiry.
            if key.startswith("BEFORE_"):
                if deadline_dt is None:
                    continue

                if current_time >= deadline_dt:
                    sent.append(key)
                    continue

            # Do not announce an expired job as newly found.
            if key == "FOUND" and deadline_dt:
                if current_time >= deadline_dt:
                    sent.append(key)
                    continue

            output.append(message(job, heading, current_time))
            sent.append(key)

    return output


# ==================================================
# MAIN BOT
# ==================================================

def main():
    current_time = now()

    print("=" * 50)
    print(BRAND_NAME, "- GOVERNMENT JOB BOT")
    print("Local time:", current_time.strftime("%d-%m-%Y %H:%M"))
    print("=" * 50)

    db = load_db()
    discovered = 0

    for source, listing_url in SITES.items():
        print(f"\nChecking {source}...")

        try:
            listings = scan_listing(source, listing_url)
            print("Links found:", len(listings))

        except requests.RequestException as error:
            print("Website error:", error)
            continue

        for url, job in listings.items():
            if url not in db:
                inspect_job(job)

                db[url] = {
                    **job,
                    "found_at": current_time.isoformat(),
                    "sent": []
                }

                discovered += 1
                print("New listing:", job["title"][:65])

            else:
                existing = db[url]

                # Try to find dates again if still missing.
                if not existing.get("deadline") or not existing.get("start_date"):
                    inspect_job(existing)

                existing["title"] = job["title"]
                existing["source"] = job["source"]
                existing["url"] = url

                if not existing.get("start_date") and job.get("start_date"):
                    existing["start_date"] = job["start_date"]

                if not existing.get("deadline") and job.get("deadline"):
                    existing["deadline"] = job["deadline"]

                if not isinstance(existing.get("sent"), list):
                    existing["sent"] = []

    output = prepare_due_messages(db, current_time)

    save_db(db)

    if output:
        with OUTPUT_FILE.open("a", encoding="utf-8") as file:
            file.write("\n" + "\n".join(output) + "\n")

        print(f"\nMessages prepared: {len(output)}")
        print("Check new_jobs.txt")

    else:
        print("\nઆ સમયે કોઈ નવો reminder due નથી.")

    if discovered:
        print("New links saved:", discovered)

    print("Scan completed.")


if __name__ == "__main__":
    main()
