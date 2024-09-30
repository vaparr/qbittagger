import qbittorrentapi
import json
import sys
import os
from datetime import datetime, timedelta
from colorama import Fore, Back, Style, init
from tqdm import tqdm
from collections import defaultdict

from .torrentinfo import *

class TorrentManager:

    def __init__(self, config_manager, dry_run, no_color, tracker_json_path):

        # args
        self.server = config_manager.get('server')
        self.port = config_manager.get('port')
        self.dry_run = dry_run
        self.no_color = no_color

        # dict to store torrents
        self.torrent_info_list = defaultdict(list)
        self.torrent_tag_hashes_list = defaultdict(list)

        # tracker config
        self.tracker_options = self.load_trackers(tracker_json_path)

        # config values
        self.default_autobrr_delete_days = config_manager.get('default_autobrr_delete_days')  # days
        self.remove_category_for_bad_torrents = config_manager.get('remove_category_for_bad_torrents')

        # connect to qb
        self.qb = self.connect_to_qb(self.server, self.port)

        # process torrents and create list of TorrentInfo objects
        print(f"\n=== Phase 1: Getting a list of torrents from qBitTorrent... ")
        qb_torrents = self.qb.torrents_info()
        # for torrent_dict in qb_torrents:
        for torrent_dict in tqdm(qb_torrents, desc="Processing torrents", unit=" torrent", ncols=120):

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

            if UpdateState.CATEGORY_REMOVE in torrent_info.update_state:
                self.qb_remove_category(torrent_info)

        if i > 0:
            print(f"\nProcessed {len(self.torrent_info_list)} torrents and updated {i} torrents.\n")
        else:
            print(f"Processed {len(self.torrent_info_list)} torrents and updated {i} torrents.\n")

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

        unregistered_tag = "_unregistered"
        torrent_info.torrent_add_tag(unregistered_tag) if torrent_info.is_unregistered else torrent_info.torrent_remove_tag(unregistered_tag)

        tracker_error_tag = "_tracker_error"
        torrent_info.torrent_add_tag(tracker_error_tag) if torrent_info.is_tracker_error and not torrent_info.is_unregistered else torrent_info.torrent_remove_tag(tracker_error_tag)

        rarred_tag = "_rarred"
        torrent_info.torrent_add_tag(rarred_tag) if torrent_info.is_rarred else torrent_info.torrent_remove_tag(rarred_tag)

        season_pack_tag = "_season_pack"
        torrent_info.torrent_add_tag(season_pack_tag) if torrent_info.is_season_pack else torrent_info.torrent_remove_tag(season_pack_tag)

        throttled_tag = "_throttled"
        torrent_info.torrent_add_tag(throttled_tag) if torrent_info.torrent_dict["up_limit"] > 0 else torrent_info.torrent_remove_tag(throttled_tag)

        # Cross-seeded, orphaned peers
        if torrent_info.cross_seed_state == CrossSeedState.PEER:
            hasParent = False
            for cross_hash in torrent_info.cross_seed_hashes:
                if self.torrent_info_list[cross_hash].cross_seed_state == CrossSeedState.PARENT:
                    hasParent = True
                    break
            if not hasParent:
                torrent_info.cross_seed_state = CrossSeedState.ORPHAN

        # update cross-seed tags
        self.update_cross_seed_tags(torrent_info)

        # update delete tags
        if not torrent_info.torrent_trackers_filtered:
            torrent_info.delete_state = DeleteState.DELETE
            torrent_info.torrent_remove_category(self.remove_category_for_bad_torrents)

        # Remove category if we are in an error state. Allows sonarr and radarr to give up.
        if torrent_info.is_tracker_error or torrent_info.is_unregistered:
            torrent_info.torrent_remove_category(self.remove_category_for_bad_torrents)

        self.update_delete_tags(torrent_info)

    def update_cross_seed_tags(self, torrent_info):

        # _cs_all tag
        cs_all_tag = "_cs_all"
        torrent_info.torrent_add_tag(cs_all_tag) if torrent_info.cross_seed_state != CrossSeedState.NONE else torrent_info.torrent_remove_tag(cs_all_tag)

        # First, check for NONE and remove all cross-seed tags
        if torrent_info.cross_seed_state == CrossSeedState.NONE:
            for state in CrossSeedState:
                if state != CrossSeedState.NONE:  # Remove all other tags if state is NONE
                    torrent_info.torrent_remove_tag(state.value)
            return  # Exit after handling NONE

        # For other states, add the corresponding tag
        for state in CrossSeedState:
            if torrent_info.cross_seed_state == state:
                torrent_info.torrent_add_tag(state.value)
            else:
                torrent_info.torrent_remove_tag(state.value)

    def update_delete_tags(self, torrent_info):

        # Special case for NONE
        if torrent_info.delete_state == DeleteState.NONE:
            for state in DeleteState:
                if state != DeleteState.NONE:
                    torrent_info.torrent_remove_tag(state.value)
            return  # Exit after handling NONE

        # For other states, add the corresponding tag
        for state in DeleteState:
            if torrent_info.delete_state == state:
                torrent_info.torrent_add_tag(state.value)
            else:
                torrent_info.torrent_remove_tag(state.value)

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

        # Check if it's unregistered
        if torrent_info.is_unregistered and torrent_info.cross_seed_state == CrossSeedState.NONE:
            torrent_info.delete_state = DeleteState.READY

        if torrent_info.delete_state == DeleteState.NONE:
            # Set tracker delete days, default to 0 if None
            tracker_delete_days = torrent_info.tracker_opts.get("delete", 0)
            if torrent_info.is_autobrr_torrent:
                tracker_delete_days = torrent_info.tracker_opts.get("autobrr_delete", 0) or self.default_autobrr_delete_days

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
                print(f"\nConnecting to: {server}:{port}")
            else:
                print(f"\nConnecting to: {Fore.GREEN}{server}:{port}{Fore.RESET}")
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

        if not os.path.exists(tracker_json_path):
            print(f"ERROR: Unable to find '{tracker_json_path}'")
            exit(2)

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

    def qb_remove_category(self, torrent_info: TorrentInfo):
        if self.remove_category_for_bad_torrents == True:
            category = torrent_info.torrent_dict["category"]
            torrent_hash = torrent_info._hash
            try:
                if self.dry_run:
                    if self.no_color:
                        print(f"  [DRY RUN] Would remove category '{category}' on torrent {torrent_hash}")
                    else:
                        print(f"  [DRY RUN] Would remove category '{Fore.GREEN}{category}{Fore.RESET}' on torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
                else:
                    self.qb.torrents_set_category("", torrent_hash)
                    if self.no_color:
                        print(f"  Removing category '{category}' on torrent {torrent_hash}")
                    else:
                        print(f"  Removing category '{Fore.GREEN}{category}{Fore.RESET}' on torrent {Fore.CYAN}{torrent_hash}{Fore.RESET}")
            except:
                print(f"  Failed to removing category on torrent for {torrent_hash}")

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