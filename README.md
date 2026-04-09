# dayone2textbundle

Export your [Day One](https://dayoneapp.com) journal to [TextBundle](https://textbundle.org) files or plain Markdown — with full support for tags, locations, merged daily/monthly files, and [Obsidian](https://obsidian.md) compatibility.

## Requirements

- macOS (reads Day One's local SQLite database directly)
- Python 3.10+
- Day One app installed with at least one journal

## Usage

```sh
python3 run.py [options]
```

### Options

| Flag                      | Short | Description                                                                    |
| ------------------------- | ----- | ------------------------------------------------------------------------------- |
| `--markdown`              | `-m`  | Export as plain `.md` files instead of `.textbundle`                           |
| `--merge-day`             | `-d`  | Merge all entries of a calendar day into one file                              |
| `--merge-month`           | `-M`  | Merge all entries of a calendar month into one file                            |
| `--merge-journals`        | `-j`  | Put all journals into the same folder (no journal subfolder)                   |
| `--obsidian`              |       | Use full-date filenames (`YYYY-MM-DD.md`) for Obsidian Daily Notes compatibility |
| `--path-template YYYY/MM` | `-p`  | Obsidian Daily Note-style path template (default: `YYYY/MM`)                  |
| `--output PATH`           | `-o`  | Output directory (default: `~/tmp/DayOne Export`)                              |

`--merge-day` and `--merge-month` are mutually exclusive. `--obsidian` requires one of them.

### Examples

```sh
# One TextBundle per entry (default)
python3 run.py

# Plain Markdown, one file per entry
python3 run.py --markdown

# Merge all entries of each day into one Markdown file (Obsidian Daily Note style)
python3 run.py --markdown --merge-day --path-template YYYY/MM/DD

# Merge by month, all journals in the same folder
python3 run.py --markdown --merge-month --merge-journals

# Export directly to an Obsidian vault
python3 run.py --markdown --merge-day --output ~/Obsidian/DayOne
```

## Output structure

### Default — one TextBundle per entry

```text
~/tmp/DayOne Export/
└── Journal Name/
    └── 2024/01/
        ├── 2024-01-15 0930 Morning notes.textbundle/
        │   ├── text.md
        │   ├── info.json
        │   └── assets/
        └── 2024-01-15 1430 Afternoon walk.textbundle/
```

### `--markdown` — one `.md` per entry

```text
~/tmp/DayOne Export/
└── Journal Name/
    └── 2024/01/
        ├── 15 0930 Morning notes.md
        └── 15 1430 Afternoon walk.md
```

### `--merge-day` — one file per day

```text
~/tmp/DayOne Export/
└── Journal Name/
    └── 2024/01/
        └── 15.md          ← all entries from 2024-01-15 combined
```

### `--merge-month` — one file per month

```text
~/tmp/DayOne Export/
└── Journal Name/
    └── 2024/
        └── 01.md          ← all entries from January 2024 combined
```

## Frontmatter / Properties

Every exported file includes YAML front matter with enough metadata for Obsidian features like [Map view](https://obsidian.md/help/bases/views/map) and [Cards view](https://obsidian.md/help/bases/views/cards).

### Single entry

```yaml
---
date: 2024-01-15
time: "09:30"
created: 2024-01-15T09:30:00+01:00
title: Morning notes
starred: false
uuid: ABC123...
year: 2024
month: 1
day: 15
day_of_year: 15
month_day: 01-15        # filter "On This Day" across years
weekday: Monday
tags:
  - hiking
  - travel
location: [48.137154, 11.576124]   # Obsidian Map view format
place: Marienplatz, Germany
---
```

### Merged file (day or month)

When entries are merged, properties are combined:

- **tags** — union of all entry tags
- **starred** — `true` if any entry is starred
- **location / place** — taken from the first entry that has one
- **entry_count** — number of entries in the file
- Day-specific keys (`day`, `day_of_year`, `month_day`, `weekday`) are included for day-merges

Each entry within the merged file gets its own `## HH:MM – Title` heading, separated by `---`.

## Obsidian integration

<img width="3230" height="2200" alt="image" src="https://github.com/user-attachments/assets/ad39a279-2bd4-47e1-bbe3-b2f115b266a5" />

with all values as properties:

<img width="815" height="708" alt="image" src="https://github.com/user-attachments/assets/804fbe18-6992-42b5-a3fa-51dc5986ee7d" />


### Map view

The `location: [lat, lng]` property is recognised by Obsidian's built-in Map view out of the box.

## Daily Note Plugin support

<img width="654" height="590" alt="image" src="https://github.com/user-attachments/assets/7ec34013-c588-4553-a2f0-ed9c8ef58c52" />



### "On This Day" (Cards view)

Use `month_day` (e.g. `01-15`) to filter entries from the same calendar day across different years:

```text
formulas:
  on_this_day: |
    if(date(now()).month == date(file.name).month,
      if(date(now()).day == date(file.name).day,
        "true",
        "false"),
      "false")
views:
  - type: cards
    name: On This Day
    filters:
      and:
        - formula.on_this_day.contains("true")
    order:
      - file.name
      - title
      - summary
      - first_image
    sort:
      - property: file.name
        direction: DESC

```

Combine with the Cards view to build an "On This Day" board.

### Path template

The `--path-template` flag mirrors the Obsidian Daily Note plugin's folder format.
`YYYY/MM/DD` produces `2024/01/15.md` with `--merge-day`, matching a Daily Notes vault exactly.

Available tokens: `YYYY` `MM` `DD` `HH` `mm`

## Notes

- The script reads Day One's SQLite database at
  `~/Library/Group Containers/5U8NS4GX82.dayoneapp2/Data/Documents/DayOne.sqlite`
- Attachments are resolved by MD5 hash (the way Day One stores them on disk) and copied into each file's `assets/` folder.
- If an attachment can't be found, a comment is inserted: `<!-- attachment not found: … -->`
- The script never modifies Day One's database or original files.
