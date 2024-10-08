import argparse

from colorama import Fore

from src.config import ConfigManager
from src.torrentmanager import TorrentManager
from src.torrentinfo import *

print()
header = "|| QBit-Deleter version 2.0 ||"
padding = "=" * len(header)
print(f"{padding}\n{header}\n{padding}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete torrents in qBittorrent.")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to the config file.")
    parser.add_argument("-d", "--dry-run", default=False, action="store_true", help="Perform a dry run without making changes.")
    parser.add_argument("-n", "--no-color", default=False, action="store_true", help="No color in output. Useful when running in unraid via User scripts.")
    parser.add_argument("-t", "--tag-name", default="#_delete_ready", help="Tag name for which to list torrents.")

    args = parser.parse_args()

    config_manager = ConfigManager(args.config, None)
    util.Config_Manager = config_manager

    manager = TorrentManager(args.dry_run, args.no_color, None)

    tag_name = args.tag_name
    total_size = 0
    print()
    torrents_dict = manager.qb.torrents_info()
    for torrent in torrents_dict:
        torrent_tags = [t.strip() for t in torrent.get("tags", "").split(",")]
        if tag_name in torrent_tags:
            torrent_hash = torrent['hash']
            torrent_name = torrent['name']
            torrent_size = torrent['size']
            total_size += torrent_size
            formatted_size = util.format_bytes(torrent_size)
            if args.dry_run:
                print(f"-- [DRY RUN] Will remove torrent '{torrent_name if args.no_color else f'{Fore.YELLOW}{torrent_name}{Fore.RESET}'}' ({torrent_hash if args.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}) with size '{formatted_size if args.no_color else f'{Fore.GREEN}{formatted_size}{Fore.RESET}'}'")
            else:
                print(f"-- Removing torrent '{torrent_name if args.no_color else f'{Fore.YELLOW}{torrent_name}{Fore.RESET}'}' ({torrent_hash if args.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}) with size '{formatted_size if args.no_color else f'{Fore.GREEN}{formatted_size}{Fore.RESET}'}'")
                # remove torrents with delete_files set to False, as orphan cleanup will take care of them.
                # manager.qb.torrents_delete(delete_files=False, torrent_hashes=torrent_hash)

    print()
    if args.dry_run:
        print(f"[DRY RUN] Total size of removed torrents with '{tag_name if args.no_color else f'{Fore.GREEN}{tag_name}{Fore.RESET}'}' tag: {util.format_bytes(total_size)}")
    else:
        print(f"Total size of removed torrents with '{tag_name if args.no_color else f'{Fore.GREEN}{tag_name}{Fore.RESET}'}' tag: {util.format_bytes(total_size)}")
    print()