# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

QBit-Tagger is a single-run CLI script that connects to a qBittorrent instance over its
WebUI API and reconciles each torrent's **tags** (and upload limits / categories) against
rules derived from per-tracker config. It also handles orphaned-file cleanup and automated
torrent deletion. It is meant to be run periodically (e.g. cron / Unraid User Scripts),
not as a daemon — every invocation is a full pass over all torrents.

## Commands

```bash
pip3 install -r requirements.txt

# Default run: connect using config.yaml, only updates tags
python3 ./qb-tagger.py

# Common flags
python3 ./qb-tagger.py -c config.yaml          # use a specific config file (default: config.yaml)
python3 ./qb-tagger.py -d                       # dry-run: print intended changes, write nothing
python3 ./qb-tagger.py -n                       # no ANSI color (use for Unraid User Scripts logs)
python3 ./qb-tagger.py -o <hash>[,<hash>...]    # dump computed TorrentInfo for given hashes
python3 ./qb-tagger.py -o <hash> -e             # extended dump (raw torrent dict/trackers/files, redacted)

# Operations (-op is append-style; repeat to combine). update-tags always runs unless other ops chosen.
python3 ./qb-tagger.py -op update-tags
python3 ./qb-tagger.py -op auto-delete          # delete torrents whose tags match auto_delete_tags
python3 ./qb-tagger.py -op move-orphaned        # move (then optionally remove) orphaned files on disk
```

There is no test suite, linter, or build step. Use `-d` (dry-run) against a real qBittorrent
instance as the primary way to validate changes.

## Architecture

Execution is orchestrated top-to-bottom in `qb-tagger.py`:

1. **ConfigManager** loads `config.yaml`, deep-merges it with `default_config`, and immediately
   **rewrites the file** (`config_manager.save()`) to backfill missing keys and strip obsolete ones.
2. **TorrentManager** connects to qBittorrent and runs the pipeline:
   - `get_torrents()` — fetch every torrent + its trackers + files, wrap each in a `TorrentInfo`.
   - `analyze_torrents()` — **two passes** (see below).
   - `update_torrents()` / `auto_delete_torrents()` / `move_orphaned()` + `remove_orphaned()` depending on `-op`.

### The two-pass analysis (important)

`analyze_torrents()` runs two separate loops on purpose:

- **Pass 1 — `analyze_torrient()`**: computes `cross_seed_state` and `delete_state` per torrent.
  Cross-seed grouping uses the static `TorrentInfo.ContentPath_Dict` (torrents sharing a
  `content_path`). A torrent that downloaded 0 bytes but is complete is a `PEER`; one that has
  downloaded data is a `PARENT`.
- **Pass 2 — `set_torrent_info()`**: turns the computed state into concrete tag add/remove
  operations. It is separate because deciding a peer is an `ORPHAN` requires knowing whether any
  of its cross-seed siblings is a `PARENT` — which is only known after pass 1 has visited all torrents.

### State-as-tags model

All derived state is expressed as qBittorrent tags. Enums in `src/torrentinfo.py` are the source of truth:

- `TagNames` — descriptive tags (`#_unregistered`, `#_tracker_error`, `#_rarred`, `#_season_pack`, `#_throttled`, `#_hardlink`, `#_no_hardlink`, `#_cs_all`, `PTP-Archive`).
- `CrossSeedState` — `#_cs_none` / `#_cs_parent` / `#_cs_peer` / `#_cs_orphan`.
- `DeleteState` — `#_delete_*` family (e.g. `#_delete_ready`, `#_delete_now`, `#_delete_autobrr`, `#_delete_hardlink`, `#_delete_malware`, `#_keep_last`, `#_delete_never`).

A torrent is exactly one `CrossSeedState` and one `DeleteState` at a time; `update_*_tags()` add the
matching tag and remove all sibling tags in the enum. Adding a new state = add an enum member.

### Mutation batching

`TorrentInfo` never calls the qBittorrent API. Instead `torrent_add_tag()` / `torrent_remove_tag()` /
`torrent_set_upload_limit()` / `torrent_remove_category()` diff against `current_tags` and accumulate
intended changes into `update_tags_add`, `update_tags_remove`, `update_upload_limit`, and an
`UpdateState` flag bitmask. `TorrentManager.update_torrents()` later reads that bitmask and performs the
actual API calls (respecting `dry_run`). So most logic is side-effect-free and testable by inspecting a
`TorrentInfo` (that's what `-o` dumps).

### Config schema lives in code

The canonical config schema is the `default_config` `OrderedDict` in `qb-tagger.py` (lines ~28-63), **not**
in any YAML file. `config.yaml` is regenerated from it on every run. To add/rename/remove a config option
you must edit `default_config`; the YAML will be reconciled automatically (and unknown keys deleted).

### Per-tracker rules (`trackers.json`)

`tracker_config` (default `trackers.json`) is a JSON array of tracker entries. Each torrent is matched to
the **first** entry whose `trackers` list contains a substring of any of the torrent's announce URLs.
Non-private torrents fall back to the special `"public"` entry. Per-entry keys consumed by the logic:
`throttle` / `throttle_dl` (KiB/s upload caps, applied seeding vs. downloading), `delete` (age in days
before deletion eligibility), `autobrr_delete` (override for autobrr-tagged torrents), `keep_last`
(preserve N newest <10GB non-cross-seed torrents for bonus points), `polite` (seed longer if seeders <
this). See `example_trackers.json`.

### Global singletons

`src/util.py` holds module-level globals set up at startup and read everywhere: `Config_Manager`
(the `ConfigManager`), `Current_Time` (one timestamp captured at import, so all age math is consistent
within a run), and `Discord_Summary` (a list of `(name, value)` tuples accumulated by operations and
sent as a Discord embed at the end if notifications are enabled).

## Path mapping

The script reads file metadata directly from disk (hardlink detection via `st_nlink`, orphan scanning,
file move/delete). When qBittorrent runs in a container, its `save_path` differs from the host path the
script sees. `path_mappings` in config rewrites `container_path` → `host_path`. `util.format_path()`
normalizes every path with a trailing slash — keep using it when comparing paths.

## Notes

- `src/` is a package (imported as `from src...`); run the script from the repo root so imports resolve.
- `junkyard/` contains old standalone experiments — not imported by the main script; ignore unless asked.
- Deletion operations export a `.torrent` backup to `backup_destination` before removing; auto-delete
  uses `delete_files=False` and relies on the orphan-cleanup pass to reclaim disk.
