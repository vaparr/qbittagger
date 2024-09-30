import qbittorrentapi
import os

# Initialize qBittorrent client
qb = qbittorrentapi.Client(host='192.168.1.62', port=8080)

# Fetch torrents info
torrents = qb.torrents_info()

# Set to store unique file paths for faster lookups
unique_files = set()
save_paths = set()

def is_hard_link(filename):
    return os.stat(filename).st_nlink > 1

# Process each torrent
for torrent in torrents:
    files = qb.torrents_files(torrent['hash'])
    save_path = torrent['save_path'].replace("/plexmedia/", "/mnt/user/plexmedia/")

    # Ensure save_path ends with a '/'
    if not save_path.endswith("/"):
        save_path += "/"
    save_paths.add(save_path)
    # Add file paths to the set
    for file in files:
        filename = os.path.join(save_path, file['name'])
        unique_files.add(filename)

hardlink_count = 0

# Walk through the directory to find files
for path in save_paths:
    print(f"Scanning {path}")
    for root, _, filenames in os.walk(path):
        for file in filenames:
            full_path = os.path.join(root, file)

            # Check for hard links
            if is_hard_link(full_path):
                hardlink_count += 1
                
            # Print paths not in the set
            if full_path not in unique_files:
                print(full_path)

print(f"Hard-linked files: {hardlink_count}")