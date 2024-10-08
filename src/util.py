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