import qbittorrentapi
import sys
import os
import shutil

from colorama import Fore, Back, Style, init
from tqdm import tqdm
from collections import defaultdict

from .torrentinfo import *
from . import util

class TorrentManager:

    def __init__(self, dry_run, no_color):

        # args
        self.server = util.Config_Manager.get("server")
        self.port = util.Config_Manager.get("port")
        self.dry_run = dry_run
        self.no_color = no_color

        # dict to store torrents
        self.torrent_info_list = defaultdict(list)
        self.torrent_tag_hashes_list = defaultdict(list)

        # connect to qb
        self.qb = self.connect_to_qb(self.server, self.port)

        # tracker config
        tracker_json_path = util.Config_Manager.get("tracker_config")
        if tracker_json_path:
            self.tracker_options = util.load_trackers(tracker_json_path)

    def get_torrents(self):

        # process torrents and create list of TorrentInfo objects
        print(f"\n=== Phase 1: Getting a list of torrents from qBitTorrent ===")
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

                torrent_trackers = self.qb.torrents_trackers(torrent_dict.hash)
                torrent_files = self.qb.torrents_files(torrent_dict.hash)

                # create new TorrentInfo object and add it to dict
                self.torrent_info_list[torrent_dict.hash] = TorrentInfo(torrent_dict, torrent_files, torrent_trackers, self.tracker_options)

            except Exception as e:
                print(f"Error processing torrent {torrent_dict.name} (hash: {torrent_dict.hash}): {e}")
                print(f"Exiting!")
                exit(1)  # Exit early if we can't fetch the torrents

        # store hashes per tag in a list, used for keep_last
        self.build_tag_to_hashes()

    def analyze_torrents(self):

        # process the list for cross-seeds and deletes and set torrentinfo object props accordingly
        print(f"\n=== Phase 2: Analyzing torrents ===")
        # for torrent_info in self.torrent_info_list.values():
        for torrent_info in tqdm(self.torrent_info_list.values(), desc="Processing torrents (first pass)", unit=" torrent", ncols=120):
            self.analyze_torrent(torrent_info)

        # set torrentinfo props, separate loop to make sure cross-seed orphans are set properly
        # for torrent_info in self.torrent_info_list.values():
        for torrent_info in tqdm(self.torrent_info_list.values(), desc="Processing torrents (second pass)", unit=" torrent", ncols=120):
            self.set_torrent_info(torrent_info)

    def update_torrents(self):

        i = 0
        print(f"\n=== Update torrents ===\n")
        for torrent_info in self.torrent_info_list.values():

            if torrent_info.update_state == UpdateState(0):
                continue

            i = i + 1
            if self.no_color:
                print(f"++ Updating [{torrent_info.tracker_name}] torrent {torrent_info._name} ({torrent_info._hash})")
            else:
                print(f"++ Updating [{Fore.MAGENTA}{torrent_info.tracker_name}{Fore.RESET}] torrent {Fore.YELLOW}{torrent_info._name}{Fore.RESET} ({Fore.CYAN}{torrent_info._hash}{Fore.RESET})")

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
            print(f"\nProcessed {len(self.torrent_info_list)} torrents and updated {i} torrents.")
        else:
            print(f"Processed {len(self.torrent_info_list)} torrents and updated {i} torrents.")

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

        unregistered_tag = TagNames.UNREGISTERED.value
        torrent_info.torrent_add_tag(unregistered_tag) if torrent_info.is_unregistered else torrent_info.torrent_remove_tag(unregistered_tag)

        tracker_error_tag = TagNames.TRACKER_ERROR.value
        (
            torrent_info.torrent_add_tag(tracker_error_tag)
            if torrent_info.is_tracker_error and not torrent_info.is_unregistered
            else torrent_info.torrent_remove_tag(tracker_error_tag)
        )

        rarred_tag = TagNames.RARRED.value
        torrent_info.torrent_add_tag(rarred_tag) if torrent_info.is_rarred else torrent_info.torrent_remove_tag(rarred_tag)

        season_pack_tag = TagNames.SEASON_PACK.value
        torrent_info.torrent_add_tag(season_pack_tag) if torrent_info.is_season_pack else torrent_info.torrent_remove_tag(season_pack_tag)

        throttled_tag = TagNames.THROTTLED.value
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
        if util.Config_Manager.get('options')['tag_hardlink']:
            hl_tag_add = TagNames.HARDLINK.value if torrent_info.is_hardlinked else TagNames.NO_HARDLINK.value
            hl_tag_remove = TagNames.NO_HARDLINK.value if torrent_info.is_hardlinked else TagNames.HARDLINK.value
            torrent_info.torrent_add_tag(hl_tag_add)
            torrent_info.torrent_remove_tag(hl_tag_remove)


    def update_cross_seed_tags(self, torrent_info):

        # _cs_all tag
        cs_all_tag = TagNames.CROSS_SEED_ALL.value
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
            torrent_info.delete_state = DeleteState.DELETE

        if torrent_info.delete_state == DeleteState.NONE:
            # Set tracker delete days, default to 0 if None
            tracker_delete_days = torrent_info.tracker_opts.get("delete", 0)
            if torrent_info.has_autobrr_tag:
                tracker_delete_days = torrent_info.tracker_opts.get("autobrr_delete", 0) or util.Config_Manager.get('autobrr')['default_delete_days']

            # Handle delete states for torrents past delete threshold. Incomplete torrents should have negative value for torrent_completed_since_days
            if tracker_delete_days > 0 and torrent_info.torrent_completed_since_days > tracker_delete_days:
                self.handle_delete_state(torrent_info)
                self.handle_keep_last(torrent_info)

    def handle_delete_state(self, torrent_info: TorrentInfo):

        # Not cross-seeded
        if torrent_info.cross_seed_state == CrossSeedState.NONE:
            if torrent_info.has_autobrr_tag and torrent_info.is_private:
                torrent_info.delete_state = DeleteState.AUTOBRR_DELETE
            else:
                if torrent_info.tracker_name == "BTN" and torrent_info.is_season_pack:
                    torrent_info.delete_state = DeleteState.NEVER
                elif torrent_info.has_hardlink_tag and torrent_info.is_private:
                    torrent_info.delete_state = DeleteState.HARDLINK_DELETE
                else:
                    torrent_info.delete_state = DeleteState.DELETE_IF_NEEDED if torrent_info.is_polite_to_seed else DeleteState.READY

        # Cross-seeded, decide based on parent's state
        if torrent_info.cross_seed_state == CrossSeedState.PARENT:
            if torrent_info.has_autobrr_tag and torrent_info.is_private:
                for cross_hash in torrent_info.cross_seed_hashes:
                    self.torrent_info_list[cross_hash].delete_state = DeleteState.AUTOBRR_DELETE
            else:
                # Determine if BTN is involved in cross-seeds
                is_btn_involved = any(self.torrent_info_list[cross_hash].tracker_name == "BTN" for cross_hash in torrent_info.cross_seed_hashes)

                if torrent_info.is_season_pack and is_btn_involved:
                    for cross_hash in torrent_info.cross_seed_hashes:
                        self.torrent_info_list[cross_hash].delete_state = DeleteState.NEVER
                elif torrent_info.has_hardlink_tag and torrent_info.is_private:
                    for cross_hash in torrent_info.cross_seed_hashes:
                        self.torrent_info_list[cross_hash].delete_state = DeleteState.HARDLINK_DELETE
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
                if self.torrent_info_list[h].cross_seed_state == CrossSeedState.NONE
                and not self.torrent_info_list[h].has_autobrr_tag
                and self.torrent_info_list[h].torrent_dict.get("size", 0) <= 10 * 1024**3  # 10GB in bytes
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
            # for k, v in qb.app.build_info.items():
            #     print(f" -- {k}: {v}")
            return qb
        except qbittorrentapi.exceptions.APIConnectionError as e:
            print(f"ERROR: {e}")
            sys.exit(1)



    def qb_add_tag(self, torrent_info: TorrentInfo):

        torrent_hash = torrent_info._hash
        for tag in torrent_info.update_tags_add:
            try:
                if self.dry_run:
                    print(f"  [DRY RUN] Will add tag '{tag if self.no_color else f'{Fore.GREEN}{tag}{Fore.RESET}'}' to torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
                else:
                    self.qb.torrents_add_tags(tag, torrent_hash)
                    print(f"  Adding tag '{tag if self.no_color else f'{Fore.GREEN}{tag}{Fore.RESET}'}' to torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
            except:
                print(f"  Failed to set tag '{tag}' for {torrent_hash}")

    def qb_remove_category(self, torrent_info: TorrentInfo):

        category = torrent_info.torrent_dict["category"]
        torrent_hash = torrent_info._hash
        try:
            if self.dry_run:
                print(f"  [DRY RUN] Will remove category '{category if self.no_color else f'{Fore.GREEN}{category}{Fore.RESET}'}' from torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
            else:
                print(f"  Removing category '{category if self.no_color else f'{Fore.GREEN}{category}{Fore.RESET}'}' from torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
                self.qb.torrents_set_category("", torrent_hash)
        except:
            print(f"  Failed to removing category on torrent for {torrent_hash}")

    def qb_remove_tag(self, torrent_info: TorrentInfo):

        torrent_hash = torrent_info._hash
        for tag in torrent_info.update_tags_remove:
            try:
                if self.dry_run:
                    print(f"  [DRY RUN] Will remove tag '{tag if self.no_color else f'{Fore.RED}{tag}{Fore.RESET}'}' from torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
                else:
                    print(f"  Removing tag '{tag if self.no_color else f'{Fore.RED}{tag}{Fore.RESET}'}' from torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
                    self.qb.torrents_remove_tags(tag, torrent_hash)
            except:
                print(f"  Failed to remove tag '{tag}' from {torrent_hash}")

    def qb_set_upload_limit(self, torrent_info: TorrentInfo):

        try:
            upload_limit = torrent_info.update_upload_limit
            torrent_hash = torrent_info._hash
            if self.dry_run:
                print(f"  [DRY RUN] Will set upload_limit to '{upload_limit if self.no_color else f'{Fore.GREEN}{upload_limit}{Fore.RESET}'}' for torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
            else:
                print(f"  Setting upload_limit to '{upload_limit if self.no_color else f'{Fore.GREEN}{upload_limit}{Fore.RESET}'}' for torrent {torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}")
                self.qb.torrents_set_upload_limit(upload_limit, torrent_hash)
        except:
            print(f"  Failed to set upload limit for {torrent_hash}")

    def move_orphaned(self):
        print("\n=== Find and move orphaned files ===")

        try:
            config_orphaned = util.Config_Manager.get('orphaned_files')
            if not config_orphaned['move_orphaned']:
                print(f"\nSkipping because move_orphaned is false.")
                return

            orphan_dest = config_orphaned['orphan_destination']
            orphan_dest = util.format_path(orphan_dest)
            excluded_save_paths = config_orphaned['excluded_save_paths']
            move_orphaned_after_days = config_orphaned['move_orphaned_after_days']

        except Exception as e:
            print(f"Error: Failed to retrieve orphaned_files config: {e}")
            return

        ignore_files = {".ds_store", "thumbs.db"}  # Set of files to ignore
        summary = ""
        for save_path in TorrentInfo.Unique_SavePaths:

            if excluded_save_paths and save_path in excluded_save_paths:
                continue

            print(f"\nScanning {save_path}")
            moved = 0
            total_size = 0
            try:
                for root, _, filenames in os.walk(save_path):
                    # Filter out ignored files
                    filenames = [f for f in filenames if f.lower() not in ignore_files]

                    for file in filenames:
                        full_path = os.path.join(root, file)
                        root2 = util.format_path(root)

                        # Check if the file is orphaned
                        if full_path not in TorrentInfo.Unique_Files:
                            dest_path = full_path.replace(save_path, orphan_dest)
                            dest_path_parent = dest_path.rsplit(os.sep, 1)[0]

                            if util.file_modified_older_than(full_path, move_orphaned_after_days):
                                moved += 1
                                file_size = os.path.getsize(full_path)
                                total_size += file_size
                                if self.dry_run:
                                    print(f"-- [DRY RUN] Will move {full_path if self.no_color else f'{Fore.GREEN}{root2}{Fore.YELLOW}{file}{Fore.RESET}'} [{util.format_bytes(file_size)}] TO {dest_path_parent if self.no_color else f'{Fore.CYAN}{dest_path_parent}{Fore.RESET}'}")
                                else:
                                    print(f"-- MOVING {full_path if self.no_color else f'{Fore.GREEN}{root2}{Fore.YELLOW}{file}{Fore.RESET}'} [{util.format_bytes(file_size)}] TO {dest_path_parent if self.no_color else f'{Fore.CYAN}{dest_path_parent}{Fore.RESET}'}")
                                    try:
                                        # Create destination path if it doesn't exist
                                        os.makedirs(dest_path_parent, exist_ok=True)

                                        # remove if exists at destination
                                        if os.path.exists(dest_path):
                                            os.remove(dest_path)

                                        # move file
                                        shutil.move(full_path, dest_path_parent)

                                    except (OSError, shutil.Error) as move_error:
                                        print(f"   Error moving {full_path}: {move_error}")

                # Remove empty directories after processing
                self.remove_empty_dirs(save_path)
                summary += f"\n\nSave Path: *{save_path}* \nMoved {moved} files **[{util.format_bytes(total_size)}]**."
                print(f"-- {'[DRY RUN] Will move' if self.dry_run else 'Moved'} {moved} files with total size [{util.format_bytes(total_size)}].")

            except Exception as e:
                print(f"-- Error scanning {save_path}: {e}")

        util.Discord_Summary.append(("Move orphaned files", summary))


    def remove_orphaned(self):

        print(f"\n=== Remove orphaned files ===\n")
        try:
            config_orphaned = util.Config_Manager.get('orphaned_files')

            # Get config values
            directory = config_orphaned['orphan_destination']
            directory = util.format_path(directory)
            remove_age_days = config_orphaned['remove_orphaned_age_days']
            if remove_age_days < 0:
                print(f"Skipping because remove_orphaned_age_days is set to {remove_age_days}.\n")
                return

        except Exception as e:
            print(f"Error: Failed to retrieve or validate 'orphaned_files': {e}\n")
            return

        try:
            print(f"Removing files older than {remove_age_days} days in {directory}")

            # Traverse through the directory and process files
            removed = 0
            total_size = 0
            for root, _, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    root_print = util.format_path(root)

                    try:
                        if util.file_modified_older_than(file_path, remove_age_days):
                            removed += 1
                            file_size = os.path.getsize(file_path)
                            total_size += file_size
                            if self.dry_run:
                                print(f"-- [DRY RUN] Will remove {file_path if self.no_color else f'{Fore.GREEN}{root_print}{Fore.YELLOW}{file}{Fore.RESET}'} [{util.format_bytes(file_size)}]")
                            else:
                                print(f"-- Removing {file_path if self.no_color else f'{Fore.GREEN}{root_print}{Fore.YELLOW}{file}{Fore.RESET}'} [{util.format_bytes(file_size)}]")
                                os.remove(file_path)

                    except OSError as e:
                        print(f"-- Error accessing file {file_path}: {e}")
                    except Exception as e:
                        print(f"-- Error processing file {file_path}: {e}")

            # Remove empty directories after processing
            self.remove_empty_dirs(directory)
            util.Discord_Summary.append(("Remove orphaned files", f"Orphan Destination: *{directory}* \nRemoved {removed} files **[{util.format_bytes(total_size)}]**."))
            print(f"-- {'[DRY RUN] Will remove' if self.dry_run else 'Removed'} {removed} files with total size [{util.format_bytes(total_size)}].")
        except Exception as e:
            print(f"-- Error traversing directory {directory}: {e}")

    def remove_empty_dirs(self, directory):
        dirs_removed = False  # Flag to track if any directory was removed during the current pass

        try:
            # Walk through directory tree from bottom-up to ensure empty directories are removed
            for dirpath, dirnames, filenames in os.walk(directory, topdown=False):
                # If the directory is empty (contains no subdirectories or files)
                if not dirnames and not filenames:
                    try:
                        if self.dry_run:
                            print(f"-- [DRY RUN] Will remove empty directory {dirpath if self.no_color else f'{Fore.YELLOW}{dirpath}{Fore.RESET}'}")
                        else:
                            print(f"-- Removing empty directory {dirpath if self.no_color else f'{Fore.YELLOW}{dirpath}{Fore.RESET}'}")
                            os.rmdir(dirpath)
                            if not os.path.exists(dirpath):  # Ensure directory was actually removed
                                dirs_removed = True  # Set flag to True only when directory is actually removed
                    except OSError as e:
                        print(f"-- Error removing directory {dirpath}: {e}")
                    except Exception as e:
                        print(f"-- Unexpected error while removing directory {dirpath}: {e}")
        except Exception as e:
            print(f"-- Error walking through directory {directory}: {e}")

        # Recursively call the function if directories were removed during this pass
        if dirs_removed:
            self.remove_empty_dirs(directory)


    def auto_delete_torrents(self):

        print("\n=== Auto-delete torrents ===\n")

        auto_delete_config = util.Config_Manager.get('auto_delete_torrents')
        if not auto_delete_config['enabled']:
            print("Auto-delete is not enabled. Skipping.")
            return

        auto_delete_tags = auto_delete_config['auto_delete_tags']
        if not auto_delete_tags:
            print("auto-delete-tags is not defined. Skipping.")
            return

        total_size = 0
        removed = 0
        backup_dest = auto_delete_config['backup_destination']
        if not backup_dest:
            print(f"backup_destination is not specified for auto-delete. Skipping.")
            return

        if not os.path.exists(backup_dest):
            os.makedirs(backup_dest)

        for torrent_info in self.torrent_info_list.values():
            matching_tag = next((tag for tag in auto_delete_tags if tag in torrent_info.current_tags), None)
            if matching_tag and torrent_info.torrent_completed_since_days >= auto_delete_config['auto_delete_age_days']:
                torrent_hash = torrent_info._hash
                torrent_name = torrent_info._name
                torrent_size = torrent_info.torrent_dict['size']
                total_size += torrent_size
                formatted_size = util.format_bytes(torrent_size)
                removed += 1
                if self.dry_run:
                    print(f"-- [DRY RUN] Will remove [{matching_tag if self.no_color else f'{Fore.GREEN}{matching_tag}{Fore.RESET}'}] '{torrent_name if self.no_color else f'{Fore.YELLOW}{torrent_name}{Fore.RESET}'}' ({torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}) torrent with size '{formatted_size if self.no_color else f'{Fore.GREEN}{formatted_size}{Fore.RESET}'}'")
                else:
                    # remove torrents with delete_files set to False, as orphan cleanup will take care of them.
                    print(f"-- Removing [{matching_tag if self.no_color else f'{Fore.GREEN}{matching_tag}{Fore.RESET}'}] '{torrent_name if self.no_color else f'{Fore.YELLOW}{torrent_name}{Fore.RESET}'}' ({torrent_hash if self.no_color else f'{Fore.CYAN}{torrent_hash}{Fore.RESET}'}) torrent with size '{formatted_size if self.no_color else f'{Fore.GREEN}{formatted_size}{Fore.RESET}'}'")
                    if backup_dest:
                        torrent_ex = self.qb.torrents_export(torrent_hash)
                        torrent_ex_path = os.path.join(backup_dest, f"{torrent_hash}.torrent")
                        with open(torrent_ex_path, 'wb') as f:
                            f.write(torrent_ex)
                        if os.path.exists(torrent_ex_path):
                            self.qb.torrents_delete(delete_files=False, torrent_hashes=torrent_hash)

        print()
        util.Discord_Summary.append(("Auto-delete torrents", f"auto_delete_tags: *{auto_delete_tags}* \nRemoved {removed} torrents **[{util.format_bytes(total_size)}]**."))
        if self.dry_run:
            print(f"[DRY RUN] Total size of removed torrents [{removed}] with '{auto_delete_tags if self.no_color else f'{Fore.GREEN}{auto_delete_tags}{Fore.RESET}'}' tag: {util.format_bytes(total_size)}")
        else:
            print(f"Total size of removed torrents [{removed}] with '{auto_delete_tags if self.no_color else f'{Fore.GREEN}{auto_delete_tags}{Fore.RESET}'}' tag: {util.format_bytes(total_size)}")
