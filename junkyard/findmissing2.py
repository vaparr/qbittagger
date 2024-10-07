import qbittorrentapi
import os

# Initialize qBittorrent client
qb = qbittorrentapi.Client(host='192.168.1.62', port=8080)

# Fetch torrents info
torrents = qb.torrents_info()

# Set to store unique file paths for faster lookups
unique_files = set()
save_paths = {}
hash_to_filenames = {}

def is_hard_link(filename):
    return os.stat(filename).st_nlink > 1

# Process each torrent
for torrent in torrents:
    files = qb.torrents_files(torrent['hash'])
    save_path = torrent['save_path'].replace("/plexmedia/", "/mnt/user/plexmedia/")

    # Ensure save_path ends with a '/'
    if not save_path.endswith("/"):
        save_path += "/"
    
    # Store the save path and corresponding filenames
    save_paths[save_path] = []
    for file in files:
        filename = os.path.join(save_path, file['name'])
        unique_files.add(filename)
        hash_to_filenames.setdefault(torrent['hash'], []).append(filename)
        save_paths[save_path].append(filename)

hardlink_count = 0
hardlink_info = []

# Walk through the directory to find files
for path in save_paths.keys():
    print(f"Scanning {path}")
    for root, _, filenames in os.walk(path):
        for file in filenames:
            full_path = os.path.join(root, file)

            # Check for hard links
            if is_hard_link(full_path):
                hardlink_count += 1
                # Find the corresponding hash for this file
                for torrent_hash, filenames in hash_to_filenames.items():
                    if full_path in filenames:
                        hardlink_info.append((torrent_hash, full_path))
                        break
                
            # Print paths not in the set
            if full_path not in unique_files:
                print(full_path)

# Print out hard link information
for torrent_hash, linked_file in hardlink_info:
    print(f"Hard link detected for file '{linked_file}' in torrent with hash '{torrent_hash}'")

print(f"Total hard-linked files: {hardlink_count}")