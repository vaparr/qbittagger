import qbittorrentapi
import os
from concurrent.futures import ThreadPoolExecutor

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
    save_paths[save_path] = set()
    for file in files:
        filename = os.path.join(save_path, file['name'])
        unique_files.add(filename)
        hash_to_filenames.setdefault(torrent['hash'], set()).add(filename)
        save_paths[save_path].add(filename)

hardlink_count = 0
hardlink_hashes = set()
missing_files = set()

def check_hard_link(full_path, hash_to_filenames):
    """Check if a file is a hard link and return associated torrent hash if it is."""
    if is_hard_link(full_path):
        for torrent_hash, filenames in hash_to_filenames.items():
            if full_path in filenames:
                return torrent_hash  # Return only the hash
    return None

# Walk through the directory to find files
with ThreadPoolExecutor() as executor:
    futures = []
    for path in save_paths.keys():
        print(f"Scanning {path}")
        for root, _, filenames in os.walk(path):
            for file in filenames:
                full_path = os.path.join(root, file)
                futures.append(executor.submit(check_hard_link, full_path, hash_to_filenames))

    # Check for missing files in the unique_files set
    for path in save_paths.keys():
        for root, _, filenames in os.walk(path):
            for file in filenames:
                full_path = os.path.join(root, file)
                if full_path not in unique_files:
                    missing_files.add(full_path)

    # Gather results from futures
    for future in futures:
        result = future.result()
        if result:
            hardlink_hashes.add(result)  # Add only unique hashes
            hardlink_count += 1

# Print out unique hard link hashes
for torrent_hash in hardlink_hashes:
    print(f"Unique hard link detected for torrent with hash '{torrent_hash}'")

# Print missing files
for missing_file in missing_files:
    print(f"Missing file not in unique set: {missing_file}")

print(f"Total hard-linked files: {hardlink_count}")
