"""Discover likely Nepal government vacancy notices and retain run history.

Run on Windows: double-click run_vacancy_scraper.bat (first run installs requirements)
Manual setup:   python -m pip install -r requirements.txt
Manual run:     python it_vacancy_scraper_v2.py
Output:  vacancy_notices.html, vacancy_notices.csv, and vacancy_notices.sqlite3

It reads selectable PDF text locally and includes only notices that pass the
configured open-competition, role, level, and recency rules. Scanned PDFs need
manual review; OCR is intentionally not used.
"""

import csv
import hashlib
import html
import io
import logging
import re
import sqlite3
import time
import unicodedata
import urllib.robotparser
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

try:
    import nepali_datetime
except ImportError:
    nepali_datetime = None

# ------------------------------------------------------------------ CONFIG
# Start with sources identified as the best-supported in the design document.
# Verify each source's current listing page before adding more bodies.
SITES = {
    "Federal Lok Sewa (PSC)": "https://psc.gov.np/",
    "Koshi PSC": "https://psc.koshi.gov.np/",
    "Bagmati PSC": "https://ppsc.bagamati.gov.np/",
    "Sudurpashchim PSC": "https://psc.sudurpashchim.gov.np/",
}

DAYS_BACK = 60
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NepalVacancyMonitor/0.1; low-volume)"
}
DELAY_SECONDS = 2
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
ROBOTS_TIMEOUT = 10
DATABASE_FILE = "vacancy_notices.sqlite3"
CSV_FILE = "vacancy_notices.csv"
HTML_FILE = "vacancy_notices.html"
MAX_SUBPAGES = 4
MAX_CANDIDATES_PER_SOURCE = 20
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 60
NOTICE_WORDS = [
    "vacanc", "career", "notice", "recruit", "advertis", "opening", "job",
    "सूचना", "विज्ञापन", "रिक्त", "दरखास्त", "करार", "नियुक्ति", "भर्ना",
    "पदपूर्ति",
]
VACANCY_WORDS = [
    "vacanc", "recruit", "advertis", "पदपूर्ति", "विज्ञापन",
]
UPDATE_WORDS = [
    "additional vacancy", "deadline extension", "extension of deadline",
    "amendment", "revised vacancy", "म्याद थप", "थप पद", "संशोधन",
    "परिमार्जन", "विज्ञापन संशोधन",
]
RESULT_WORDS = [
    "result", "results", "नामावली", "नतिजा", "परिणाम", "सिफारिस",
    "परीक्षा केन्द्र", "परिक्षा केन्द्र", "सीप परीक्षण", "skill test",
    "admit card", "प्रवेश पत्र", "समय तालिका", "पाठ्यक्रम", "syllabus",
    "answer key", "अन्तर्वार्ता", "interview schedule",
]
OPEN_COMPETITION_WORDS = [
    "open competition", "open competitive", "open and inclusive",
    "open/inclusive", "खुला प्रतियोगितात्मक", "खुला तथा समावेशी",
    "खुला र समावेशी", "खुला प्रतिस्पर्धात्मक", "खुला तर्फ",
]
PROMOTION_WORDS = [
    "promotion", "internal competition", "बढुवा", "आन्तरिक",
]
IT_ROLE_TERMS = [
    "computer officer", "information technology officer", "it officer",
    "कम्प्युटर अधिकृत", "कम्प्यूटर अधिकृत", "सूचना प्रविधि अधिकृत",
    "सूचना प्रबिधि अधिकृत", "आईटी अधिकृत",
]
PUBLICATION_WORDS = [
    "publication date", "published on", "date of publication",
    "प्रकाशन मिति", "प्रकाशित मिति", "विज्ञापन प्रकाशित",
]
LEVEL_SEVEN_WORDS = ["seventh", "सातौं", "सातौँ", "सप्तम"]
LEVEL_OTHER_WORDS = {
    1: ["first", "प्रथम"],
    2: ["second", "दोस्रो"],
    3: ["third", "तेस्रो"],
    4: ["fourth", "चौथो"],
    5: ["fifth", "पाँचौं", "पाँचौँ", "पाचौं"],
    6: ["sixth", "छैटौं", "छैटौँ"],
    8: ["eighth", "आठौं", "आठौँ"],
    9: ["ninth", "नवौं", "नवौँ"],
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger("vacancy_scraper")
_ROBOTS_CACHE = {}
_LAST_REQUEST_AT = {}

# ------------------------------------------------------------------ HELPERS
NEP_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
DATE_RE = re.compile(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})"   # Y-M-D
                     r"|(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})")  # D-M-Y
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
TEXT_DATE_RE = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")


def allowed_by_robots(url):
    """Return whether robots.txt permits the URL; fail closed if unavailable."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    root = "{0.scheme}://{0.netloc}".format(parsed)
    robots_url = root + "/robots.txt"
    if robots_url in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[robots_url].can_fetch(HEADERS["User-Agent"], url)

    try:
        resp = _request(robots_url, timeout=ROBOTS_TIMEOUT)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Allow: /"])
            _ROBOTS_CACHE[robots_url] = parser
            return parser.can_fetch(HEADERS["User-Agent"], url)
        raise RuntimeError(f"could not check robots.txt for {root}: {exc}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"could not check robots.txt for {root}: {exc}") from exc

    if resp.status_code in (401, 403):
        raise RuntimeError(f"robots.txt access denied for {root}")
    if resp.status_code == 404:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(["User-agent: *", "Allow: /"])
    elif 200 <= resp.status_code < 300:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(resp.url)
        parser.parse(resp.text.splitlines())
    else:
        raise RuntimeError(
            f"could not check robots.txt for {root}: HTTP {resp.status_code}"
        )

    _ROBOTS_CACHE[robots_url] = parser
    return parser.can_fetch(HEADERS["User-Agent"], url)


def _request(url, timeout=REQUEST_TIMEOUT, stream=False):
    """Fetch a URL with per-host pacing and bounded retries."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"unsupported URL: {url}")
    host = parsed.netloc.lower()

    for attempt in range(MAX_RETRIES):
        elapsed = time.monotonic() - _LAST_REQUEST_AT.get(host, 0)
        if elapsed < DELAY_SECONDS:
            time.sleep(DELAY_SECONDS - elapsed)
        try:
            response = requests.get(
                url, headers=HEADERS, timeout=timeout, allow_redirects=True,
                stream=stream,
            )
        except requests.RequestException:
            _LAST_REQUEST_AT[host] = time.monotonic()
            if attempt + 1 == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)
            continue

        _LAST_REQUEST_AT[host] = time.monotonic()
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 == MAX_RETRIES:
                response.raise_for_status()
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError(f"request failed after {MAX_RETRIES} attempts: {url}")


def fetch(url):
    """GET HTML with normal TLS certificate verification enabled."""
    response = _request(url)
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def bs_to_ad(y, m, d):
    if nepali_datetime is None:
        return None
    try:
        return nepali_datetime.date(y, m, d).to_datetime_date()
    except Exception:
        return None


def to_ad(y, m, d):
    """Years >= 2000 BS are Nepali (2000-2100); 19xx/20xx AD are English."""
    try:
        if y >= 2070:                       # Bikram Sambat (e.g. 2083)
            return bs_to_ad(y, m, d), "BS"
        return date(y, m, d), "AD"
    except ValueError:
        return None, None


def find_date(text):
    """Return (ad_date, original_string, calendar) or (None, '', '')."""
    text = text.translate(NEP_DIGITS)
    for m in DATE_RE.finditer(text):
        g = m.groups()
        if g[0]:
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
        else:
            d, mo, y = int(g[3]), int(g[4]), int(g[5])
        ad, cal = to_ad(y, mo, d)
        if ad:
            return ad, m.group(0), cal
    m = TEXT_DATE_RE.search(text)
    if m and m.group(1)[:3].lower() in MONTHS:
        try:
            return (date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2))),
                    m.group(0), "AD")
        except ValueError:
            pass
    return None, "", ""


def norm(t):
    """Normalize digits and common Nepali combining marks for exact matching."""
    normalized = unicodedata.normalize("NFC", t)
    normalized = normalized.translate(NEP_DIGITS).lower()
    return unicodedata.normalize(
        "NFC", normalized.replace("\u0902", "").replace("\u0901", "")
    )


def context_text(a):
    """Text around a link (row/list item) so we can find its date."""
    for parent in a.parents:
        if parent.name in ("tr", "li", "article", "div") and len(parent.get_text(strip=True)) < 400:
            return parent.get_text(" ", strip=True)
    return a.get_text(" ", strip=True)


# ------------------------------------------------------------------ DISCOVERY
def get_soup(url):
    """Fetch a robots-checked page."""
    if not allowed_by_robots(url):
        raise RuntimeError(f"blocked by robots.txt: {url}")
    return BeautifulSoup(fetch(url), "html.parser")


def triage_notice(text):
    """Make a conservative first-pass notice classification."""
    value = norm(text)
    if any(norm(word) in value for word in RESULT_WORDS):
        return "result"
    has_open_category = any(
        norm(word) in value for word in OPEN_COMPETITION_WORDS
    )
    if any(norm(word) in value for word in PROMOTION_WORDS) and not has_open_category:
        return "promotion"
    if any(norm(word) in value for word in UPDATE_WORDS):
        return "update"
    if any(norm(word) in value for word in VACANCY_WORDS):
        return "open_vacancy" if has_open_category else "candidate"
    return "other"


def notice_subpages(soup, base):
    """Find same-site links that look like notice/vacancy listing pages."""
    host = urlparse(base).netloc
    found = []
    for a in soup.find_all("a", href=True):
        txt = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        link = urljoin(base, a["href"]).split("#")[0]
        if (urlparse(link).netloc == host and link != base and link not in found
                and not link.lower().endswith((
                    ".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"
                ))
                and any(w in txt for w in NOTICE_WORDS)):
            found.append(link)
    return found[:MAX_SUBPAGES]


def extract(soup, page_url, name, seen_in_run):
    """Collect likely advertisement links; PDF content decides inclusion."""
    out = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(" ", strip=True)
        ctx = context_text(a)
        href = a["href"].strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        link = urljoin(page_url, href)
        if urlparse(link).netloc != urlparse(page_url).netloc:
            continue
        path_text = urlparse(link).path
        notice_text = f"{title} {path_text}"
        notice_type = triage_notice(notice_text)
        if notice_type not in ("candidate", "open_vacancy", "update"):
            continue
        if link in seen_in_run:
            continue
        seen_in_run.add(link)
        out.append({
            "source": name, "title": title or ctx[:100],
            "notice_type": notice_type,
            "listing_context": ctx,
            "link": link,
        })
    return out


def _download_pdf_text(url):
    """Download a size-limited PDF and return its selectable text."""
    if not allowed_by_robots(url):
        raise RuntimeError(f"PDF blocked by robots.txt: {url}")
    response = _request(url, stream=True)
    try:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds the configured download size limit")
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                raise ValueError("PDF exceeds the configured download size limit")
            chunks.append(chunk)
    finally:
        response.close()

    content = b"".join(chunks)
    if not content.startswith(b"%PDF"):
        raise ValueError("linked file is not a PDF")
    try:
        with io.BytesIO(content) as stream:
            document = PdfReader(stream, strict=False)
            if len(document.pages) > MAX_PDF_PAGES:
                raise ValueError("PDF exceeds the configured page limit")
            return "\n".join(page.extract_text() or "" for page in document.pages)
    except PdfReadError as exc:
        raise ValueError(f"could not read PDF: {exc}") from exc


def _is_pdf_url(url):
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".pdf") or ".pdf" in parsed.query.lower()


def notice_pdfs(link):
    """Resolve a candidate PDF directly or through its same-site detail page."""
    parsed = urlparse(link)
    if _is_pdf_url(link):
        return [(link, _download_pdf_text(link))]

    soup = get_soup(link)
    pdf_links = []
    for anchor in soup.find_all("a", href=True):
        pdf_url = urljoin(link, anchor["href"].strip())
        pdf_parsed = urlparse(pdf_url)
        if (pdf_parsed.netloc == parsed.netloc
                and _is_pdf_url(pdf_url)
                and pdf_url not in pdf_links):
            pdf_links.append(pdf_url)
    for element in soup.find_all(["iframe", "object", "embed"]):
        source = element.get("src") or element.get("data")
        if not source:
            continue
        pdf_url = urljoin(link, source)
        if (urlparse(pdf_url).netloc == parsed.netloc
                and _is_pdf_url(pdf_url)
                and pdf_url not in pdf_links):
            pdf_links.append(pdf_url)

    documents = []
    failures = []
    for pdf_url in pdf_links[:3]:
        try:
            documents.append((pdf_url, _download_pdf_text(pdf_url)))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            failures.append(f"{pdf_url}: {exc}")
    if not documents and failures:
        raise RuntimeError("no readable PDF attachments; " + "; ".join(failures))
    return documents


def _publication_date(text, listing_context):
    lines = text.splitlines()
    for index in range(len(lines)):
        section = " ".join(lines[max(0, index - 1):index + 2])
        normalized = norm(section)
        if any(norm(word) in normalized for word in PUBLICATION_WORDS):
            converted, original, calendar = find_date(section)
            if converted:
                return converted, original, calendar, "PDF"
    converted, original, calendar = find_date(listing_context)
    if converted:
        return converted, original, calendar, "listing"
    return None, "", "", ""


def _level_in_context(text):
    normalized = norm(text)
    found = set()
    for match in re.finditer(
        r"\b([1-9])\s*(?:st|nd|rd|th|औ)?\s*(?:level|तह)\b"
        r"|\b(?:level|तह)\s*([1-9])\b",
        normalized,
    ):
        found.add(int(match.group(1) or match.group(2)))
    for level, words in LEVEL_OTHER_WORDS.items():
        if any(
            norm(word) in normalized
            and re.search(r"(?:level|तह)", normalized)
            for word in words
        ):
            found.add(level)
    if any(norm(word) in normalized for word in LEVEL_SEVEN_WORDS):
        if re.search(r"(?:level|तह)", normalized):
            found.add(7)
    return found


def verify_notice(candidate, pdf_text, today=None):
    """Require open-competition, role, applicable level, and recent-date evidence."""
    today = today or date.today()
    text = unicodedata.normalize("NFC", pdf_text)
    normalized = norm(text)
    has_open = any(norm(word) in normalized for word in OPEN_COMPETITION_WORDS)
    if not has_open:
        return None

    has_promotion = any(norm(word) in normalized for word in PROMOTION_WORDS)
    def nearest_distance(position, terms):
        distances = []
        for term in terms:
            normalized_term = norm(term)
            search_from = 0
            while True:
                marker_position = normalized.find(normalized_term, search_from)
                if marker_position < 0:
                    break
                distances.append(abs(position - marker_position))
                search_from = marker_position + len(normalized_term)
        return min(distances, default=len(normalized) + 1)

    matches = []
    for role in IT_ROLE_TERMS:
        normalized_role = norm(role)
        start = 0
        while True:
            index = normalized.find(normalized_role, start)
            if index < 0:
                break
            start = index + len(normalized_role)
            context = normalized[max(0, index - 500):index + len(normalized_role) + 500]
            if has_promotion:
                nearest_open = nearest_distance(index, OPEN_COMPETITION_WORDS)
                nearest_promotion = nearest_distance(index, PROMOTION_WORDS)
                if (nearest_promotion <= nearest_open
                        or not any(norm(word) in context
                                   for word in OPEN_COMPETITION_WORDS)):
                    continue

            levels = _level_in_context(context)
            all_levels = _level_in_context(normalized)
            if 7 in levels:
                if levels - {7}:
                    continue
                level = "7th"
            elif levels:
                continue
            elif all_levels:
                continue
            else:
                level = "Not stated"
            matches.append((role, level))
    if not matches:
        return None

    publication, original, calendar, date_source = _publication_date(
        text, candidate.get("listing_context", "")
    )
    if publication is None or publication > today:
        return None
    if (today - publication).days > DAYS_BACK:
        return None

    role, level = matches[0]
    notice = dict(candidate)
    notice.update({
        "title": candidate.get("title") or role,
        "notice_type": "open_vacancy_mixed" if has_promotion else "open_vacancy",
        "role": role,
        "level": level,
        "date_found": original,
        "calendar": calendar,
        "date_AD": publication.isoformat(),
        "date_BS": (
            nepali_datetime.date.from_datetime_date(publication).strftime("%Y-%m-%d")
            if nepali_datetime else "unknown"
        ),
        "date_source": date_source,
        "match_evidence": (
            f"PDF text: open competition; {role}; level {level}; "
            f"publication date within {DAYS_BACK} days ({date_source})"
        ),
        "it_status": "rule_match",
        "match_score": 100,
        "document_link": candidate.get("document_link", ""),
        "link": candidate.get("link", ""),
    })
    return notice


def scrape():
    results, report = [], []

    for name, url in SITES.items():
        print(f"Scraping {name} ...")
        seen_in_run, pages_ok, found = set(), 0, 0
        candidates = []
        try:
            home = get_soup(url)
            pages = [(url, home)]
            notes = []
            for sub in notice_subpages(home, url):       # go one level deeper
                try:
                    pages.append((sub, get_soup(sub)))
                except (requests.RequestException, RuntimeError, ValueError) as exc:
                    notes.append(f"sub-page failed: {sub[-40:]} ({str(exc)[:50]})")
            for page_url, soup in pages:
                pages_ok += 1
                hits = extract(soup, page_url, name, seen_in_run)
                found += len(hits)
                candidates.extend(hits)
            if not candidates:
                notes.append(
                    "no advertisement candidate links found; verify listing-page coverage"
                )
            verified = []
            skipped = max(0, len(candidates) - MAX_CANDIDATES_PER_SOURCE)
            for candidate in candidates[:MAX_CANDIDATES_PER_SOURCE]:
                matched = False
                try:
                    documents = notice_pdfs(candidate["link"])
                    for document_link, document_text in documents:
                        candidate["document_link"] = document_link
                        match = verify_notice(candidate, document_text)
                        if match:
                            verified.append(match)
                            matched = True
                            break
                except (requests.RequestException, RuntimeError, ValueError) as exc:
                    LOGGER.warning("Skipping %s: %s", candidate["link"], exc)
                    notes.append(
                        f"notice skipped ({candidate['link'][-35:]}): {str(exc)[:70]}"
                    )
                if not matched:
                    skipped += 1
            results.extend(verified)
            if len(candidates) > MAX_CANDIDATES_PER_SOURCE:
                notes.append("candidate limit reached; some notices were not checked")
            summary = (
                f"{pages_ok} page(s), {len(verified)} qualifying notice(s), "
                f"{skipped} candidate(s) excluded"
            )
            details = "; ".join([summary] + notes)
            report.append((name, "WARN" if notes else "OK", details))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            report.append((name, "FAILED", str(exc)[:100]))
            LOGGER.error("%s failed: %s", name, exc)

    results.sort(key=lambda r: r["date_AD"], reverse=True)
    return results, report


def _fingerprint(notice):
    fields = (
        notice.get("title", ""),
        notice.get("notice_type", ""),
        notice.get("date_found", ""),
        notice.get("date_AD", ""),
        notice.get("role", ""),
        notice.get("level", ""),
        notice.get("match_evidence", ""),
        notice.get("document_link", ""),
    )
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def save_notices(notices, database_file=DATABASE_FILE, now=None):
    """Upsert discovered notices and label each as new, changed, or unchanged."""
    timestamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection = sqlite3.connect(database_file)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                source TEXT NOT NULL,
                link TEXT NOT NULL,
                title TEXT NOT NULL,
                notice_type TEXT NOT NULL,
                date_found TEXT NOT NULL,
                calendar TEXT NOT NULL,
                date_ad TEXT NOT NULL,
                date_bs TEXT NOT NULL,
                match_score INTEGER NOT NULL,
                it_status TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                PRIMARY KEY (source, link)
            )
        """)
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(notices)")
        }
        added_columns = {
            "role": "TEXT NOT NULL DEFAULT ''",
            "level": "TEXT NOT NULL DEFAULT ''",
            "date_source": "TEXT NOT NULL DEFAULT ''",
            "match_evidence": "TEXT NOT NULL DEFAULT ''",
            "document_link": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in added_columns.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE notices ADD COLUMN {column} {definition}"
                )
        for notice in notices:
            fingerprint = _fingerprint(notice)
            existing = connection.execute(
                "SELECT fingerprint, first_seen FROM notices "
                "WHERE source = ? AND link = ?",
                (notice["source"], notice["link"]),
            ).fetchone()
            if existing is None:
                status, first_seen = "NEW", timestamp
            else:
                status = "CHANGED" if existing[0] != fingerprint else ""
                first_seen = existing[1]
            notice["status"] = status
            connection.execute("""
                INSERT INTO notices (
                    source, link, title, notice_type, date_found, calendar,
                    date_ad, date_bs, match_score, it_status, fingerprint,
                    first_seen, last_seen, role, level, date_source,
                    match_evidence, document_link
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, link) DO UPDATE SET
                    title = excluded.title,
                    notice_type = excluded.notice_type,
                    date_found = excluded.date_found,
                    calendar = excluded.calendar,
                    date_ad = excluded.date_ad,
                    date_bs = excluded.date_bs,
                    match_score = excluded.match_score,
                    it_status = excluded.it_status,
                    fingerprint = excluded.fingerprint,
                    last_seen = excluded.last_seen,
                    role = excluded.role,
                    level = excluded.level,
                    date_source = excluded.date_source,
                    match_evidence = excluded.match_evidence,
                    document_link = excluded.document_link
            """, (
                notice["source"], notice["link"], notice["title"],
                notice["notice_type"], notice["date_found"], notice["calendar"],
                notice["date_AD"], notice["date_BS"], notice["match_score"],
                notice["it_status"], fingerprint, first_seen, timestamp,
                notice.get("role", ""), notice.get("level", ""),
                notice.get("date_source", ""), notice.get("match_evidence", ""),
                notice.get("document_link", ""),
            ))
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()
    return notices


CSV_FIELDS = [
    "status", "source", "title", "notice_type", "role", "level",
    "date_found", "calendar", "date_AD", "date_BS", "date_source",
    "match_evidence", "document_link", "link",
]


def write_csv(notices, csv_file=CSV_FILE):
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(notices)


def write_html_report(notices, html_file=HTML_FILE, site_report=None):
    """Write a self-contained, browser-friendly report of the latest scan."""
    rows = []
    for notice in notices:
        link = notice.get("document_link") or notice.get("link", "")
        parsed_link = urlparse(link)
        link_label = (
            "Open PDF" if parsed_link.path.lower().endswith(".pdf")
            else "View official notice"
        )
        if parsed_link.scheme in ("http", "https") and parsed_link.netloc:
            notice_link = (
                f'<a class="notice-link" href="{html.escape(link, quote=True)}" '
                'target="_blank" rel="noopener noreferrer">'
                f"{link_label} ↗</a>"
            )
        else:
            notice_link = "Link unavailable"

        status = notice.get("status", "")
        status_class = status.lower() if status in ("NEW", "CHANGED") else "seen"
        rows.append(
            "<tr>"
            f'<td><span class="badge {status_class}">'
            f"{html.escape(status or 'SEEN')}</span></td>"
            f"<td>{html.escape(str(notice.get('source', '')))}</td>"
            f'<td class="title">{html.escape(str(notice.get("title", "")))}</td>'
            f"<td>{html.escape(str(notice.get('notice_type', '')).replace('_', ' ').title())}</td>"
            f"<td>{html.escape(str(notice.get('role', '')))}</td>"
            f"<td>{html.escape(str(notice.get('level', '')))}</td>"
            f"<td>{html.escape(str(notice.get('date_AD', 'unknown')))}</td>"
            f"<td>{html.escape(str(notice.get('date_BS', 'unknown')))}</td>"
            f'<td><span class="assessment">Rule checks passed</span>'
            f'<div class="score">{html.escape(str(notice.get("match_evidence", "")))}</div></td>'
            f"<td>{notice_link}</td>"
            "</tr>"
        )

    if not rows:
        table_body = (
            '<tr><td class="empty" colspan="10">'
            "No notices passed all configured filters in this scan."
            "</td></tr>"
        )
    else:
        table_body = "\n".join(rows)

    new_count = sum(n.get("status") == "NEW" for n in notices)
    changed_count = sum(n.get("status") == "CHANGED" for n in notices)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    health_rows = []
    for source, status, note in site_report or []:
        status_class = status.lower()
        health_rows.append(
            "<tr>"
            f"<td><span class=\"badge {status_class}\">{html.escape(status)}</span></td>"
            f"<td>{html.escape(source)}</td>"
            f"<td>{html.escape(note)}</td>"
            "</tr>"
        )
    health_section = ""
    if health_rows:
        health_section = f"""
  <section class="health">
    <h2>Source health</h2>
    <p class="subtitle">An empty results table can also mean a source failed or
      its listing page needs updating. Check warnings before concluding there are
      no current vacancies.</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Status</th><th>PSC source</th><th>Scan details</th></tr></thead>
        <tbody>{"".join(health_rows)}</tbody>
      </table>
    </div>
  </section>
"""
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nepal vacancy notice report</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, Segoe UI, sans-serif; }}
    body {{ margin: 0; background: #f3f6fa; color: #172033; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 32px 20px 56px; }}
    h1 {{ margin: 0 0 6px; font-size: clamp(1.6rem, 3vw, 2.2rem); }}
    .subtitle, .note {{ color: #536176; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 24px 0; }}
    .card {{ background: white; border: 1px solid #dfe6ef; border-radius: 10px;
             padding: 14px 18px; min-width: 120px; }}
    .number {{ display: block; font-size: 1.5rem; font-weight: 700; }}
    .label {{ color: #536176; font-size: .88rem; }}
    .note {{ background: #fff8e6; border-left: 4px solid #d99a16;
             border-radius: 4px; padding: 12px 14px; line-height: 1.5; }}
    .toolbar {{ display: flex; justify-content: flex-end; margin: 18px 0 10px; }}
    input {{ width: min(100%, 360px); padding: 10px 12px; border: 1px solid #cbd5e1;
             border-radius: 7px; font: inherit; }}
    .table-wrap {{ overflow-x: auto; background: white; border: 1px solid #dfe6ef;
                   border-radius: 10px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 900px; }}
    th, td {{ text-align: left; padding: 12px 14px; border-bottom: 1px solid #e7edf4;
              vertical-align: top; }}
    th {{ background: #f8fafc; color: #475569; font-size: .82rem; white-space: nowrap; }}
    tr:last-child td {{ border-bottom: 0; }}
    .title {{ min-width: 240px; font-weight: 600; }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 3px 9px;
              font-size: .75rem; font-weight: 700; }}
    .new {{ color: #075e38; background: #d9f7e8; }}
    .changed {{ color: #824500; background: #fff0cb; }}
    .seen {{ color: #475569; background: #e9eef5; }}
    .assessment {{ font-weight: 600; }}
    .score {{ color: #64748b; font-size: .8rem; margin-top: 4px; }}
    .notice-link {{ color: #075bbb; font-weight: 650; white-space: nowrap; }}
    .empty {{ text-align: center; padding: 32px; color: #64748b; }}
    .health {{ margin-top: 32px; }}
    .health td:last-child {{ max-width: 720px; overflow-wrap: anywhere; }}
    footer {{ margin-top: 16px; color: #64748b; font-size: .85rem; }}
  </style>
</head>
<body>
<main>
  <h1>Nepal vacancy notice report</h1>
  <div class="subtitle">Latest scan · generated {html.escape(generated)}</div>
  <section class="summary" aria-label="Scan summary">
    <div class="card"><span class="number">{len(notices)}</span>
      <span class="label">matched notices</span></div>
    <div class="card"><span class="number">{new_count}</span>
      <span class="label">new</span></div>
    <div class="card"><span class="number">{changed_count}</span>
      <span class="label">changed</span></div>
  </section>
  <p class="note"><strong>Scope:</strong> This report checks PSC sources only.
    Any listed notice passed text-based rules for open competition, an IT/Computer
    Officer role, applicable level, and publication date. This is a rule match, not
    a guarantee that every detail was interpreted correctly. Open the PDF and verify
    the post, category, and deadline before acting.</p>
  <div class="toolbar">
    <input id="filter" type="search" placeholder="Filter by title, role, source, or level"
      aria-label="Filter notices">
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Status</th><th>Source</th><th>Notice</th><th>Type</th>
        <th>Role</th><th>Level</th><th>Date (AD)</th><th>Date (BS)</th>
        <th>Rule evidence</th>
        <th>Official link</th></tr></thead>
      <tbody id="notices">{table_body}</tbody>
    </table>
  </div>
{health_section}
  <footer>Links open the discovered official notice URL. Report contains this scan only;
    history is stored in the local SQLite database.</footer>
</main>
<script>
  const filter = document.getElementById("filter");
  filter.addEventListener("input", () => {{
    const query = filter.value.toLocaleLowerCase();
    document.querySelectorAll("#notices tr").forEach(row => {{
      row.hidden = !row.textContent.toLocaleLowerCase().includes(query);
    }});
  }});
</script>
</body>
</html>
"""
    with open(html_file, "w", encoding="utf-8") as output:
        output.write(document)


def main():
    data, report = scrape()
    save_notices(data)
    write_csv(data)
    write_html_report(data, site_report=report)

    print(f"\n=== {len(data)} rule-matched notices (last {DAYS_BACK} days), "
          f"{sum(r['status'] == 'NEW' for r in data)} new, "
          f"{sum(r['status'] == 'CHANGED' for r in data)} changed ===\n")
    print(f"HTML report: {HTML_FILE}")
    for r in data:
        print(f"{r['status']:7} [{r['date_AD']} | {r['date_BS']}] "
              f"{r['source']}: {r['title'][:80]}")
        print(f"      {r.get('document_link') or r['link']}")
        print(f"      {r['match_evidence']}")

    print("\n=== SITE HEALTH (FAILED = fetch failure; WARN = incomplete coverage; OK = scan completed) ===")
    for name, status, note in report:
        print(f"{status:6} {name:30} {note[:140]}")


if __name__ == "__main__":
    main()