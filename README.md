# QBit-Tagger

Tags qBittorrent torrents by tracker, cross-seed status and deletion eligibility, and applies
per-tracker upload limits. It can also auto-delete torrents by tag, clean up orphaned files, and
move Sonarr/Radarr torrents that contain dangerous files out of the arr's category.

It runs once and exits. Schedule it with cron or Unraid User Scripts.

## Setup

```bash
pip3 install -r requirements.txt
cp example_trackers.json trackers.json   # then edit: one entry per tracker
python3 ./qb-tagger.py -d                # first run writes config.yaml, then dry-runs
```

Edit the generated `config.yaml`: set `server`, `port` and credentials, then enable the features
you want. The file is rewritten on every run to add new options and drop old ones, so edit values
but don't expect comments to survive.

## Usage

```bash
python3 ./qb-tagger.py                    # update tags (default operation)
python3 ./qb-tagger.py -d                 # dry run: print planned changes, change nothing
python3 ./qb-tagger.py -n                 # no color (use for Unraid User Scripts logs)
python3 ./qb-tagger.py -op auto-delete    # delete torrents tagged with auto_delete_tags
python3 ./qb-tagger.py -op move-orphaned  # move, then optionally remove, orphaned files
python3 ./qb-tagger.py -o <hash> [-e]     # dump the computed state for a torrent
```

Repeat `-op` to combine operations, e.g. `-op update-tags -op auto-delete`.

Always try new config with `-d` first.

## trackers.json

A torrent uses the first entry whose `trackers` list contains a substring of any of its announce
URLs. Put every announce domain a tracker uses in that list. Public torrents use the `public` entry.
Torrents that match nothing are listed in a warning at the end of the run.

| Key | Meaning |
|---|---|
| `throttle` / `throttle_dl` | Upload cap in KiB/s while seeding / downloading (0 = unlimited) |
| `delete` | Days after completion before the torrent is eligible for deletion |
| `autobrr_delete` | Same as `delete`, but for autobrr-tagged torrents |
| `keep_last` | Protect the N oldest non-cross-seed torrents under 10 GB (bonus points) |
| `polite` | If the swarm has fewer seeders than this, tag `#_delete_if_needed` instead of `#_delete_ready` |

## Dangerous files

`banned_extensions` in `config.yaml` holds two lists of file extensions (case-insensitive):

- `dangerous`: any torrent with one of these files is tagged `#_delete_malware`.
- `executable`: together with `dangerous`, a torrent in a `sonarr*` or `radar*` category with any
  of these files is moved to `<category>-dangerous` (created if missing), so the arr stops
  tracking it.

## Tags

| Tag | Meaning |
|---|---|
| `<tracker name>` | The matching `trackers.json` entry |
| `#_cs_parent` / `#_cs_peer` / `#_cs_orphan`, `#_cs_all` | Cross-seed role; orphan = peer whose parent is gone |
| `#_delete_*`, `#_keep_last` | Deletion state; add the ones you want removed to `auto_delete_tags` |
| `#_unregistered`, `#_tracker_error` | Tracker reports the torrent as gone, or every tracker is failing |
| `#_rarred`, `#_season_pack`, `#_throttled` | Content and upload-limit info |
| `#_hardlink` / `#_no_hardlink` | Hardlink status (needs `options.tag_hardlink` and `path_mappings`) |
