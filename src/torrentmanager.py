import qbittorrentapi
import sys
import os
import shutil
import time

from datetime import datetime, timedelta
from colorama import Fore, Back, Style, init
from tqdm import tqdm
from collections import defaultdict

from .torrentinfo import *
from . import util

class TorrentManager:

    Config_Manager = None

    def __init__(self, dry_run, no_color, tracker_json_path):

        # args
        self.server = TorrentManager.Config_Manager.get("server")
        self.port = TorrentManager.Config_Manager.get("port")
        self.dry_run = dry_run
        self.no_color = no_color

        # dict to store torrents
        self.torrent_info_list = defaultdict(list)
        self.torrent_tag_hashes_list = defaultdict(list)

        # tracker config
        self.tracker_options = util.load_trackers(tracker_json_path)

        # connect to qb
        self.qb = self.connect_to_qb(self.server, self.port)

        # process torrents and create list of TorrentInfo objects
        print(f"\n=== Phase 1: Getting a list of torrents from qBitTorrent... ")
        try:
            qb_torrents = self.qb.torrents_info()
        except Exception as e:
            print(f"ERROR! Failed to get torrent list from qBitTorrent: {e}")
            print(f"Exiting!")
            exit(1)  # Exit early if we can't fetch the torrents

        # for torrent_dict in qb_torrents:
        for torrent_dict in tqdm(qb_torrents, desc="Processing torrents", unit=" torrent", ncols=120):

            # get additional torrent info
            try:
                torrent_files = self.qb.torrents_files(torrent_dict.hash)
                torrent_trackers = self.qb.torrents_trackers(torrent_dict.hash)

                # create new TorrentInfo object and add it to dict
                self.torrent_info_list[torrent_dict.hash] = TorrentInfo(torrent_dict, torrent_files, torrent_trackers, self.tracker_options)

            except Exception as e:
                print(f"Error processing torrent {torrent_dict.name} (hash: {torrent_dict.hash}): {e}")
                print(f"Exiting!")
                exit(1)  # Exit early if we can't fetch the torrents

        # store hashes per tag in a list, used for keep_last
        self.build_tag_to_hashes()

        # process the list for cross-seeds and deletes and set torrentinfo object props accordingly
        print(f"\n=== Phase 2: Analyzing torrents... ")
        for torrent_info in self.torrent_info_list.values():
            self.analyze_torrent(torrent_info)

        # set torrentinfo props, separate loop to make sure cross-seed orphans are set properly
        for torrent_info in self.torrent_info_list.values():
            self.set_torrent_info(torrent_info)

    def update_torrents(self):

        i = 0
        print(f"\n=== Phase 3: Updating torrents\n")
        for torrent_info in self.torrent_info_list.values():

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
            # Add the torrent hash to the corresponding tag in the defaultdict
            for tag in torrent_info.current_tags:
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
        (
            torrent_info.torrent_add_tag(tracker_error_tag)
            if torrent_info.is_tracker_error and not torrent_info.is_unregistered
            else torrent_info.torrent_remove_tag(tracker_error_tag)
        )

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
            torrent_info.torrent_remove_category()
        self.update_delete_tags(torrent_info)

        # Remove category if we are in an error state. Allows sonarr and radarr to give up.
        if torrent_info.is_tracker_error or torrent_info.is_unregistered:
            torrent_info.torrent_remove_category()

        # hardlink
        if TorrentManager.Config_Manager.get('tag_hardlink'):
            hl_tag_add = "_hardlink" if torrent_info.is_hardlinked else "_no_hardlink"
            hl_tag_remove = "_no_hardlink" if torrent_info.is_hardlinked else "_hardlink"
            torrent_info.torrent_add_tag(hl_tag_add)
            torrent_info.torrent_remove_tag(hl_tag_remove)


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
        if torrent_info.torrent_dict['amount_left'] > 0:
            torrent_info.cross_seed_state = CrossSeedState.NONE
        else:
            file_torrents = TorrentInfo.ContentPath_Dict[torrent_info.content_path]
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
                tracker_delete_days = torrent_info.tracker_opts.get("autobrr_delete", 0) or TorrentManager.Config_Manager.get('default_autobrr_delete_days')

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

    def move_orphaned(self):
        print("=== Finding orphaned files")

        try:
            # Get orphaned destination path and format it
            orphan_dest = TorrentManager.Config_Manager.get('orphaned_destination')
            orphan_dest = util.format_path(orphan_dest)
        except KeyError as e:
            print(f"Error: Missing config for orphaned destination: {e}")
            return
        except Exception as e:
            print(f"Error: Failed to format orphaned destination: {e}")
            return

        ignore_files = {".ds_store", "thumbs.db"}  # Set of files to ignore

        for save_path in TorrentInfo.Unique_SavePaths:
            print(f"\nScanning {save_path}")

            try:
                for root, _, filenames in os.walk(save_path):
                    # Filter out ignored files
                    filenames = [f for f in filenames if f.lower() not in ignore_files]

                    for file in filenames:
                        full_path = os.path.join(root, file)

                        # Check if the file is orphaned
                        if full_path not in TorrentInfo.Unique_Files:
                            dest_path = full_path.replace(save_path, orphan_dest)
                            dest_path_parent = dest_path.rsplit(os.sep, 1)[0]

                            # Dry-run behavior vs actual move
                            if not self.dry_run:
                                try:
                                    print(f"  -- MOVING: {full_path} TO: {dest_path_parent}")

                                    # Create destination path if it doesn't exist
                                    os.makedirs(dest_path_parent, exist_ok=True)

                                    # remove if exists at destination
                                    if os.path.exists(dest_path):
                                        os.remove(dest_path)

                                    # move file
                                    shutil.move(full_path, dest_path_parent)
                                except (OSError, shutil.Error) as move_error:
                                    print(f"     Error moving {full_path}: {move_error}")
                            else:
                                print(f"  -- Will move: {full_path} TO: {dest_path_parent}")

                # Remove empty directories after processing
                self.remove_empty_dirs(save_path)

            except Exception as e:
                print(f"  -- Error scanning {save_path}: {e}")


    def remove_orphaned(self):

        try:
            # Get config value for file age threshold and validate
            remove_orphaned_age_days = TorrentManager.Config_Manager.get('remove_orphaned_age_days')
            if remove_orphaned_age_days < 0:
                return
        except KeyError as e:
            print(f"Error: Missing config for 'remove_orphaned_age_days': {e}")
            return
        except Exception as e:
            print(f"Error: Failed to retrieve or validate 'remove_orphaned_age_days': {e}")
            return

        try:
            # Calculate time threshold for orphan removal
            current_time = time.time()
            days_in_seconds = remove_orphaned_age_days * 24 * 60 * 60
            directory = TorrentManager.Config_Manager.get('orphaned_destination')
            directory = util.format_path(directory)
            print(f"\n=== Removing files older than {remove_orphaned_age_days} days in {directory} ===\n")
        except KeyError as e:
            print(f"Error: Missing config for 'orphaned_destination': {e}")
            return
        except Exception as e:
            print(f"Error: Failed to format 'orphaned_destination': {e}")
            return

        try:
            # Traverse through the directory and process files
            for root, _, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)

                    try:
                        # Get the last modified time of the file
                        file_mtime = os.path.getmtime(file_path)

                        # Calculate file age
                        file_age = current_time - file_mtime

                        # If file is older than the threshold
                        if file_age > days_in_seconds:
                            if self.dry_run:
                                print(f"  -- Will remove: {file_path}")
                            else:
                                print(f"  -- REMOVING: {file_path}")
                                os.remove(file_path)

                    except OSError as e:
                        print(f"  -- Error accessing file {file_path}: {e}")
                    except Exception as e:
                        print(f"  -- Error processing file {file_path}: {e}")

            # Remove empty directories after processing
            self.remove_empty_dirs(directory)
            print()

        except Exception as e:
            print(f"  -- Error traversing directory {directory}: {e}")

    def remove_empty_dirs(self, directory):
        try:
            # Walk through directory tree from bottom-up to ensure empty directories are removed
            for dirpath, dirnames, filenames in os.walk(directory, topdown=False):
                # If the directory is empty (contains no subdirectories or files)
                if not dirnames and not filenames:
                    try:
                        if self.dry_run:
                            print(f"  -- Will remove empty directory: {dirpath}")
                        else:
                            print(f"  -- REMOVING empty directory: {dirpath}")
                            os.rmdir(dirpath)
                    except OSError as e:
                        print(f"  -- Error removing directory {dirpath}: {e}")
                    except Exception as e:
                        print(f"  -- Unexpected error while removing directory {dirpath}: {e}")
        except Exception as e:
            print(f"  -- Error walking through directory {directory}: {e}")
