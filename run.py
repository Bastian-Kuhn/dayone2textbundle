#!/usr/bin/env python3
"""
DayOne → TextBundle / Markdown Exporter

Modes
─────
Default (one TextBundle per entry):
  output/<Journal>/<YYYY>/<YYYY-MM-DD HHmm Title>.textbundle

--markdown (one .md per entry):
  output/<Journal>/<template>/<DD HHmm Title>.md

--merge-day (one file per calendar day):
  output/<Journal>/<parent-template>/<DD>.md  (or .textbundle)

--merge-month (one file per calendar month):
  output/<Journal>/<YYYY>/<MM>.md  (or .textbundle)

--merge-journals:
  All journals land in the same folder (no <Journal> subfolder).

--path-template YYYY/MM/DD:
  Obsidian Daily Note-style path template.  Default: YYYY/MM
"""

import argparse
import sqlite3
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── Configuration ──────────────────────────────────────────────────────────────

DAYONE_DOCS   = Path.home() / "Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/Documents"
DAYONE_DB     = DAYONE_DOCS / "DayOne.sqlite"
DAYONE_PHOTOS = DAYONE_DOCS / "DayOnePhotos"
DAYONE_VIDEOS = DAYONE_DOCS / "DayOneVideos"
DAYONE_AUDIOS = DAYONE_DOCS / "DayOneAudios"
DAYONE_PDFS   = DAYONE_DOCS / "DayOnePDFAttachments"

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Keys whose list values are written as YAML inline arrays (e.g. location: [lat, lng])
_INLINE_ARRAY_KEYS = {'location'}

# ── Helpers ────────────────────────────────────────────────────────────────────

def apple_ts_to_datetime(ts) -> datetime:
    if ts is None:
        return datetime.now().astimezone()
    try:
        return (APPLE_EPOCH + timedelta(seconds=float(ts))).astimezone()
    except (ValueError, TypeError):
        return datetime.now().astimezone()


def safe_filename(name: str, max_len: int = 60) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name[:max_len] if name else "Untitled"


def derive_title(text: str, date: datetime) -> str:
    if not text or not text.strip():
        return date.strftime('%H-%M')
    first_line = text.strip().split('\n')[0].strip()
    first_line = re.sub(r'^#+\s*', '', first_line)
    first_line = re.sub(r'\*+', '', first_line)
    first_line = re.sub(r'!\[\]\(.*?\)', '', first_line)
    first_line = first_line.strip()
    if len(first_line) > 3:
        return safe_filename(first_line, 60)
    return date.strftime('%H-%M')


def extract_heading(text: str) -> str | None:
    """Returns the text of the first markdown heading, or None if there is none."""
    for line in text.splitlines():
        m = re.match(r'^#{1,6}\s+(.+)', line.strip())
        if m:
            heading = m.group(1).strip()
            heading = re.sub(r'\*+', '', heading)       # strip bold/italic markers
            heading = re.sub(r'`', '', heading)          # strip inline code markers
            heading = heading.strip()
            return heading if heading else None
    return None


def extract_summary(text: str, max_len: int = 160) -> str | None:
    """Returns a clean plain-text summary of up to max_len characters."""
    clean = text
    clean = re.sub(r'^#{1,6}\s+.*$', '', clean, flags=re.MULTILINE)  # remove headings
    clean = re.sub(r'!\[.*?\]\(.*?\)', '', clean)                      # remove images
    clean = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', clean)           # links → text
    clean = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', clean)           # bold/italic
    clean = re.sub(r'`[^`]+`', '', clean)                             # inline code
    clean = re.sub(r'<!--.*?-->', '', clean, flags=re.DOTALL)         # comments
    clean = re.sub(r'\n+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    if not clean:
        return None
    if len(clean) > max_len:
        cut = clean[:max_len]
        # avoid cutting mid-word
        space = cut.rfind(' ')
        clean = cut[:space] if space > max_len // 2 else cut
    return clean


def extract_first_image(body: str) -> str | None:
    """Returns the src of the first markdown image in the (processed) body."""
    m = re.search(r'!\[.*?\]\((.+?)\)', body)
    return m.group(1) if m else None


def apply_path_template(template: str, date: datetime) -> Path:
    """Replaces Obsidian-style date tokens in a path template string."""
    r = template
    r = r.replace('YYYY', date.strftime('%Y'))
    r = r.replace('MM',   date.strftime('%m'))
    r = r.replace('DD',   date.strftime('%d'))
    r = r.replace('HH',   date.strftime('%H'))
    r = r.replace('mm',   date.strftime('%M'))
    return Path(r)


def template_folder_for_day(template: str, date: datetime) -> Path:
    """
    For --merge-day: returns the folder that contains the day file.
    If the template's last segment is 'DD', strips it (day becomes the file name).
    YYYY/MM/DD → folder YYYY/MM,  file DD.md
    YYYY/MM    → folder YYYY/MM,  file DD.md  (same result)
    """
    parts = template.rstrip('/').split('/')
    if parts and parts[-1] == 'DD':
        parent_tmpl = '/'.join(parts[:-1])
        return apply_path_template(parent_tmpl, date) if parent_tmpl else Path('.')
    return apply_path_template(template, date)


def template_has_day(template: str) -> bool:
    return 'DD' in template.split('/')[-1] if template else False


def find_attachment(identifier: str, md5=None, file_type=None) -> Path | None:
    ftype_lower = (file_type or '').lower()
    if ftype_lower in ('mp4', 'mov', 'm4v'):
        search_dirs = [DAYONE_VIDEOS, DAYONE_PHOTOS, DAYONE_AUDIOS, DAYONE_PDFS]
    elif ftype_lower in ('m4a', 'aac', 'mp3'):
        search_dirs = [DAYONE_AUDIOS, DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_PDFS]
    elif ftype_lower == 'pdf':
        search_dirs = [DAYONE_PDFS, DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_AUDIOS]
    else:
        search_dirs = [DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_AUDIOS, DAYONE_PDFS]

    extensions = ('', '.jpeg', '.jpg', '.png', '.gif', '.heic',
                  '.mp4', '.mov', '.m4v', '.pdf', '.m4a')

    for base in search_dirs:
        if not base.exists():
            continue
        if md5:
            md5_lower = md5.lower()
            if file_type:
                c = base / f"{md5_lower}.{ftype_lower}"
                if c.exists():
                    return c
            for ext in extensions:
                c = base / f"{md5_lower}{ext}"
                if c.exists():
                    return c
        for name in (identifier.lower(), identifier.upper()):
            if file_type:
                c = base / f"{name}.{ftype_lower}"
                if c.exists():
                    return c
            for ext in extensions:
                c = base / f"{name}{ext}"
                if c.exists():
                    return c

    for base in search_dirs:
        if not base.exists():
            continue
        if md5:
            for f in base.rglob(f"{md5.lower()}*"):
                if f.is_file():
                    return f
        for f in base.rglob(f"{identifier}*"):
            if f.is_file():
                return f
    return None


def resolve_attachments(text: str, att_list: list, assets_dir: Path) -> str:
    """Copies attachments to assets_dir and rewrites dayone-moment:// URLs."""
    if not att_list:
        return text
    assets_dir.mkdir(parents=True, exist_ok=True)
    for ident, md5, ftype in att_list:
        src = find_attachment(ident, md5, ftype)
        if src:
            dest_name = src.name if src.suffix else f"{ident}.jpeg"
            shutil.copy2(src, assets_dir / dest_name)
            new_ref = f"assets/{dest_name}"
        else:
            new_ref = f"<!-- attachment not found: {ident} -->"
        text = re.sub(
            rf'dayone-moment:/+(?:\w+/)?{re.escape(ident)}',
            new_ref, text, flags=re.IGNORECASE,
        )
    return text


# ── Database loading ───────────────────────────────────────────────────────────

def load_journals_and_attachments(conn) -> tuple[dict, dict]:
    journals: dict[int, str] = {}
    for row in conn.execute("SELECT Z_PK, ZNAME FROM ZJOURNAL"):
        journals[row['Z_PK']] = row['ZNAME'] or 'Journal'

    attachments: dict[int, list] = {}
    for row in conn.execute(
        "SELECT ZENTRY, ZIDENTIFIER, ZMD5, ZTYPE "
        "FROM ZATTACHMENT WHERE ZENTRY IS NOT NULL AND ZIDENTIFIER IS NOT NULL"
    ):
        ident = row['ZIDENTIFIER'].upper()
        attachments.setdefault(row['ZENTRY'], []).append((ident, row['ZMD5'], row['ZTYPE']))

    return journals, attachments


def load_tags(conn) -> dict[int, list[str]]:
    """
    Returns {entry_pk: [tag_name, ...]} by locating the Core Data junction table
    that links ZENTRY to ZTAG.

    Strategy:
    1. Try every table whose name starts with 'Z_' (Core Data junction tables).
    2. Inspect column names for one referencing entries and one referencing tags.
    3. Use the ORIGINAL (non-uppercased) column names in the SQL query so SQLite
       can resolve them regardless of case sensitivity.
    4. Collect results from ALL matching tables (not just the first) to handle
       databases where tags are spread across multiple junction tables.
    """
    result: dict[int, list[str]] = defaultdict(list)

    all_tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )]
    if 'ZTAG' not in all_tables:
        return dict(result)

    for table in all_tables:
        if not table.upper().startswith('Z_'):
            continue
        try:
            # Keep ORIGINAL column names for the SQL query (SQLite is case-insensitive
            # for identifiers, but using the exact name avoids any edge cases).
            col_rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            orig_names = [c[1] for c in col_rows]           # original case
            upper_names = [n.upper() for n in orig_names]   # for pattern matching

            entry_idx = next((i for i, n in enumerate(upper_names) if 'ENTR' in n), None)
            tag_idx   = next((i for i, n in enumerate(upper_names) if 'TAG'  in n), None)
            if entry_idx is None or tag_idx is None:
                continue

            entry_col = orig_names[entry_idx]
            tag_col   = orig_names[tag_idx]

            rows = conn.execute(
                f'SELECT j."{entry_col}", t.ZNAME '
                f'FROM "{table}" j JOIN ZTAG t ON t.Z_PK = j."{tag_col}" '
                f'WHERE j."{entry_col}" IS NOT NULL AND t.ZNAME IS NOT NULL'
            ).fetchall()
            for entry_pk, tag_name in rows:
                if tag_name not in result[entry_pk]:
                    result[entry_pk].append(tag_name)
        except Exception:
            continue

    return dict(result)


def load_locations(conn) -> dict[int, dict]:
    """Returns {entry_pk: {latitude, longitude, place_name?, country?}}."""
    result: dict[int, dict] = {}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if 'ZLOCATION' not in tables:
        return result
    try:
        loc_cols = {c[1].upper(): c[1] for c in conn.execute("PRAGMA table_info(ZLOCATION)").fetchall()}
        ent_cols = {c[1].upper(): c[1] for c in conn.execute("PRAGMA table_info(ZENTRY)").fetchall()}

        lat_col     = next((v for k, v in loc_cols.items() if 'LATIT'   in k), None)
        lng_col     = next((v for k, v in loc_cols.items() if 'LONGI'   in k), None)
        place_col   = next((v for k, v in loc_cols.items() if k.startswith('ZPLACE')), None)
        country_col = next((v for k, v in loc_cols.items() if 'COUNTRY' in k), None)
        loc_fk      = ent_cols.get('ZLOCATION')

        if not (lat_col and lng_col and loc_fk):
            return result

        extras: list[tuple[str, str]] = []
        if place_col:   extras.append(('place_name', place_col))
        if country_col: extras.append(('country',    country_col))

        selects = ['e.Z_PK', f'l."{lat_col}"', f'l."{lng_col}"'] + [f'l."{c}"' for _, c in extras]
        for row in conn.execute(
            f'SELECT {", ".join(selects)} '
            f'FROM ZENTRY e JOIN ZLOCATION l ON e."{loc_fk}" = l.Z_PK '
            f'WHERE e."{loc_fk}" IS NOT NULL AND l."{lat_col}" IS NOT NULL'
        ):
            loc: dict = {'latitude': row[1], 'longitude': row[2]}
            for i, (key, _) in enumerate(extras, 3):
                if row[i] is not None:
                    loc[key] = row[i]
            result[row[0]] = loc
    except Exception as e:
        print(f"  ⚠️  Could not load location data: {e}", file=sys.stderr)

    return result


# ── Frontmatter ────────────────────────────────────────────────────────────────

def build_entry_frontmatter(
    date: datetime,
    title: str,
    starred: bool,
    uuid: str,
    tags: list[str],
    location: dict | None,
    heading: str | None = None,
    summary: str | None = None,
    first_image: str | None = None,
) -> dict:
    """Builds the frontmatter property dict for a single entry."""
    props: dict = {
        'date':        date.strftime('%Y-%m-%d'),
        'time':        date.strftime('%H:%M'),
        'created':     date.isoformat(),
        # title: prefer the actual heading; fall back to the filename-derived title
        'title':       heading if heading else title,
        'starred':     starred,
        'uuid':        uuid,
        # "On This Day" helpers (usable with Obsidian Bases / Cards view)
        'year':        date.year,
        'month':       date.month,
        'day':         date.day,
        'day_of_year': int(date.strftime('%j')),
        'month_day':   date.strftime('%m-%d'),   # e.g. "03-15" for filtering across years
        'weekday':     date.strftime('%A'),
    }
    if tags:
        props['tags'] = sorted(set(tags))
    if location:
        lat = location.get('latitude')
        lng = location.get('longitude')
        if lat is not None and lng is not None:
            # Obsidian map view format: location: [lat, lng]
            props['location'] = [round(float(lat), 6), round(float(lng), 6)]
        parts = [location.get('place_name'), location.get('country')]
        place = ', '.join(p for p in parts if p)
        if place:
            props['place'] = place
    if summary:
        props['summary'] = summary
    if first_image:
        props['first_image'] = first_image
    return props


def merge_entry_frontmatters(fms: list[dict], level: str) -> dict:
    """
    Merges multiple single-entry frontmatter dicts into one for a merged file.
    level: 'day' or 'month'
    """
    if not fms:
        return {}
    first = fms[0]
    merged: dict = {
        'year':        first.get('year'),
        'month':       first.get('month'),
        'entry_count': len(fms),
    }
    if level == 'day':
        merged['date']        = first.get('date', '')
        merged['day']         = first.get('day')
        merged['day_of_year'] = first.get('day_of_year')
        merged['month_day']   = first.get('month_day')
        merged['weekday']     = first.get('weekday')

    # Union of all tags
    all_tags: list[str] = []
    for fm in fms:
        all_tags.extend(fm.get('tags', []))
    if all_tags:
        merged['tags'] = sorted(set(all_tags))

    # Starred if any entry is starred
    merged['starred'] = any(fm.get('starred', False) for fm in fms)

    # First available location
    for fm in fms:
        if 'location' in fm:
            merged['location'] = fm['location']
            if 'place' in fm:
                merged['place'] = fm['place']
            break

    # Single-entry fields: carry through when there is exactly one entry,
    # or pick the first available value for multi-entry merged files.
    for key in ('title', 'summary', 'first_image'):
        for fm in fms:
            if fm.get(key):
                merged[key] = fm[key]
                break

    # uuid / time / created: only meaningful for single-entry files
    if len(fms) == 1:
        for key in ('uuid', 'time', 'created'):
            if fms[0].get(key):
                merged[key] = fms[0][key]

    return merged


def _yaml_str(value: str) -> str:
    """
    Serialises a string as a safe YAML scalar.
    Uses single-quoted style: the only escape needed is ' → ''.
    Plain (unquoted) style is used only for simple alphanumeric-ish values.
    """
    _PLAIN_RE = re.compile(r'^[A-Za-z0-9_\-./]+$')
    if _PLAIN_RE.match(value):
        return value                          # no quoting needed
    return "'" + value.replace("'", "''") + "'"


def frontmatter_to_yaml(props: dict) -> str:
    """Serialises a property dict to a YAML frontmatter block (--- … ---)."""
    lines = ['---']
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, bool):
            lines.append(f'{k}: {"true" if v else "false"}')
        elif isinstance(v, list):
            if not v:
                lines.append(f'{k}: []')
            elif k in _INLINE_ARRAY_KEYS:
                # Inline array:  location: [48.12, 11.56]
                lines.append(f'{k}: [{", ".join(str(x) for x in v)}]')
            else:
                # Block array
                lines.append(f'{k}:')
                for item in v:
                    scalar = _yaml_str(item) if isinstance(item, str) else str(item)
                    lines.append(f'  - {scalar}')
        elif isinstance(v, str):
            lines.append(f'{k}: {_yaml_str(v)}')
        else:
            lines.append(f'{k}: {v}')
    lines.append('---')
    return '\n'.join(lines)


# ── Core markdown builder (shared by both output formats) ──────────────────────

@dataclass
class EntryData:
    date:  datetime
    title: str
    body:  str       # processed text (attachment URLs already rewritten)
    fm:    dict      # frontmatter properties dict


def process_entry(
    entry_row,
    tags: list[str],
    location: dict | None,
    att_list: list,
    assets_dir: Path,
) -> EntryData:
    """
    Resolves attachments, derives metadata, and builds the EntryData for one row.
    Copies attachment files to assets_dir as a side-effect.
    """
    date    = apple_ts_to_datetime(entry_row['ZCREATIONDATE'])
    text    = (entry_row['ZMARKDOWNTEXT'] or '').replace('\\.', '.')
    uuid    = entry_row['ZUUID'] or ''
    starred = bool(entry_row['ZSTARRED'])

    title       = derive_title(text, date)
    heading     = extract_heading(text)
    summary     = extract_summary(text)
    body        = resolve_attachments(text, att_list, assets_dir)
    first_image = extract_first_image(body)
    fm          = build_entry_frontmatter(
        date, title, starred, uuid, tags, location,
        heading=heading, summary=summary, first_image=first_image,
    )

    return EntryData(date=date, title=title, body=body, fm=fm)


def entry_to_markdown(ed: EntryData) -> str:
    """Renders a single EntryData to a complete markdown string (frontmatter + body)."""
    return frontmatter_to_yaml(ed.fm) + '\n\n' + ed.body


def merged_to_markdown(eds: list[EntryData], level: str) -> str:
    """Renders multiple EntryData objects into a single merged markdown string."""
    merged_fm   = merge_entry_frontmatters([e.fm for e in eds], level)
    merged_yaml = frontmatter_to_yaml(merged_fm)

    sections = []
    for ed in eds:
        heading = f"## {ed.date.strftime('%H:%M')} – {safe_filename(ed.title, 80)}"
        sections.append(f"{heading}\n\n{ed.body}")

    return merged_yaml + '\n\n' + '\n\n---\n\n'.join(sections)


# ── Writers ────────────────────────────────────────────────────────────────────

def write_textbundle(bundle_path: Path, md_content: str) -> None:
    bundle_path.mkdir(parents=True, exist_ok=True)
    (bundle_path / 'text.md').write_text(md_content, encoding='utf-8')
    info = {
        "version": 2,
        "type": "net.daringfireball.markdown",
        "transient": False,
        "creatorIdentifier": "com.dayone.export",
    }
    (bundle_path / 'info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')


# ── Export orchestration ───────────────────────────────────────────────────────

def export_all(
    entries:       list,
    journals:      dict,
    attachments:   dict,
    tags_map:      dict,
    locations_map: dict,
    output_dir:    Path,
    as_markdown:   bool,
    merge_day:     bool,
    merge_month:   bool,
    merge_journals: bool,
    obsidian:      bool,
    path_template: str,
) -> tuple[int, int]:

    errors  = 0
    written = 0

    # ── Group entries ──────────────────────────────────────────────────────────
    # groups[journal_key][group_key] = [entry_row, …]
    groups: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for entry_row in entries:
        journal_pk   = entry_row['ZJOURNAL']
        journal_name = safe_filename(journals.get(journal_pk) or 'Journal')
        jkey = '' if merge_journals else journal_name

        date = apple_ts_to_datetime(entry_row['ZCREATIONDATE'])

        if merge_month:
            gkey = date.strftime('%Y-%m')
        elif merge_day:
            gkey = date.strftime('%Y-%m-%d')
        else:
            gkey = str(entry_row['Z_PK'])   # unique per entry

        groups[jkey][gkey].append(entry_row)

    # ── Write groups ───────────────────────────────────────────────────────────
    for jkey, date_groups in groups.items():
        journal_dir = output_dir / jkey if jkey else output_dir

        for gkey, group_entries in sorted(date_groups.items()):
            group_entries = sorted(group_entries, key=lambda r: r['ZCREATIONDATE'] or 0)
            first_date    = apple_ts_to_datetime(group_entries[0]['ZCREATIONDATE'])

            try:
                if merge_month:
                    # ── One file per month ─────────────────────────────────────
                    folder_path = journal_dir / first_date.strftime('%Y')
                    file_stem   = (first_date.strftime('%Y-%m') if obsidian
                                   else first_date.strftime('%m'))
                    folder_path.mkdir(parents=True, exist_ok=True)

                    if as_markdown:
                        assets_dir  = folder_path / 'assets'
                        out_path    = folder_path / f"{file_stem}.md"
                    else:
                        bundle_path = folder_path / f"{file_stem}.textbundle"
                        assets_dir  = bundle_path / 'assets'

                    eds = [
                        process_entry(e, tags_map.get(e['Z_PK'], []),
                                      locations_map.get(e['Z_PK']),
                                      attachments.get(e['Z_PK'], []), assets_dir)
                        for e in group_entries
                    ]
                    content = merged_to_markdown(eds, 'month')

                    if as_markdown:
                        out_path.write_text(content, encoding='utf-8')
                    else:
                        write_textbundle(bundle_path, content)
                    written += len(group_entries)

                elif merge_day:
                    # ── One file per day ───────────────────────────────────────
                    folder_path = journal_dir / template_folder_for_day(path_template, first_date)
                    file_stem   = (first_date.strftime('%Y-%m-%d') if obsidian
                                   else first_date.strftime('%d'))
                    folder_path.mkdir(parents=True, exist_ok=True)

                    if as_markdown:
                        assets_dir  = folder_path / 'assets'
                        out_path    = folder_path / f"{file_stem}.md"
                    else:
                        bundle_path = folder_path / f"{file_stem}.textbundle"
                        assets_dir  = bundle_path / 'assets'

                    eds = [
                        process_entry(e, tags_map.get(e['Z_PK'], []),
                                      locations_map.get(e['Z_PK']),
                                      attachments.get(e['Z_PK'], []), assets_dir)
                        for e in group_entries
                    ]
                    content = merged_to_markdown(eds, 'day')

                    if as_markdown:
                        out_path.write_text(content, encoding='utf-8')
                    else:
                        write_textbundle(bundle_path, content)
                    written += len(group_entries)

                else:
                    # ── One file per entry ─────────────────────────────────────
                    entry_row = group_entries[0]
                    entry_pk  = entry_row['Z_PK']
                    date      = apple_ts_to_datetime(entry_row['ZCREATIONDATE'])

                    if as_markdown:
                        folder_path = journal_dir / apply_path_template(path_template, date)
                        folder_path.mkdir(parents=True, exist_ok=True)
                        assets_dir  = folder_path / 'assets'
                        ed          = process_entry(
                            entry_row, tags_map.get(entry_pk, []),
                            locations_map.get(entry_pk),
                            attachments.get(entry_pk, []), assets_dir,
                        )
                        # If the template's last segment is the day, omit DD from filename
                        if template_has_day(path_template):
                            filename = f"{date.strftime('%H%M')} {safe_filename(ed.title, 60)}.md"
                        else:
                            filename = f"{date.strftime('%d %H%M')} {safe_filename(ed.title, 60)}.md"
                        (folder_path / filename).write_text(entry_to_markdown(ed), encoding='utf-8')
                    else:
                        # TextBundle: derive title first (from raw text, before attachment rewrite)
                        raw_text   = (entry_row['ZMARKDOWNTEXT'] or '').replace('\\.', '.')
                        raw_title  = derive_title(raw_text, date)
                        folder_dir = journal_dir / apply_path_template(path_template, date)
                        folder_dir.mkdir(parents=True, exist_ok=True)
                        # Use the same naming scheme as Markdown: DD HHmm Title
                        # (year+month are already captured by the folder structure)
                        if template_has_day(path_template):
                            bundle_stem = f"{date.strftime('%H%M')} {safe_filename(raw_title, 60)}"
                        else:
                            bundle_stem = f"{date.strftime('%d %H%M')} {safe_filename(raw_title, 60)}"
                        bundle_name = f"{bundle_stem}.textbundle"
                        bundle_path = folder_dir / bundle_name
                        assets_dir = bundle_path / 'assets'
                        ed = process_entry(
                            entry_row, tags_map.get(entry_pk, []),
                            locations_map.get(entry_pk),
                            attachments.get(entry_pk, []), assets_dir,
                        )
                        write_textbundle(bundle_path, entry_to_markdown(ed))
                    written += 1

            except Exception as e:
                import traceback
                print(f"  ⚠️  Error processing group {gkey}: {e}")
                traceback.print_exc()
                errors += len(group_entries)

    return written, errors


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export DayOne entries to TextBundle or Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One TextBundle per entry (default)
  python run.py

  # Plain Markdown, one file per entry, custom path template
  python run.py --markdown --path-template YYYY/MM/DD

  # Obsidian Daily Notes: one file per day, named YYYY-MM-DD.md
  python run.py --markdown --merge-day --obsidian --output ~/Obsidian/DayOne

  # Merge by month, all journals in the same folder
  python run.py --markdown --merge-month --merge-journals

  # Custom output directory
  python run.py --output ~/Obsidian/DayOne --markdown --merge-day
""",
    )
    parser.add_argument(
        '--markdown', '-m', action='store_true',
        help='Export as plain Markdown files instead of TextBundles.',
    )
    parser.add_argument(
        '--merge-day', '-d', action='store_true',
        help='Merge all entries of a calendar day into one file.',
    )
    parser.add_argument(
        '--merge-month', '-M', action='store_true',
        help='Merge all entries of a calendar month into one file.',
    )
    parser.add_argument(
        '--merge-journals', '-j', action='store_true',
        help='Put all journals into the same output folder (no journal subfolder).',
    )
    parser.add_argument(
        '--obsidian', action='store_true',
        help=(
            'Use full-date filenames for merged files so they are compatible with '
            'Obsidian Daily Notes: YYYY-MM-DD.md (--merge-day) or YYYY-MM.md (--merge-month). '
            'Has no effect without a merge flag.'
        ),
    )
    parser.add_argument(
        '--path-template', '-p', default='YYYY/MM', metavar='TEMPLATE',
        help=(
            'Obsidian Daily Note-style path template for the output folder. '
            'Tokens: YYYY MM DD HH mm.  Default: YYYY/MM  '
            'Example: YYYY/MM/DD  →  2024/01/15/'
        ),
    )
    parser.add_argument(
        '--output', '-o', default=str(Path.home() / 'tmp/DayOne Export'), metavar='PATH',
        help='Output directory (default: ~/tmp/DayOne Export).',
    )
    args = parser.parse_args()

    if args.merge_day and args.merge_month:
        parser.error("--merge-day and --merge-month are mutually exclusive.")
    if args.obsidian and not (args.merge_day or args.merge_month):
        parser.error("--obsidian requires --merge-day or --merge-month.")

    output_dir = Path(args.output).expanduser()

    if not DAYONE_DB.exists():
        print(f"❌ Database not found:\n   {DAYONE_DB}")
        sys.exit(1)

    mode_parts = ["Markdown" if args.markdown else "TextBundle"]
    if args.merge_day:      mode_parts.append("merge-day")
    if args.merge_month:    mode_parts.append("merge-month")
    if args.merge_journals: mode_parts.append("merge-journals")
    if args.obsidian:       mode_parts.append("obsidian")
    mode = ", ".join(mode_parts)

    print(f"📂 Reading:  {DAYONE_DB}")
    print(f"📸 Photos:   {DAYONE_PHOTOS} ({'✅' if DAYONE_PHOTOS.exists() else '❌ not found'})")
    print(f"🎬 Videos:   {DAYONE_VIDEOS} ({'✅' if DAYONE_VIDEOS.exists() else '❌ not found'})")
    print(f"🔊 Audio:    {DAYONE_AUDIOS} ({'✅' if DAYONE_AUDIOS.exists() else '❌ not found'})")
    print(f"📄 PDFs:     {DAYONE_PDFS} ({'✅' if DAYONE_PDFS.exists() else '❌ not found'})")
    print(f"📤 Output:   {output_dir}  [{mode}]")
    print(f"📁 Template: {args.path_template}\n")

    conn = sqlite3.connect(DAYONE_DB)
    conn.row_factory = sqlite3.Row

    journals, attachments = load_journals_and_attachments(conn)
    print(f"📚 {len(journals)} journal(s): {list(journals.values())}")

    tags_map      = load_tags(conn)
    locations_map = load_locations(conn)
    print(f"🏷️  Tags loaded for {len(tags_map)} entries")
    print(f"📍 Locations loaded for {len(locations_map)} entries\n")

    entries = conn.execute(
        "SELECT Z_PK, ZJOURNAL, ZCREATIONDATE, ZMARKDOWNTEXT, ZUUID, ZSTARRED "
        "FROM ZENTRY ORDER BY ZCREATIONDATE"
    ).fetchall()
    conn.close()

    print(f"✏️  Exporting {len(entries)} entries…\n")

    ok, errors = export_all(
        entries       = entries,
        journals      = journals,
        attachments   = attachments,
        tags_map      = tags_map,
        locations_map = locations_map,
        output_dir    = output_dir,
        as_markdown   = args.markdown,
        merge_day     = args.merge_day,
        merge_month   = args.merge_month,
        merge_journals = args.merge_journals,
        obsidian       = args.obsidian,
        path_template  = args.path_template,
    )

    print(f"\n🎉 Done!  {ok}/{len(entries)} entries exported")
    if errors:
        print(f"   ⚠️  {errors} error(s)")
    print(f"   → {output_dir}")


if __name__ == '__main__':
    main()
