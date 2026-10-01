"""Turns the crawler's cached HTML into clean documents for the index.

Input: the raw page cache (scraper.py) and the committed, hand written
supplementary sources (sources/public_sources.jsonl).
Output: KNOWLEDGE_DIR/docs.jsonl, one document per page (or per group of
Nuxt store items), each with source_url and retrieved_at, sections of
heading plus text lines, detected packages and detected FAQ pairs.
Optionally (--write-faq) the committed faq.jsonl candidates file, which is
then curated by hand; the build never overwrites the curated file.

What "clean" means here:
    * Scripts, styles, forms, navigation, header and footer are dropped.
    * Boilerplate is removed statistically: a text block that appears on
      many pages of the same host (menus, login blurbs, "Looking for..."
      lists) is dropped everywhere. That is more robust than per site CSS
      selectors, which break silently when a theme changes.
    * Headings are kept (as section boundaries) because they carry the
      package or topic name that the lines below them depend on.
    * Tables become "Label: value" lines, so a row such as
      "Plan Price: Rs. 58; Anytime Bundle: 512 MB; Validity: 7 Days" stays
      one retrievable unit instead of a column soup.
    * "Submitted by <staff email> on <date>" bylines on the legacy site
      are removed (the date is kept as page_date metadata), and e-mail
      addresses that are not generic role mailboxes are removed, so no
      personal data reaches the index.

Package detection: price (Rs/LKR), data allowance (GB/MB), validity and
name are read from table cells and labels; a multi row price table yields
one package per row, and single record tables under one heading are
merged into one package named by its "Marketing Name" (or similar) field
or, failing that, by the heading.

Run: python -m backend.knowledge.normalize [--write-faq]
"""

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup, NavigableString, Tag

import config
from knowledge.nuxt_state import extract_nuxt_state
from knowledge.scraper import cached_pages, read_cached

KNOWLEDGE_PACKAGE_DIR = Path(__file__).resolve().parent
PUBLIC_SOURCES_PATH = KNOWLEDGE_PACKAGE_DIR / "sources" / "public_sources.jsonl"
FAQ_PATH = KNOWLEDGE_PACKAGE_DIR / "faq.jsonl"
DOCS_PATH = config.KNOWLEDGE_DIR / "docs.jsonl"
FAQ_CANDIDATES_PATH = config.KNOWLEDGE_DIR / "faq_candidates.jsonl"

_DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "form", "button", "select", "input",
              "textarea", "nav", "header", "footer", "img", "video", "audio", "link", "meta")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCK_TAGS = ("p", "div", "li", "ul", "ol", "section", "article", "dd", "dt", "dl", "tr", "blockquote",
               "pre", "main", "aside", "figure", "figcaption", "center", "tbody", "thead")
# Elements whose class or id names them as page chrome rather than content.
_CHROME = re.compile(r"(^|[\s_-])(menu|navbar|breadcrumb|cookie|social|share|login|search|"
                     r"footer|header|sidebar|skip-link|element-invisible|visually-hidden)([\s_-]|$)", re.I)

_PRICE = re.compile(r"(?<![A-Za-z])(?:Rs\.?|LKR|රු\.?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
_DATA = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(GB|MB|TB)\b", re.I)
_VALIDITY = re.compile(r"\b([0-9]+)\s*(day|days|month|months|hours?|hrs|weeks?|year|years)\b", re.I)
_BYLINE = re.compile(r"Submitted by\s+\S+\s+on\s+\w{3},\s*(\d{2}/\d{2}/\d{4})(?:\s*-\s*\d{1,2}:\d{2})?", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ROLE_MAILBOXES = {"info", "customercare", "customer.care", "cc", "support", "contact", "care", "enquiries",
                   "inquiries", "sales", "feedback", "helpdesk", "service", "services", "hotline", "ir",
                   "investors", "media", "press", "complaints", "noreply", "no-reply", "webmaster", "mcash",
                   "enterprise", "business", "roaming", "idd", "privacy", "dpo"}
# Raw markup that leaked into text (for example a map widget's broken
# attribute string on the store finder page).
_MARKUP_DEBRIS = re.compile(r'[a-z_]+="[^"]*"\s*/?>?|^"\s*\w+=')
_QUESTION_START = re.compile(r"^\s*(?:Q\s*[:.)]?\s*)?(?:\d+\s*[.)]\s*)?", re.I)
# Label keywords, most specific first: the first label that matches wins.
_NAME_LABELS = ("marketing name", "package name", "plan name", "product name", "name of the package",
                "bundle name", "details", "package", "plan", "name")
# Labels that on their own make a priced record a package.
_STRONG_NAME_LABELS = ("marketing name", "package name", "plan name", "product name", "name of the package")
_PRICE_LABELS = ("plan price", "package price", "price", "monthly rental", "rental", "fee", "charge", "cost")
_VALIDITY_LABELS = ("validity", "valid", "duration", "period")
_DATA_LABELS = ("anytime data", "anytime bundle", "data bundle", "data", "anytime", "quota", "volume",
                "internet", "bundle")
_NOT_PRICE_LABELS = ("limit", "deposit", "trc", "reference", "date", "status")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip(" \t\r\n|")


def _redact_emails(text: str) -> str:
    def replace(match):
        local = match.group(0).split("@", 1)[0].lower()
        return match.group(0) if local in _ROLE_MAILBOXES else ""
    return _EMAIL.sub(replace, text)


def detect_language(text: str) -> str:
    sinhala = sum(1 for ch in text if "඀" <= ch <= "෿")
    tamil = sum(1 for ch in text if "஀" <= ch <= "௿")
    letters = sum(1 for ch in text if ch.isalpha()) or 1
    if sinhala / letters > 0.3:
        return "si"
    if tamil / letters > 0.3:
        return "ta"
    return "en"


# HTML to blocks

class _Block:
    __slots__ = ("kind", "text", "level", "records")

    def __init__(self, kind: str, text: str = "", level: int = 0, records: Optional[list] = None):
        self.kind = kind  # "heading", "text" or "table"
        self.text = text
        self.level = level
        self.records = records or []


def _cell_text(cell: Tag) -> str:
    for br in cell.find_all("br"):
        br.replace_with(" ")
    return _clean(cell.get_text(" "))


def _cell_label_value(cell: Tag) -> Optional[tuple]:
    """A cell written as <strong>Label</strong><br/>value holds its own
    label; returns (label, value) for it."""
    first = cell.find(["strong", "b"])
    if not first:
        return None
    label = _clean(first.get_text(" "))
    full = _cell_text(cell)
    if not label or not full.startswith(label) or len(label) > 60:
        return None
    value = _clean(full[len(label):].lstrip(":"))
    return (label.rstrip(":"), value) if value else None


def _cell_pairs(cell: Tag) -> dict:
    """Reads a cell laid out as a list of "Label :" headings each followed
    by its value (the legacy site's per plan cards). Empty when the cell
    has fewer than two such labels."""
    pieces = []
    for node in cell.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "span", "div"]):
        if node.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "div"]):
            continue  # a container; its children are visited on their own
        text = _clean(node.get_text(" "))
        if text and (not pieces or pieces[-1] != text):
            pieces.append(text)
    pairs, label = {}, None
    for text in pieces:
        if text.endswith(":") and len(text) < 60:
            label = text.rstrip(": ").strip()
        elif label:
            pairs[label] = f"{pairs[label]}, {text}" if label in pairs else text
    return pairs if len(pairs) >= 2 else {}


def _table_records(table: Tag) -> list:
    """Reads a table into a list of {label: value} records. Handles the
    three layouts the legacy site uses: a header row with one or more
    value rows, a two column label/value table, and cells that carry
    their own bold label."""
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if cells:
            rows.append(cells)
    if not rows:
        return []

    # One card per row: a name cell and a details cell of "Label :" pairs.
    card_records = []
    for row in rows:
        if len(row) == 2:
            name = _cell_text(row[0])
            pairs = _cell_pairs(row[1])
            if pairs and name and len(name) < 80:
                card_records.append({"_title": name, **pairs})
    if card_records and len(card_records) >= len(rows) * 0.5:
        return card_records

    # Cells with their own bold labels (a grid of label/value boxes).
    labelled = [_cell_label_value(c) for row in rows for c in row]
    if labelled and sum(1 for x in labelled if x) >= max(2, len(labelled) * 0.6):
        record = {}
        for item in labelled:
            if item:
                record.setdefault(item[0], item[1])
        return [record]

    texts = [[_cell_text(c) for c in row] for row in rows]
    header_is_th = all(c.name == "th" for c in rows[0])
    # Rows narrower than the table (a title cell spanning all columns) are
    # kept as the record title rather than breaking the layout detection.
    width = Counter(len(r) for r in texts).most_common(1)[0][0]
    full = [r for r in texts if len(r) == width]
    titles = [r[0] for r in texts if len(r) < width and r and r[0]]

    def label_like(value: str, limit: int = 60) -> bool:
        return bool(value) and not _PRICE.search(value) and len(value) < limit

    # Two columns, label on the left: one record.
    if width == 2 and len(full) >= 2 and sum(1 for r in full if label_like(r[0])) >= len(full) * 0.7 and not (header_is_th and len(full) > 2 and _PRICE.search(full[1][1]) and _PRICE.search(full[2][1])):
        record = {"_title": titles[0]} if titles else {}
        for r in full:
            if r[0] and r[1]:
                record.setdefault(r[0].rstrip(":"), r[1])
        return [record]

    # Transposed: labels down the first column, one package per column
    # (for example "Package Name | Upahara 499 | Upahara 727").
    if (width >= 3 and len(full) >= 3 and not header_is_th
            and sum(1 for r in full if label_like(r[0])) >= len(full) * 0.8
            and any(_PRICE.search(v) for r in full for v in r[1:])):
        records = []
        for j in range(1, width):
            record = {r[0].rstrip(":"): r[j] for r in full if r[0] and r[j]}
            if titles:
                record["_title"] = titles[0]
            if record:
                records.append(record)
        return records

    # Header row plus value rows.
    if len(full) >= 2 and (header_is_th or not any(_PRICE.search(t) for t in full[0])):
        body = full[1:]
        # A header like "Package Name | Upahara 499 | Upahara 727" over rows
        # whose first cell is a label: one package per column.
        if width >= 3 and label_like(full[0][0]) and all(label_like(r[0], 200) for r in body) and any(_PRICE.search(v) for r in body for v in r[1:]):
            records = []
            for j in range(1, width):
                record = {full[0][0].rstrip(":"): full[0][j]} if full[0][j] else {}
                record.update({r[0].rstrip(":"): r[j] for r in body if r[0] and r[j]})
                if titles:
                    record["_title"] = titles[0]
                if record:
                    records.append(record)
            return records
        # Two columns under an empty header: name on the left, details on
        # the right ("One Shot Ultra 7 Days | Plan prices : Rs.488 ...").
        if width == 2 and not any(full[0]):
            return [{"_title": r[0], "Details": r[1]} for r in body if r[0] and r[1]]
        header = [h or f"Column {i + 1}" for i, h in enumerate(full[0])]
        records = []
        for r in full[1:]:
            record = {h: v for h, v in zip(header, r) if v}
            if record:
                if titles:
                    record["_title"] = titles[0]
                records.append(record)
        if records:
            return records

    # Anything else: each row as a numbered record.
    return [{f"Column {i + 1}": v for i, v in enumerate(r) if v} for r in texts if any(r)]


def _render_record(record: dict) -> str:
    parts = []
    title = record.get("_title")
    if title:
        parts.append(title)
    parts += [f"{k}: {v}" for k, v in record.items() if k != "_title"]
    return "; ".join(parts)


def html_to_blocks(root: Tag) -> list:
    blocks = []
    buffer = []

    def flush():
        text = _clean(" ".join(buffer))
        buffer.clear()
        if text:
            blocks.append(_Block("text", text))

    def walk(node):
        for child in list(node.children):
            if isinstance(child, NavigableString):
                if child.__class__.__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction"):
                    continue
                buffer.append(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in _DROP_TAGS:
                continue
            marker = " ".join(child.get("class") or []) + " " + (child.get("id") or "")
            if marker.strip() and _CHROME.search(marker):
                continue
            if name in _HEADINGS:
                flush()
                text = _clean(child.get_text(" "))
                if text:
                    blocks.append(_Block("heading", text, level=int(name[1])))
            elif name == "table":
                flush()
                records = _table_records(child)
                if records:
                    text = "\n".join(_render_record(r) for r in records)
                    blocks.append(_Block("table", text, records=records))
            elif name == "br":
                flush()
            elif name in _BLOCK_TAGS:
                flush()
                walk(child)
                flush()
            else:
                walk(child)

    walk(root)
    flush()
    return blocks


# Nuxt store items (www.sltmobitel.lk)

_STORE_SKIP_KEYS = {"structuredNavigation", "standaloneNavigation", "footerData", "seoData", "sltSeoData",
                    "common", "form", "map", "notifications", "careers", "careers-by-id", "currentRoute"}
_STORE_TEXT_KEYS = ("title", "name", "heading", "sub_title", "subtitle", "description", "content", "text",
                    "body", "meta_title", "meta_description", "caption", "short_description", "summary")
_STORE_SECTION_URLS = {
    "personal": "https://www.sltmobitel.lk/personal",
    "business": "https://www.sltmobitel.lk/business/business-solutions",
    "roaming": "https://www.sltmobitel.lk/personal/idd-and-roaming",
    "entertainment": "https://www.sltmobitel.lk/entertainment",
    "digital-service": "https://www.sltmobitel.lk/personal/digital-service",
    "news-highlights": "https://www.sltmobitel.lk/personal/news-highlights",
    "microsite": "https://www.sltmobitel.lk/",
    "home": "https://www.sltmobitel.lk/",
    "packages": "https://www.sltmobitel.lk/personal",
}


def _string_of(value) -> str:
    if isinstance(value, str):
        return _clean(BeautifulSoup(value, "html.parser").get_text(" ")) if "<" in value else _clean(value)
    if isinstance(value, list):
        return _clean(" ".join(_string_of(v) for v in value if isinstance(v, (str, dict, list))))
    if isinstance(value, dict) and "text" in value:
        return _string_of(value["text"])
    return ""


def store_items(state: dict) -> list:
    """(store_path, text) pairs worth indexing: a dict's own title-like
    and description-like fields joined, when there is real prose (at
    least one field of 25 characters or more), never SEO keyword lists."""
    items = []

    def walk(node, path):
        if isinstance(node, dict):
            fields = []
            for key in _STORE_TEXT_KEYS:
                if key in node:
                    text = _string_of(node[key])
                    if text and not text.startswith(("http", "/")) and text not in fields:
                        fields.append(text)
            if fields and max(len(f) for f in fields) >= 25:
                items.append((path, ". ".join(fields)))
            for key, value in node.items():
                if key in _STORE_SKIP_KEYS or key in _STORE_TEXT_KEYS or key == "keywords":
                    continue
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for value in node:
                walk(value, path)

    walk((state or {}).get("state") or {}, "")
    return items


# Documents

def _page_title(soup: BeautifulSoup, state: Optional[dict]) -> str:
    seo = ((state or {}).get("state") or {}).get("seoData") or {}
    title = _clean(seo.get("title") or "") if isinstance(seo, dict) else ""
    if not title and soup.title:
        title = _clean(soup.title.get_text())
    title = re.sub(r"\s*\|\s*(SLTMobitel|SLT Mobitel|Mobitel)\s*$", "", title, flags=re.I)
    return title or "SLT-Mobitel"


def _meta_description(soup: BeautifulSoup) -> str:
    tag = soup.find("meta", attrs={"name": "description"})
    return _clean(tag.get("content", "")) if tag else ""


def _content_root(soup: BeautifulSoup) -> Tag:
    for selector in (".body-wrapper", "#main-content", "main", ".region-content", "#__nuxt"):
        found = soup.select_one(selector)
        if found:
            return found
    return soup.body or soup


def _day(iso: str) -> str:
    return (iso or "")[:10]


def _blocks_to_sections(blocks: list, boilerplate: set) -> list:
    sections = [{"heading": "", "level": 0, "lines": [], "tables": []}]
    for block in blocks:
        key = block.text
        if block.kind != "table" and key in boilerplate:
            continue
        text = _redact_emails(_BYLINE.sub("", block.text)).strip()
        if not text or _MARKUP_DEBRIS.search(text):
            continue
        if block.kind == "heading":
            sections.append({"heading": text, "level": block.level, "lines": [], "tables": []})
        else:
            sections[-1]["lines"].append(text)
            if block.kind == "table":
                sections[-1]["tables"].append(block.records)
    return [s for s in sections if s["lines"] or s["tables"]]


def _label_match(label: str, words) -> bool:
    low = label.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)


def _pick(record: dict, labels, accept, used: set) -> Optional[tuple]:
    """The first (label, value) whose label matches the earliest keyword
    in labels and whose value passes accept()."""
    for word in labels:
        for label, value in record.items():
            if label in used or label == "_title":
                continue
            if _label_match(label, (word,)) and accept(label, value):
                return label, value
    return None


def _package_from_record(record: dict, fallback_name: str, source_url: str, retrieved_at: str) -> Optional[dict]:
    used = set()

    def not_price_label(label):
        return not _label_match(label, _NOT_PRICE_LABELS)

    price = _pick(record, _PRICE_LABELS,
                  lambda l, v: not_price_label(l) and bool(_PRICE.search(v) or re.match(r"^\s*[0-9][0-9,.]*", v)),
                  used)
    if price is None:
        for label, value in record.items():
            if label != "_title" and not_price_label(label) and _PRICE.search(value):
                price = (label, value)
                break
    if price is None:
        return None
    used.add(price[0])

    name = _pick(record, _NAME_LABELS, lambda l, v: not _PRICE.search(v) and 2 < len(v) < 80, used)
    if name:
        used.add(name[0])
    validity = _pick(record, _VALIDITY_LABELS, lambda l, v: True, used)
    if validity is None:
        for label, value in record.items():
            if label not in used and label != "_title" and _VALIDITY.search(value) and not _DATA.search(value):
                validity = (label, value)
                break
    if validity:
        used.add(validity[0])
    data = _pick(record, _DATA_LABELS, lambda l, v: bool(_DATA.search(v)) or "unlimited" in v.lower(), used)
    if data is None:
        for label, value in record.items():
            if label not in used and label != "_title" and _DATA.search(value):
                data = (label, value)
                break
    if data:
        used.add(data[0])

    price_value_match = _PRICE.search(price[1]) or re.match(r"^\s*([0-9][0-9,]*(?:\.[0-9]+)?)", price[1])
    price_value = float(price_value_match.group(1).replace(",", "")) if price_value_match else None
    package_name = name[1] if name else record.get("_title")
    if not package_name:
        package_name = f"{fallback_name} (Rs. {price_value_match.group(1)})" if price_value_match else fallback_name
    return {
        "name": package_name,
        "price": price[1],
        "price_lkr": price_value,
        "data": data[1] if data else None,
        "validity": validity[1] if validity else None,
        "conditions": {k: v for k, v in record.items() if k not in used and k != "_title"},
        "section": fallback_name,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "_named": bool(name) and _label_match(name[0], _STRONG_NAME_LABELS),
        # "field" when a name like cell (Marketing Name, Package Name) gave
        # the name, "heading" when it was inferred from the nearest heading
        # (best effort on pages with many tabbed packages).
        "name_from": "field" if name else ("table" if record.get("_title") else "heading"),
    }


_GENERIC_HEADING = re.compile(
    r"^(\d+\s*[.)]|.*\?$|(other |general )?(conditions|terms( and|\s*&)? conditions|package details|"
    r"details|faqs?|notes?|how to .*|trc related details|plans? (&|and) rates)$)", re.I)


def _meaningful(heading: str) -> bool:
    return bool(heading) and not _GENERIC_HEADING.match(heading.strip())


def detect_packages(sections: list, page_title: str, source_url: str, retrieved_at: str) -> list:
    """Packages from the page's tables. A record only counts as a package
    when it has a price and at least one of a data allowance, a validity
    or an explicit name field; that drops fee schedules (short code
    charges, deposits, mCash fees) that also contain rupee amounts."""
    packages = []
    # Group sections under their nearest meaningful h2/h3 (the package or
    # topic), so detail tables under "1. Package Details" (h4) or a
    # numbered FAQ heading are attributed to the package above them.
    groups = []
    current = page_title
    for section in sections:
        heading = section["heading"]
        starts_group = not groups or (section["level"] and section["level"] <= 3 and _meaningful(heading))
        if starts_group:
            current = heading if _meaningful(heading) else current
            groups.append({"name": current, "tables": list(section["tables"])})
        else:
            groups[-1]["tables"].extend(section["tables"])
    for group in groups:
        merged = {}
        for records in group["tables"]:
            # Several rows, or a single card that names itself: each record
            # is its own package. Untitled single records (a "Package
            # Details" box plus a TRC table) are merged under the heading.
            if len(records) >= 2 or (records and records[0].get("_title")):
                for record in records:
                    pkg = _package_from_record(record, group["name"], source_url, retrieved_at)
                    if pkg:
                        packages.append(pkg)
            elif records:
                for key, value in records[0].items():
                    merged.setdefault(key, value)
        if merged:
            pkg = _package_from_record(merged, group["name"], source_url, retrieved_at)
            if pkg:
                packages.append(pkg)
    unique = {}
    for pkg in packages:
        if not (pkg["data"] or pkg["validity"] or pkg.pop("_named", False)):
            continue
        pkg.pop("_named", None)
        pkg["page_title"] = page_title
        unique.setdefault((pkg["name"].lower(), pkg["price"], pkg["data"]), pkg)
    return list(unique.values())


def detect_faqs(sections: list, page_title: str, source_url: str, retrieved_at: str) -> list:
    """Question/answer pairs: a heading or a short line ending in "?"
    followed by answer lines. The enclosing package or topic heading is
    kept as context, because many legacy FAQs say "this package"."""
    faqs = []
    context = page_title
    for section in sections:
        heading = section["heading"]
        if heading and not heading.rstrip().endswith("?") and section["level"] <= 3:
            context = heading
        candidates = []
        if heading.rstrip().endswith("?"):
            candidates.append((heading, section["lines"]))
        else:
            lines = section["lines"]
            i = 0
            while i < len(lines):
                line = lines[i]
                if line.endswith("?") and len(line) < 200:
                    answer = []
                    j = i + 1
                    while j < len(lines) and not (lines[j].endswith("?") and len(lines[j]) < 200):
                        answer.append(lines[j])
                        j += 1
                    candidates.append((line, answer))
                    i = j
                    continue
                # "1.What is X? Answer text" written in one paragraph.
                inline = re.match(r"^(\d+\s*[.)]\s*[^?]{8,160}\?)\s+(.{20,})$", line)
                if inline:
                    candidates.append((inline.group(1), [inline.group(2)]))
                i += 1
        for question, answer_lines in candidates:
            question = _QUESTION_START.sub("", question).strip()
            answer = _clean(" ".join(answer_lines))
            if len(question) < 8 or len(answer) < 15:
                continue
            faqs.append({"question": question, "answer": answer[:1500], "context": context,
                         "source_url": source_url, "retrieved_at": retrieved_at})
    return faqs


def _doc_id(source_url: str, suffix: str = "") -> str:
    return hashlib.sha1((source_url + "#" + suffix).encode("utf-8")).hexdigest()[:16]


def build_docs(include_legacy: bool = True) -> tuple:
    started = time.perf_counter()
    entries = cached_pages(include_legacy)
    parsed = []
    block_counts_by_host = {}
    pages_by_host = Counter()
    for entry in entries:
        html = read_cached(entry)
        soup = BeautifulSoup(html, "html.parser")
        state = extract_nuxt_state(html)
        title = _page_title(soup, state)
        description = _meta_description(soup)
        byline = _BYLINE.search(soup.get_text(" "))
        root = _content_root(soup)
        blocks = html_to_blocks(root)
        host = entry.get("host") or entry["url"].split("/")[2]
        pages_by_host[host] += 1
        counts = block_counts_by_host.setdefault(host, Counter())
        counts.update({b.text for b in blocks if b.kind != "table"})
        parsed.append((entry, title, description, blocks, state, byline.group(1) if byline else None))

    boilerplate_by_host = {}
    for host, counts in block_counts_by_host.items():
        threshold = max(3, int(pages_by_host[host] * 0.2))
        boilerplate_by_host[host] = {text for text, n in counts.items() if n >= threshold}

    docs = []
    faq_candidates = []
    seen_store_text = set()
    store_groups = {}
    for entry, title, description, blocks, state, page_date in parsed:
        url = entry["url"]
        host = entry.get("host") or url.split("/")[2]
        retrieved_at = entry.get("checked_at") or entry.get("fetched_at")
        sections = _blocks_to_sections(blocks, boilerplate_by_host.get(host, set()))
        # Drupal generates the legacy site's meta description from the body
        # (a flattened copy of the first table), so it is never used there;
        # elsewhere only when it adds text the body does not already have.
        body_words = re.sub(r"[^a-z0-9]+", "", " ".join(
            f"{s['heading']} " + " ".join(s["lines"]) for s in sections).lower())
        description_key = re.sub(r"[^a-z0-9]+", "", description.lower())[:60]
        if (description and description != title and len(description) > 30 and host != "www.mobitel.lk"
                and description_key not in body_words):
            sections.insert(0, {"heading": "", "level": 0, "lines": [description], "tables": []})
        packages = detect_packages(sections, title, url, retrieved_at)
        faqs = detect_faqs(sections, title, url, retrieved_at)
        faq_candidates.extend(faqs)
        text_len = sum(len(line) for s in sections for line in s["lines"])
        if text_len >= 40:
            docs.append({
                "doc_id": _doc_id(url),
                "kind": "page",
                "source_url": url,
                "host": host,
                "title": title,
                "lang": detect_language(" ".join(line for s in sections for line in s["lines"])[:4000]),
                "retrieved_at": retrieved_at,
                "page_date": page_date,
                "sections": [{"heading": s["heading"], "lines": s["lines"]} for s in sections],
                "packages": packages,
                "faqs": faqs,
            })

        # Nuxt store: mostly the same on every page, so each text is kept
        # once, attributed to the page it describes (home tiles to the page
        # they were on, other store areas to their section's landing page).
        for path, text in store_items(state or {}):
            text = _redact_emails(text)
            if text in seen_store_text or len(text) < 30:
                continue
            seen_store_text.add(text)
            top = path.split(".", 1)[0]
            source = url if top == "sections" else _STORE_SECTION_URLS.get(top, "https://www.sltmobitel.lk/")
            lang = "si" if ".si." in f".{path}." else "ta" if ".ta." in f".{path}." else detect_language(text)
            group_key = (source, ".".join(path.split(".")[:2]), lang)
            group = store_groups.setdefault(group_key, {"retrieved_at": retrieved_at, "lines": []})
            group["lines"].append(text)

    for (source, path, lang), group in store_groups.items():
        area = path.split(".")[-1]
        area_title = re.sub(r"(?<!^)(?=[A-Z])", " ", area).replace("-", " ").strip().capitalize()
        docs.append({
            "doc_id": _doc_id(source, path + lang),
            "kind": "site_store",
            "source_url": source,
            "host": "www.sltmobitel.lk",
            "title": f"SLT-Mobitel website: {area_title}",
            "lang": lang,
            "retrieved_at": group["retrieved_at"],
            "page_date": None,
            "sections": [{"heading": "", "lines": group["lines"]}],
            "packages": [],
            "faqs": [],
        })

    for source in load_public_sources():
        docs.append({
            "doc_id": _doc_id(source["source_url"], source["title"]),
            "kind": "public",
            "source_url": source["source_url"],
            "host": source["source_url"].split("/")[2],
            "title": source["title"],
            "lang": detect_language(source["text"]),
            "retrieved_at": source["retrieved_at"],
            "page_date": None,
            "sections": [{"heading": "", "lines": [source["text"]]}],
            "packages": [],
            "faqs": [],
        })

    elapsed = time.perf_counter() - started
    print(f"Normalize: {len(entries)} cached pages -> {len(docs)} documents, "
          f"{sum(len(d['packages']) for d in docs)} packages, {len(faq_candidates)} FAQ candidates")
    print(f"Normalize latency: {elapsed:.1f}s")
    return docs, faq_candidates


def load_public_sources() -> list:
    if not PUBLIC_SOURCES_PATH.exists():
        return []
    with PUBLIC_SOURCES_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_faq() -> list:
    if not FAQ_PATH.exists():
        return []
    with FAQ_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(write_faq: bool = False, include_legacy: bool = True) -> list:
    docs, faq_candidates = build_docs(include_legacy)
    _write_jsonl(DOCS_PATH, docs)
    # Deduplicated candidates for hand curation into faq.jsonl.
    unique = {}
    for faq in faq_candidates:
        unique.setdefault((faq["question"].lower(), faq["answer"][:80].lower()), faq)
    _write_jsonl(FAQ_CANDIDATES_PATH, list(unique.values()))
    print(f"Wrote {DOCS_PATH} and {len(unique)} FAQ candidates to {FAQ_CANDIDATES_PATH}")
    if write_faq:
        rows = [{"question": f["question"], "answer": f["answer"], "context": f["context"],
                 "source_url": f["source_url"], "retrieved_at": _day(f["retrieved_at"])}
                for f in unique.values()]
        _write_jsonl(FAQ_PATH, rows)
        print(f"Overwrote {FAQ_PATH} with {len(rows)} uncurated candidates; curate it before committing.")
    return docs


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Normalize the cached pages into docs.jsonl.")
    parser.add_argument("--include-legacy", action=argparse.BooleanOptionalAction, default=True,
                        help="also use the second official host www.mobitel.lk (default on; --no-include-legacy keeps to www.sltmobitel.lk only)")
    parser.add_argument("--write-faq", action="store_true",
                        help="overwrite the committed faq.jsonl with the raw candidates (then curate by hand)")
    args = parser.parse_args(argv)
    run(write_faq=args.write_faq, include_legacy=args.include_legacy)


if __name__ == "__main__":
    main()
