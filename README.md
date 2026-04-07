# dayone2textbundle

Export your [Day One](https://dayoneapp.com) journal to [TextBundle](https://textbundle.org) files — one `.textbundle` per entry, organized by journal and year.

## Output structure

```
~/tmp/DayOne Export/
└── Journal Name/
    └── 2024/
        ├── 2024-03-15 0930 Morning notes.textbundle
        ├── 2024-03-15 1430 Meeting with team.textbundle
        └── ...
```

Each bundle contains:
- `text.md` — the entry's markdown text with YAML front matter (date, starred, uuid)
- `info.json` — TextBundle metadata
- `assets/` — any attached photos, videos, audio files, or PDFs

## Requirements

- macOS (reads Day One's local SQLite database)
- Python 3.10+
- Day One app installed with at least one journal

## Usage

```sh
python3 run.py
```

The script reads directly from Day One's database at:

```
~/Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/Documents/DayOne.sqlite
```

Output is written to `~/tmp/DayOne Export/`. You can change this by editing `OUTPUT_DIR` near the top of `run.py`.

## Notes

- Attachments are resolved by MD5 hash (the way Day One stores them on disk) and copied into each bundle's `assets/` folder.
- If an attachment can't be found, a comment is left in the markdown: `<!-- attachment not found: ... -->`.
- The script does not modify Day One's database or files in any way.
