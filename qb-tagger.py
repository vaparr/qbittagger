import qbittorrentapi
import json
import sys
import argparse
import re
from datetime import datetime, timedelta
from collections import defaultdict
from enum import Enum, Flag, auto
from colorama import Fore, Back, Style, init


class UpdateState(Flag):
    TAG_ADD = auto()
    TAG_REMOVE = auto()
    UPLOAD_LIMIT = auto()


class CrossSeedState(Enum):
    NONE = auto()
    PARENT = auto()
    PEER = auto()
    ORPHAN = auto()


class DeleteState(Enum):
    NONE = auto()
    READY = auto()
    SOFT_DELETE = auto()
    DELETE_IF_NEEDED = auto()
    KEEP_LAST = auto()
    AUTOBRR_DELETE = auto()
    NEVER = auto()


class TorrentInfo:

    # static variable
    File_Dict = defaultdict(list)

    def __init__(self, torrent_dict, torrent_files, torrent_trackers, tracker_options):

        # torrent info
        self.torrent_dict = torrent_dict
        self.torrent_files = torrent_files
        self.torrent_trackers = torrent_trackers

        # torrent props
        self._hash = torrent_dict.hash
        self._name = torrent_dict.name
        self._torrent_age = self.get_age(torrent_dict.added_on)
        self.current_tags = [t.strip() for t in torrent_dict.get("tags", "").split(",")]

        # torrent state
        self.delete_state = DeleteState.NONE
        self.cross_seed_state = CrossSeedState.NONE
        self.cross_seed_hashes = []

        # update props
        self.update_state = UpdateState(0)
        self.update_tags_add = []
        self.update_tags_remove = []
        self.update_upload_limit = 0

        # Track content_path
        self.content_path = self.format_path(torrent_dict.content_path)
        self.File_Dict[self.content_path].append(self)

        # autobrr
        self.is_autobrr_torrent = "autobrr" in self.current_tags

        # private/unregistered based on tracker message
        self.is_private = False
        self.is_unregistered = False
        for tracker in self.torrent_trackers:
            msg = tracker["msg"]
            if "private" in msg:
                self.is_private = True

            if any(
                keyword in msg
                for keyword in [
                    "Unregistered",
                    "not registered",
                    "pack out",
                    "Complete Season",
                    "Dupe of",
                    "beyond-hd.me",
                    "InfoHash not found",
                    "Tracker Inactive",
                    "Invalid InfoHash",
                    "unregistered torrent",
                ]
            ):
                self.is_unregistered = True

        # Find the first matching tracker for the torrent
        self.tracker_opts = None
        torrent_tracker_urls = {tracker.url for tracker in torrent_trackers}  # Create a set of tracker URLs for faster lookup
        for tracker_entry in tracker_options:
            # Check if any tracker URL in the option is a substring of the tracker URLs
            if any(tracker_url in url for url in torrent_tracker_urls for tracker_url in tracker_entry["trackers"]):
                self.tracker_opts = tracker_entry
                break  # Break after the first match to avoid duplicates

        # Set the tracker name and options
        self.tracker_name = None
        if self.tracker_opts:
            self.tracker_name = self.tracker_opts["name"]

        # public
        if not self.is_private:
            self.tracker_name = "public"
            public_tracker_entry = next((tracker for tracker in tracker_options if tracker["name"] == "public"), None)
            self.tracker_opts = public_tracker_entry

        # Is rarred?
        self.is_rarred = False
        for file in self.torrent_files:
            if file.name.endswith(".rar"):
                self.is_rarred = True
                break

        # Has multiple files?
        # self.is_multi_file = len(self.torrent_files) > 2

        # Season pack?
        self.is_season_pack = self.check_season_pack(self._name)

        # How many seeders? It's polite to seed if there's less seeders than polite value in config.
        politeness = self.tracker_opts.get("polite", 0) if self.tracker_opts is not None else 0
        self.is_polite_to_seed = (self.torrent_dict["num_complete"] < politeness) if politeness > 0 else False

    def check_season_pack(self, torrent_name: str) -> bool:
        season_pack_patterns = [
            r"S\d{1,2}[^E]",  # Match season like "S01", "S01-S02", without episode
            r"Season \d+",  # Match "Season 1", "Season 2"
            r"Series \d+",  # Match "Series 1", "Series 2"
            r"S\d{1,2}\s?$"  # Match patterns like "S05" at the end with optional spaces
            r"Complete",  # Match "Complete" in the name
        ]
        episode_pattern = r"S\d{2}E\d{2}"  # Match episodes like "S01E01", "S02E03"

        if re.search(episode_pattern, torrent_name, re.IGNORECASE):
            return False
        if any(re.search(pattern, torrent_name, re.IGNORECASE) for pattern in season_pack_patterns):
            return True

        return None  # Could not determine

    def torrent_add_tag(self, tag):
        # Add the tag only if it's not in current tags and not already scheduled for adding
        if tag not in self.current_tags and tag not in self.update_tags_add:
            self.update_tags_add.append(tag)  # Using set for efficient lookups
            self.update_state |= UpdateState.TAG_ADD

        # Remove it from the removal list if it was marked for removal
        if tag in self.update_tags_remove:
            self.update_tags_remove.remove(tag)
            # Check if there are no more tags left to remove and clear TAG_REMOVE flag
            if not self.update_tags_remove:
                self.update_state &= ~UpdateState.TAG_REMOVE

    def torrent_remove_tag(self, tag):
        # Remove the tag only if it's in current tags and not already scheduled for removal
        if tag in self.current_tags and tag not in self.update_tags_remove:
            self.update_tags_remove.append(tag)
            self.update_state |= UpdateState.TAG_REMOVE

        # Remove it from the add list if it was scheduled to be added
        if tag in self.update_tags_add:
            self.update_tags_add.remove(tag)  # Remove tag from tags_add
            # Check if there are no more tags left to add and clear TAG_ADD flag
            if not self.update_tags_add:
                self.update_state &= ~UpdateState.TAG_ADD

    def torrent_set_upload_limit(self, tracker_entry):
        # Set default to 0 if throttle values are 0 or non-existent
        up_limit = tracker_entry.get("throttle_dl", -1) or 0
        if self.torrent_dict["amount_left"] == 0 or self.torrent_dict["dlspeed"] == 0:
            up_limit = tracker_entry.get("throttle", -1) or 0

        up_limit = up_limit * 1024
        if self.torrent_dict["up_limit"] != up_limit:
            self.update_upload_limit = up_limit
            self.update_state |= UpdateState.UPLOAD_LIMIT

    def get_age(self, added_on):

        # Calculate age in seconds (Current time in seconds since the epoch - added_on)
        age_in_seconds = datetime.now().timestamp() - added_on

        # Convert seconds to a more readable format (days, hours, minutes)
        age_days = age_in_seconds // 86400  # Number of seconds in a day
        age_hours = (age_in_seconds % 86400) // 3600  # Remaining hours
        age_minutes = (age_in_seconds % 3600) // 60  # Remaining minutes

        return f"{age_days} days, {age_hours} hours, {age_minutes} minutes"

    def format_path(self, path):
        if not path.endswith("/"):
            path = path + "/"
        return path

    def to_str(self, include_extended=False):
        # List of attributes to exclude from dynamic formatting
        excluded_attrs = {"torrent_dict", "torrent_files", "torrent_trackers"}

        # Retrieve all instance attributes and exclude the specified ones
        attrs = {key: value for key, value in vars(self).items() if key not in excluded_attrs}

        # Sort the attributes by key name and prepare the formatted output with both key and value
        # str_attrs = "\n".join([f"\t{key} = {value}" for key, value in attrs.items()])
        str_attrs = "\n".join([f"\t{key} = {value}" for key, value in sorted(attrs.items())])

        # Special formatting for 'torrent_dict'
        str_torrent_dict = str(self.torrent_dict).replace(", '", "\n\t\t, '").replace("TorrentDictionary({", "TorrentDictionary({\n\t\t").replace("})", "\n\t})\n")

        # Combine the dynamically generated attributes and the formatted torrent_dict
        if include_extended:
            formatted_str = f"{str_attrs}\n\ttorrent_dict={str_torrent_dict}"
        else:
            formatted_str = f"{str_attrs}"

        return f"\nTorrentInfo(\n{formatted_str}\n)"


class TorrentManager:

    def __init__(self, server, port, dry_run, no_color, tracker_json_path):

        # args
        self.server = server
        self.port = port
        self.dry_run = dry_run
        self.no_color = no_color

        # dict to store torrents
        self.torrent_info_list = defaultdict(list)
        self.torrent_tag_hashes_list = defaultdict(list)

        # tracker config
        self.tracker_options = self.load_trackers(tracker_json_path)
        self.default_autobrr_delete_days = 14  # days

        # connect to qb
        self.qb = self.connect_to_qb(server, port)

        # process torrents and create list of TorrentInfo objects
        print(f"\n=== Phase 1: Getting a list of torrents from qBitTorrent... ")
        qb_torrents = self.qb.torrents_info()
        for torrent_dict in qb_torrents:

            # get additional torrent info
            torrent_files = self.qb.torrents_files(torrent_dict.hash)
            torrent_trackers = self.qb.torrents_trackers(torrent_dict.hash)

            # create new TorrentInfo object and add it to dict
            torrent_info = TorrentInfo(torrent_dict, torrent_files, torrent_trackers, self.tracker_options)
            self.torrent_info_list[torrent_dict.hash] = torrent_info

        # store hashes per tag in a list
        self.build_tag_to_hashes()

        # process the list for cross-seeds and deletes and set torrentinfo object props accordingly
        print(f"\n=== Phase 2: Analyzing torrents... ")
        for torrent_info in self.torrent_info_list.values():
            self.analyze_torrent(torrent_info)

    def update_torrents(self):

        i = 0
        print(f"\n=== Phase 3: Updating torrents\n")
        for torrent_info in self.torrent_info_list.values():

            self.set_torrent_info(torrent_info)

            if torrent_info.update_state == UpdateState(0):
                continue

            i = i + 1
            if self.no_color:
                print(f"=== Updating [{torrent_info.tracker_name}] torrent {torrent_info._name} ({torrent_info._hash})")
            else:
                print(
                    f"=== Updating [{Fore.MAGENTA}{torrent_info.tracker_name}{Fore.RESET}] torrent {Fore.YELLOW}{torrent_info._name}{Fore.RESET} ({Fore.CYAN}{torrent_info._hash}{Fore.RESET})"
                )

            # add tags
            if UpdateState.TAG_ADD in torrent_info.update_state:
                self.qb_add_tag(torrent_info)

            # remove tags
            if UpdateState.TAG_REMOVE in torrent_info.update_state:
                self.qb_remove_tag(torrent_info)

            # set upload limit
            if UpdateState.UPLOAD_LIMIT in torrent_info.update_state:
                self.qb_set_upload_limit(torrent_info)

        print(f"\nProcessed {len(self.torrent_info_list)} torrents and updated {i} torrents.\n")

    def build_tag_to_hashes(self):

        # Iterate over all torrent info in the list
        for torrent_info in self.torrent_info_list.values():
            # Get the tags and hash for the torrent
            tags = [t.strip() for t in torrent_info.torrent_dict.get("tags", "").split(",")]

            # Add the torrent hash to the corresponding tag in the defaultdict
            for tag in tags:
                if tag:  # Avoid adding empty tags
                    self.torrent_tag_hashes_list[tag].append(torrent_info._hash)

    def set_torrent_info(self, torrent_info: TorrentInfo):

        # set upload limit
        if torrent_info.tracker_opts:
            torrent_info.torrent_set_upload_limit(torrent_info.tracker_opts)

        # set tracker tag
        if torrent_info.tracker_name:
            torrent_info.torrent_add_tag(torrent_info.tracker_name)

        # set unregistered
        if torrent_info.is_unregistered:
            torrent_info.torrent_add_tag("_unregistered")

        # rarred
        if torrent_info.is_rarred:
            torrent_info.torrent_add_tag("_rarred")

        # season pack
        if torrent_info.is_season_pack:
            torrent_info.torrent_add_tag("_season_pack")

        # throttled
        if torrent_info.torrent_dict["up_limit"] > 0:
            torrent_info.torrent_add_tag("_throttled")
        else:
            torrent_info.torrent_remove_tag("_throttled")

        # Cross-seeded, orphaned peers
        if torrent_info.cross_seed_state == CrossSeedState.PEER:
            hasParent = False
            for cross_hash in torrent_info.cross_seed_hashes:
                if self.torrent_info_list[cross_hash].cross_seed_state == CrossSeedState.PARENT:
                    hasParent = True
                    break
            if not hasParent:
                torrent_info.cross_seed_state = CrossSeedState.ORPHAN

        # cross-seed
        if torrent_info.cross_seed_state != CrossSeedState.NONE:
            torrent_info.torrent_add_tag("_cs_all")
        else:
            torrent_info.torrent_remove_tag("_cs_all")

        if torrent_info.cross_seed_state == CrossSeedState.PARENT:
            torrent_info.torrent_add_tag("_cs_parent")
        else:
            torrent_info.torrent_remove_tag("_cs_parent")

        if torrent_info.cross_seed_state == CrossSeedState.PEER:
            torrent_info.torrent_add_tag("_cs_peer")
        else:
            torrent_info.torrent_remove_tag("_cs_peer")

        if torrent_info.cross_seed_state == CrossSeedState.ORPHAN:
            torrent_info.torrent_add_tag("_cs_orphan")
        else:
            torrent_info.torrent_remove_tag("_cs_orphan")

        # deletion
        if torrent_info.delete_state == DeleteState.READY:
            torrent_info.torrent_add_tag("_delete")
        else:
            torrent_info.torrent_remove_tag("_delete")

        if torrent_info.delete_state == DeleteState.SOFT_DELETE:
            torrent_info.torrent_add_tag("_delete_soft")
        else:
            torrent_info.torrent_remove_tag("_delete_soft")

        if torrent_info.delete_state == DeleteState.DELETE_IF_NEEDED:
            torrent_info.torrent_add_tag("_delete_if_needed")
        else:
            torrent_info.torrent_remove_tag("_delete_if_needed")

        if torrent_info.delete_state == DeleteState.KEEP_LAST:
            torrent_info.torrent_add_tag("_keep_last")
        else:
            torrent_info.torrent_remove_tag("_keep_last")

        if torrent_info.delete_state == DeleteState.AUTOBRR_DELETE:
            torrent_info.torrent_add_tag("_delete_autobrr")
        else:
            torrent_info.torrent_remove_tag("_delete_autobrr")

        if torrent_info.delete_state == DeleteState.NEVER:
            torrent_info.torrent_add_tag("_delete_never")
        else:
            torrent_info.torrent_remove_tag("_delete_never")

        if torrent_info.delete_state == DeleteState.NONE:
            torrent_info.torrent_remove_tag("_delete")
            torrent_info.torrent_remove_tag("_delete_soft")
            torrent_info.torrent_remove_tag("_delete_if_needed")
            torrent_info.torrent_remove_tag("_delete_never")
            torrent_info.torrent_remove_tag("_delete_autobrr")
            torrent_info.torrent_remove_tag("_keep_last")

    def analyze_torrent(self, torrent_info: TorrentInfo):

        # Determine if cross-seeded
        file_torrents = torrent_info.File_Dict[torrent_info.content_path]
        if len(file_torrents) > 1:
            if torrent_info.torrent_dict["downloaded"] == 0:
                torrent_info.cross_seed_state = CrossSeedState.PEER
            else:
                torrent_info.cross_seed_state = CrossSeedState.PARENT
        else:
            torrent_info.cross_seed_state = CrossSeedState.NONE

        # Add cross-seed hashes
        for torrent in file_torrents:
            torrent_info.cross_seed_hashes.append(torrent._hash)

        # Determine deletion
        if torrent_info.torrent_dict["force_start"]:
            torrent_info.delete_state = DeleteState.NEVER

        # Check if it's past the threshold or unregistered
        if torrent_info.is_unregistered:
            if torrent_info.cross_seed_state != CrossSeedState.NONE:
                torrent_info.delete_state = DeleteState.SOFT_DELETE
            else:
                torrent_info.delete_state = DeleteState.READY

        if torrent_info.delete_state == DeleteState.NONE:
            # Set tracker delete days, default to 0 if None
            tracker_delete_days = torrent_info.tracker_opts.get("delete", 0)
            if torrent_info.is_autobrr_torrent:
                tracker_delete_days = torrent_info.tracker_opts.get("autobrr_delete", self.default_autobrr_delete_days)

            # Only calculate if we have a valid completion timestamp and non-zero delete days
            if tracker_delete_days > 0 and torrent_info.torrent_dict["completion_on"] > 1000000000:
                torrent_completed = datetime.fromtimestamp(torrent_info.torrent_dict["completion_on"])
                torrent_threshold = torrent_completed + timedelta(days=tracker_delete_days)

                if datetime.now() > torrent_threshold:
                    self.handle_delete_state(torrent_info)
                    self.handle_keep_last(torrent_info)

    def handle_delete_state(self, torrent_info: TorrentInfo):

        # Not cross-seeded
        if torrent_info.cross_seed_state == CrossSeedState.NONE:
            if torrent_info.is_autobrr_torrent:
                torrent_info.delete_state = DeleteState.AUTOBRR_DELETE
            else:
                if torrent_info.tracker_name == "BTN" and torrent_info.is_season_pack:
                    torrent_info.delete_state = DeleteState.NEVER
                else:
                    torrent_info.delete_state = DeleteState.DELETE_IF_NEEDED if torrent_info.is_polite_to_seed else DeleteState.READY

        # Cross-seeded, decide based on parent's state
        if torrent_info.cross_seed_state == CrossSeedState.PARENT:
            if torrent_info.is_autobrr_torrent:
                for cross_hash in torrent_info.cross_seed_hashes:
                    self.torrent_info_list[cross_hash].delete_state = DeleteState.AUTOBRR_DELETE
            else:
                # Determine if BTN is involved in cross-seeds
                is_btn_involved = any(self.torrent_info_list[cross_hash].tracker_name == "BTN" for cross_hash in torrent_info.cross_seed_hashes)
                # Mark deletion state
                if torrent_info.is_season_pack and is_btn_involved:
                    for cross_hash in torrent_info.cross_seed_hashes:
                        self.torrent_info_list[cross_hash].delete_state = DeleteState.NEVER
                else:
                    for cross_hash in torrent_info.cross_seed_hashes:
                        self.torrent_info_list[cross_hash].delete_state = DeleteState.READY

    def handle_keep_last(self, torrent_info: TorrentInfo):
        # Preserve keep_last number of torrents for tracker, if set. Useful for bonus points.
        tracker_keep_last = torrent_info.tracker_opts.get("keep_last", 0) or 0

        if tracker_keep_last > 0:
            # Get all hashes associated with the tracker's tag, excluding torrents with "autobrr" tag and size over 10GB
            relevant_hashes = [
                h
                for h in self.torrent_tag_hashes_list.get(torrent_info.tracker_name.strip(), [])
                if not self.torrent_info_list[h].is_autobrr_torrent
                and self.torrent_info_list[h].torrent_dict.get("size", 0) <= 10 * 1024**3  # 10GB in bytes
                and self.torrent_info_list[h].cross_seed_state == CrossSeedState.NONE
            ]

            # Sort torrents by their added_on time
            sorted_items = sorted(relevant_hashes, key=lambda h: self.torrent_info_list[h].torrent_dict.get("added_on", float("inf")))

            # Get the hashes for the last `tracker_keep_last` torrents
            keep_last_hashes = sorted_items[:tracker_keep_last]

            # If the current torrent's hash is in the keep_last list, mark it as NEVER delete
            if torrent_info._hash in keep_last_hashes:
                torrent_info.delete_state = DeleteState.KEEP_LAST

    def connect_to_qb(self, server, port) -> qbittorrentapi.Client:
        try:
            if self.no_color:
                print(f"\nConnecting to: {server}")
            else:
                print(f"\nConnecting to: {Fore.GREEN}{server}{Fore.RESET}")
            qb = qbittorrentapi.Client(host=server, port=port)
            if self.no_color:
                print(f"qBittorrent: {qb.app.version}")
            else:
                print(f"qBittorrent: {Fore.GREEN}{qb.app.version}{Fore.RESET}")
            for k, v in qb.app.build_info.items():
                print(f" -- {k}: {v}")
            return qb
        except qbittorrentapi.exceptions.APIConnectionError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    def load_trackers(self, tracker_json_path):
        with open(tracker_json_path, "r") as read_file:
            return json.load(read_file)

    def qb_add_tag(self, torrent_info: TorrentInfo):

        torrent_hash = torrent_info._hash
        for tag in torrent_info.update_tags_add:
            try:
                if self.dry_run:
                    if self.no_color:
                        print(f"  [DRY RUN] Would add tag '{tag}' to torrent {torrent_hash}")
                    else:
                        print(f"  [DRY RUN] Would add tag '{Fore.GREEN}{tag}{Fore.RESET}' to torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
                else:
                    self.qb.torrents_add_tags(tag, torrent_hash)
                    if self.no_color:
                        print(f"  Adding tag '{tag}' to torrent {torrent_hash}")
                    else:
                        print(f"  Adding tag '{Fore.GREEN}{tag}{Fore.RESET}' to torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
            except:
                print(f"  Failed to set tag '{tag}' for {torrent_hash}")

    def qb_remove_tag(self, torrent_info: TorrentInfo):

        torrent_hash = torrent_info._hash
        for tag in torrent_info.update_tags_remove:
            try:
                if self.dry_run:
                    if self.no_color:
                        print(f"  [DRY RUN] Would remove tag '{tag}' from torrent {torrent_hash}")
                    else:
                        print(f"  [DRY RUN] Would remove tag '{Fore.RED}{tag}{Fore.RESET}' from torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
                else:
                    self.qb.torrents_remove_tags(tag, torrent_hash)
                    if self.no_color:
                        print(f"  Removing tag '{tag}' from torrent {torrent_hash}")
                    else:
                        print(f"  Removing tag '{Fore.RED}{tag}{Fore.RESET}' from torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
            except:
                print(f"  Failed to remove tag '{tag}' from {torrent_hash}")

    def qb_set_upload_limit(self, torrent_info: TorrentInfo):

        try:
            upload_limit = torrent_info.update_upload_limit
            torrent_hash = torrent_info._hash
            if self.dry_run:
                if self.no_color:
                    print(f"  [DRY RUN] Would set upload_limit to '{upload_limit}' for torrent {torrent_hash}")
                else:
                    print(f"  [DRY RUN] Would set upload_limit to '{Fore.GREEN}{upload_limit}{Fore.RESET}' for torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
            else:
                if self.no_color:
                    print(f"  Setting upload_limit to '{upload_limit}' for torrent {torrent_hash}")
                else:
                    print(f"  Setting upload_limit to '{Fore.GREEN}{upload_limit}{Fore.RESET}' for torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
                self.qb.torrents_set_upload_limit(upload_limit, torrent_hash)
        except:
            print(f"  Failed to set upload limit for {torrent_hash}")


print()
print(f"===========================")
print(f"| QBit-Tagger version 2.0 |")
print(f"===========================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage torrents in qBittorrent.")
    parser.add_argument("-s", "--server", default="10.0.0.50", help="Server IP of the qBittorrent instance.")
    parser.add_argument("-p", "--port", default="8080", help="Server port for the qBittorrent instance.")
    parser.add_argument("-t", "--tracker-config", default="trackers.json", help="Path to the tracker config json file.")
    parser.add_argument("-d", "--dry-run", default=False, action="store_true", help="Perform a dry run without making changes.")
    parser.add_argument("-n", "--no-color", default=False, action="store_true", help="No color in output. Useful when running in unraid via User scripts.")
    parser.add_argument("-o", "--output-hash", default=None, help="Torrent hash or hashes (comma separated) for which to print TorrentInfo.")
    parser.add_argument("-e", "--output-extended", default=False, action="store_true", help="Print extended output. Only works when -o is used.")

    args = parser.parse_args()

    manager = TorrentManager(args.server, args.port, args.dry_run, args.no_color, args.tracker_config)
    manager.update_torrents()

    if args.output_hash:
        hash_list = [h.strip() for h in args.output_hash.split(",")]  # Split and strip whitespaces
        for torrent_hash in hash_list:
            torrent_info = manager.torrent_info_list.get(torrent_hash)
            if torrent_info:  # Checks if the list is not empty
                print(torrent_info.to_str(args.output_extended))
            else:
                print(f"\nWARNING: Torrent with hash {torrent_hash} not found.\n")