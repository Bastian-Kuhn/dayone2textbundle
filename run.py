#!/usr/bin/env python3
"""
DayOne → TextBundle Exporter
Structure: output/<Journal-Name>/<Year>/<YYYY-MM-DD HHmm Title>.textbundle
"""

import sqlite3
import json
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── Configuration ─────────────────────────────────────────────────────────────

DAYONE_DOCS   = Path.home() / "Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/Documents"
DAYONE_DB     = DAYONE_DOCS / "DayOne.sqlite"
DAYONE_PHOTOS = DAYONE_DOCS / "DayOnePhotos"
DAYONE_VIDEOS = DAYONE_DOCS / "DayOneVideos"
DAYONE_AUDIOS = DAYONE_DOCS / "DayOneAudios"
DAYONE_PDFS   = DAYONE_DOCS / "DayOnePDFAttachments"
OUTPUT_DIR    = Path.home() / "tmp/DayOne Export"

# Apple Core Data Epoch: seconds since 2001-01-01 UTC
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# ── Helper functions ───────────────────────────────────────────────────────────

def apple_ts_to_datetime(ts) -> datetime:
    """Converts an Apple Core Data timestamp to a local datetime."""
    if ts is None:
        return datetime.now().astimezone()
    try:
        return (APPLE_EPOCH + timedelta(seconds=float(ts))).astimezone()
    except (ValueError, TypeError):
        return datetime.now().astimezone()


def safe_filename(name: str, max_len: int = 60) -> str:
    """Sanitizes a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    name = name.strip('. ')
    return name[:max_len] if name else "Untitled"


def derive_title(text: str, date: datetime) -> str:
    """Derives a title from the markdown text."""
    if not text or not text.strip():
        return date.strftime('%H-%M')
    first_line = text.strip().split('\n')[0].strip()
    first_line = re.sub(r'^#+\s*', '', first_line)        # strip heading markers
    first_line = re.sub(r'\*+', '', first_line)            # strip bold/italic
    first_line = re.sub(r'!\[\]\(.*?\)', '', first_line)   # strip image links
    first_line = first_line.strip()
    if len(first_line) > 3:
        return safe_filename(first_line, 60)
    return date.strftime('%H-%M')


def find_attachment(identifier: str, md5: str | None = None, file_type: str | None = None) -> Path | None:
    """Searches for an attachment file across all DayOne media directories.

    DayOne stores files by MD5 hash (ZMD5) in type-specific folders:
      DayOnePhotos/         – images (jpeg, png, heic, gif)
      DayOneVideos/         – videos (mp4, mov)
      DayOneAudios/         – audio (m4a)
      DayOnePDFAttachments/ – PDFs
    """
    # Search dirs: type-specific first, then all others
    ftype_lower = (file_type or '').lower()
    if ftype_lower in ('mp4', 'mov', 'm4v'):
        search_dirs = [DAYONE_VIDEOS, DAYONE_PHOTOS, DAYONE_AUDIOS, DAYONE_PDFS]
    elif ftype_lower in ('m4a', 'aac', 'mp3'):
        search_dirs = [DAYONE_AUDIOS, DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_PDFS]
    elif ftype_lower == 'pdf':
        search_dirs = [DAYONE_PDFS, DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_AUDIOS]
    else:
        search_dirs = [DAYONE_PHOTOS, DAYONE_VIDEOS, DAYONE_AUDIOS, DAYONE_PDFS]

    extensions = ('', '.jpeg', '.jpg', '.png', '.gif', '.heic', '.mp4', '.mov', '.m4v', '.pdf', '.m4a')

    for base in search_dirs:
        if not base.exists():
            continue

        # Primary: MD5 hash + known type
        if md5:
            md5_lower = md5.lower()
            if file_type:
                candidate = base / f"{md5_lower}.{ftype_lower}"
                if candidate.exists():
                    return candidate
            for ext in extensions:
                candidate = base / f"{md5_lower}{ext}"
                if candidate.exists():
                    return candidate

        # Fallback: use ZIDENTIFIER
        for name in (identifier.lower(), identifier.upper()):
            if file_type:
                candidate = base / f"{name}.{ftype_lower}"
                if candidate.exists():
                    return candidate
            for ext in extensions:
                candidate = base / f"{name}{ext}"
                if candidate.exists():
                    return candidate

    # Last resort: rglob across all directories
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


# ── Main logic ─────────────────────────────────────────────────────────────────

def load_data(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Loads journals and attachments from the database."""
    conn.row_factory = sqlite3.Row

    # Journals: PK → Name mapping
    journals = {}
    for row in conn.execute("SELECT Z_PK, ZNAME FROM ZJOURNAL"):
        journals[row['Z_PK']] = row['ZNAME'] or 'Journal'

    # Attachments: entry PK → list of (IDENTIFIER, MD5, TYPE)
    # ZIDENTIFIER = value used in dayone-moment:// links
    # ZMD5        = actual filename on disk
    # ZTYPE       = file extension (jpeg, png, mov, …)
    attachments: dict[int, list[tuple[str, str | None, str | None]]] = {}
    for row in conn.execute(
        "SELECT ZENTRY, ZIDENTIFIER, ZMD5, ZTYPE "
        "FROM ZATTACHMENT WHERE ZENTRY IS NOT NULL AND ZIDENTIFIER IS NOT NULL"
    ):
        entry_pk = row['ZENTRY']
        ident    = row['ZIDENTIFIER'].upper()
        md5      = row['ZMD5']
        ftype    = row['ZTYPE']
        attachments.setdefault(entry_pk, []).append((ident, md5, ftype))

    return journals, attachments


def create_textbundle(entry_row, journals: dict, attachments: dict) -> None:
    """Creates a single TextBundle for a journal entry."""
    entry_pk   = entry_row['Z_PK']
    journal_pk = entry_row['ZJOURNAL']

    # Journal name with fallback
    journal_name = safe_filename(journals.get(journal_pk) or 'Journal')

    date    = apple_ts_to_datetime(entry_row['ZCREATIONDATE'])
    text    = entry_row['ZMARKDOWNTEXT'] or ''
    text    = text.replace('\\.', '.')   # DayOne unnecessarily escapes dots
    uuid    = entry_row['ZUUID'] or ''
    starred = bool(entry_row['ZSTARRED'])

    title = derive_title(text, date)
    year  = date.strftime('%Y')

    # Output directory: Journal / Year
    dest_dir = OUTPUT_DIR / journal_name / year
    dest_dir.mkdir(parents=True, exist_ok=True)

    bundle_name = f"{date.strftime('%Y-%m-%d %H%M')} {title}.textbundle"
    bundle_path = dest_dir / bundle_name
    bundle_path.mkdir(exist_ok=True)

    assets_dir = bundle_path / 'assets'
    att_list = attachments.get(entry_pk, [])

    # Copy attachments and replace dayone-moment:// references
    # Format in text: ![](dayone-moment://IDENTIFIER)
    if att_list:
        assets_dir.mkdir(exist_ok=True)

    for ident, md5, ftype in att_list:
        src = find_attachment(ident, md5, ftype)

        if src:
            dest_filename = src.name if src.suffix else f"{ident}.jpeg"
            shutil.copy2(src, assets_dir / dest_filename)
            new_ref = f"assets/{dest_filename}"
        else:
            new_ref = f"<!-- attachment not found: {ident} -->"

        # Replace all dayone-moment variants for this identifier:
        #   dayone-moment://ID          (photos)
        #   dayone-moment:/video/ID     (videos)
        #   dayone-moment:/pdfAttachment/ID  (PDFs)
        text = re.sub(
            rf'dayone-moment:/+(?:\w+/)?{re.escape(ident)}',
            new_ref,
            text,
            flags=re.IGNORECASE
        )

    # Front matter
    frontmatter = (
        f"---\n"
        f"date: {date.strftime('%Y-%m-%d %H:%M')}\n"
        f"created: {date.isoformat()}\n"
        f"starred: {str(starred).lower()}\n"
        f"uuid: {uuid}\n"
        f"---\n\n"
    )

    (bundle_path / 'text.md').write_text(frontmatter + text, encoding='utf-8')

    info = {
        "version": 2,
        "type": "net.daringfireball.markdown",
        "transient": False,
        "creatorIdentifier": "com.dayone.export",
    }
    (bundle_path / 'info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')


def main():
    if not DAYONE_DB.exists():
        print(f"❌ Database not found:\n   {DAYONE_DB}")
        sys.exit(1)

    print(f"📂 Reading:  {DAYONE_DB}")
    print(f"📸 Photos:   {DAYONE_PHOTOS} ({'✅' if DAYONE_PHOTOS.exists() else '❌ not found'})")
    print(f"🎬 Videos:   {DAYONE_VIDEOS} ({'✅' if DAYONE_VIDEOS.exists() else '❌ not found'})")
    print(f"🔊 Audio:    {DAYONE_AUDIOS} ({'✅' if DAYONE_AUDIOS.exists() else '❌ not found'})")
    print(f"📄 PDFs:     {DAYONE_PDFS} ({'✅' if DAYONE_PDFS.exists() else '❌ not found'})")
    print(f"📤 Output:   {OUTPUT_DIR}\n")

    conn = sqlite3.connect(DAYONE_DB)
    conn.row_factory = sqlite3.Row

    journals, attachments = load_data(conn)
    print(f"📚 {len(journals)} journal(s): {list(journals.values())}\n")

    entries = conn.execute(
        "SELECT Z_PK, ZJOURNAL, ZCREATIONDATE, ZMARKDOWNTEXT, ZUUID, ZSTARRED "
        "FROM ZENTRY "
        "ORDER BY ZCREATIONDATE"
    ).fetchall()

    conn.close()

    print(f"✏️  Exporting {len(entries)} entries...\n")

    errors = 0
    for i, entry in enumerate(entries, 1):
        try:
            create_textbundle(entry, journals, attachments)
        except Exception as e:
            print(f"  ⚠️  Error processing {entry['ZUUID']}: {e}")
            errors += 1

        if i % 100 == 0:
            print(f"  … {i}/{len(entries)}")

    print(f"\n🎉 Done! {len(entries) - errors}/{len(entries)} entries exported")
    if errors:
        print(f"   ⚠️  {errors} error(s)")
    print(f"   → {OUTPUT_DIR}")


if __name__ == '__main__':
    main()
