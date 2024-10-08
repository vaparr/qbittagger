
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
    parser.add_argument("-t", "--tracker-config", default="trackers.json", help="Path to the tracker config json file.")
    parser.add_argument("-d", "--dry-run", default=False, action="store_true", help="Perform a dry run without making changes.")
    parser.add_argument("-n", "--no-color", default=False, action="store_true", help="No color in output. Useful when running in unraid via User scripts.")
    parser.add_argument("-o", "--output-hash", default=None, help="Torrent hash or hashes (comma separated) for which to print TorrentInfo.")
    parser.add_argument("-e", "--output-extended", default=False, action="store_true", help="Print extended output. Only works when -o is used.")

    args = parser.parse_args()

    default_config = OrderedDict([
        ('server', 'localhost'),
        ('port', 8080),
        ('default_autobrr_delete_days', 14),
        ('remove_category_for_bad_torrents', False),
        ('tag_hardlink', False),
        ('move_orphaned', False),
        ('remove_orphaned_age_days', -1),
        ('orphaned_destination', "/path/on/host/qb-orphaned/"),
        ('path_mappings', [
            {'container_path': '/path1/in/container', 'host_path': '/path1/on/host'},
            {'container_path': '/path2/in/container', 'host_path': '/path2/on/host'}
        ]),
        ('excluded_save_paths', [
            '/path1/on/host',
            '/path2/on/host'
        ])
    ])

    # Initialize the ConfigManager with the config file path and default values
    config_manager = ConfigManager(args.config, default_config)
    config_manager.save() # save the file back to populate missing settings in config.yaml
    util.Config_Manager = config_manager

    manager = TorrentManager(args.dry_run, args.no_color, args.tracker_config)
    manager.get_torrents()
    manager.analyze_torrents()
    manager.update_torrents()
    manager.move_orphaned()
    manager.remove_orphaned()

    if args.output_hash:
        hash_list = [h.strip() for h in args.output_hash.split(",")]  # Split and strip whitespaces
        for torrent_hash in hash_list:
            torrent_info = manager.torrent_info_list.get(torrent_hash)
            if torrent_info:  # Checks if the list is not empty
                print(torrent_info.to_str(args.output_extended))
            else:
                print(f"\nWARNING: Torrent with hash {torrent_hash} not found.\n")
