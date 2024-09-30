import re
from enum import Enum, Flag, auto
from collections import defaultdict
from datetime import datetime


class UpdateState(Flag):
    TAG_ADD = auto()
    TAG_REMOVE = auto()
    UPLOAD_LIMIT = auto()
    CATEGORY_REMOVE = auto()


class CrossSeedState(Enum):
    NONE = "_cs_none"
    PARENT = "_cs_parent"
    PEER = "_cs_peer"
    ORPHAN = "_cs_orphan"


class DeleteState(Enum):
    NONE = "_delete_none"
    DELETE = "_delete"
    READY = "_delete_ready"
    DELETE_IF_NEEDED = "_delete_if_needed"
    KEEP_LAST = "_keep_last"
    AUTOBRR_DELETE = "_delete_autobrr"
    NEVER = "_delete_never"


class TorrentInfo:

    # static variable
    File_Dict = defaultdict(list)

    def __init__(self, torrent_dict, torrent_files, torrent_trackers, tracker_options):

        # torrent info
        self.torrent_dict = torrent_dict
        self.torrent_files = torrent_files
        self.torrent_trackers = torrent_trackers
        self.torrent_trackers_filtered = list(filter(lambda tracker: tracker["tier"] >= 0, torrent_trackers))

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
        self.is_multi_file = len(self.torrent_files) > 1

        # Season pack?
        self.is_season_pack = False
        if self.is_multi_file:
            self.is_season_pack = self.check_season_pack(self._name)

        # How many seeders? It's polite to seed if there's less seeders than polite value in config.
        politeness = self.tracker_opts.get("polite", 0) if self.tracker_opts is not None else 0
        self.is_polite_to_seed = (self.torrent_dict["num_complete"] < politeness) if politeness > 0 else False

        # tracker error?
        self.is_tracker_error = all(tracker.status == 4 for tracker in self.torrent_trackers_filtered)

    def check_season_pack(self, torrent_name: str) -> bool:
        season_pack_patterns = [
            r"S\d{1,2}[^E]",  # Match season like "S01", "S01-S02", without episode
            r"Season \d+",  # Match "Season 1", "Season 2"
            r"Series \d+",  # Match "Series 1", "Series 2"
            r"S\d{1,2}\s?$"  # Match patterns like "S05" at the end with optional spaces
            r"Complete",  # Match "Complete" in the name
        ]
        episode_pattern = r"S\d{2}\.?E\d{2}"  # Match episodes like "S01E01", "S02E03"

        if re.search(episode_pattern, torrent_name, re.IGNORECASE):
            return False
        if any(re.search(pattern, torrent_name, re.IGNORECASE) for pattern in season_pack_patterns):
            return True

        # print(self._hash)
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

    def torrent_remove_category(self, remove_category_for_bad_torrents):

        if not remove_category_for_bad_torrents:
            return

        if (self.torrent_dict["category"]) != "" and (self.torrent_dict["category"]) != "autobrr":
            self.update_state |= UpdateState.CATEGORY_REMOVE

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
        excluded_attrs = {"torrent_dict", "torrent_files", "torrent_trackers", "torrent_trackers_filtered"}

        # Retrieve all instance attributes and exclude the specified ones
        attrs = {key: value for key, value in vars(self).items() if key not in excluded_attrs}

        # Sort the attributes by key name and prepare the formatted output with both key and value
        str_attrs = "\n".join([f"    {key} = {value}" for key, value in sorted(attrs.items())])

        # Formatting
        str_torrent_dict = str(self.torrent_dict).replace("TorrentDictionary({", "TorrentDictionary({\n        ").replace(", '", ", \n        '").replace("})", "\n      }),")
        str_torrent_trackers = str(self.torrent_trackers_filtered).replace("Tracker({", "\n        Tracker({").replace("})]", "})\n      ],")
        str_torrent_files = str(self.torrent_files).replace("TorrentFile({", "\n        TorrentFile({").replace("})]", "})\n      ],")

        # Combine the dynamically generated attributes and the formatted torrent_dict
        if include_extended:
            formatted_str = f"    torrent_trackers={str_torrent_trackers}\n    torrent_files={str_torrent_files}\n    torrent_dict={str_torrent_dict}\n{str_attrs}"

            # Redaction
            magnet_reg = r"'magnet_uri': 'magnet:\?[^']+'"
            tracker_reg = r"('(?:tracker|url)': 'https?:\/\/[^?]+)(\?)"
            formatted_str = re.sub(magnet_reg, "'magnet_uri': '<redacted>'", formatted_str)
            formatted_str = re.sub(tracker_reg + r".*", r"\1?<redacted>'", formatted_str)
        else:
            formatted_str = f"{str_attrs}"

        return f"\nTorrentInfo(\n{formatted_str}\n)"
