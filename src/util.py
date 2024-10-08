import json
import os

Config_Manager = None

def load_trackers(tracker_json_path):

    if not os.path.exists(tracker_json_path):
        print(f"ERROR: Unable to find '{tracker_json_path}'")
        exit(2)

    with open(tracker_json_path, "r") as read_file:
        return json.load(read_file)

def format_path(path):
    if not path.endswith("/"):
        path = path + "/"
    return path

def format_bytes(size):
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'kilo', 2: 'mega', 3: 'giga', 4: 'tera'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}bytes"