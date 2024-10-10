
import argparse
from collections import OrderedDict

from src.config import ConfigManager
from src.torrentmanager import TorrentManager
from src.torrentinfo import *
from src import util

print()
header = "|| QBit-Tagger version 2.0 ||"
padding = "=" * len(header)
print(f"{padding}\n{header}\n{padding}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage torrents in qBittorrent.")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to the config file.")
    parser.add_argument("-d", "--dry-run", default=False, action="store_true", help="Perform a dry run without making changes.")
    parser.add_argument("-n", "--no-color", default=False, action="store_true", help="No color in output. Useful when running in unraid via User scripts.")
    parser.add_argument("-o", "--output-hash", default=None, help="Torrent hash or hashes (comma separated) for which to print TorrentInfo.")
    parser.add_argument("-e", "--output-extended", default=False, action="store_true", help="Print extended output. Only works when -o is used.")
    parser.add_argument("-op", "--operation", default=None, choices=('update-tags', 'move-orphaned', 'auto-delete'), action="append", help="Execution mode.")

    args = parser.parse_args()
    print(f"DRY-RUN: {args.dry_run}")
    print(f"CONFIG: {args.config}")

    default_config = OrderedDict([
        ('server', 'localhost'),
        ('port', 8080),
        ('tracker_config', 'trackers.json'),
        ('path_mappings', []),
        ('options', {
            'tag_hardlink': False   ,
            'remove_category_for_bad_torrents': False
        }),
        ('orphaned_files', {
            'move_orphaned': False,
            'orphan_destination': None,
            'move_orphaned_after_days': 30,
            'remove_orphaned_age_days': -1,
            'excluded_save_paths': []
        }),
        ('auto_delete_torrents', {
            'enabled': False,
            'auto_delete_tags': [],
            'auto_delete_age_days': 3,
            'backup_destination': None
        }),
        ('autobrr', {
            'enabled': True,
            'autobrr_tag_name': 'autobrr',
            'default_delete_days': 14
        })
    ])

    # Initialize the ConfigManager with the config file path and default values
    config_manager = ConfigManager(args.config, default_config)
    config_manager.save() # save the file back to populate missing settings in config.yaml
    util.Config_Manager = config_manager

    # print(args)
    # exit(1)

    manager = TorrentManager(args.dry_run, args.no_color)
    manager.get_torrents()
    manager.analyze_torrents()

    # default, always update tags
    if not args.operation or "update-tags" in args.operation:
        manager.update_torrents()

    # only run orphaned related tasks when explicitly specified
    if args.operation and "move-orphaned" in args.operation:
        manager.move_orphaned()
        manager.remove_orphaned()

    # only run auto-delete when explicitly specified
    if args.operation and "auto-delete" in args.operation:
        manager.auto_delete_torrents()

    print()

    if args.output_hash:
        hash_list = [h.strip() for h in args.output_hash.split(",")]  # Split and strip whitespaces
        for torrent_hash in hash_list:
            torrent_info = manager.torrent_info_list.get(torrent_hash)
            if torrent_info:  # Checks if the list is not empty
                print(torrent_info.to_str(args.output_extended))
            else:
                print(f"\nWARNING: Torrent with hash {torrent_hash} not found.\n")
