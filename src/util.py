import json
import os
import time
from datetime import datetime, timedelta

Config_Manager = None
Current_Time = time.time()

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

def get_age(added_on, days_only=False):

    # Calculate age in seconds (Current time in seconds since the epoch - added_on)
    age_in_seconds = Current_Time - added_on

    # Convert seconds to a more readable format (days, hours, minutes)
    age_days = age_in_seconds // 86400  # Number of seconds in a day
    age_hours = (age_in_seconds % 86400) // 3600  # Remaining hours
    age_minutes = (age_in_seconds % 3600) // 60  # Remaining minutes

    if days_only:
        return age_days

    return f"{age_days} days, {age_hours} hours, {age_minutes} minutes"

def days_since(timestamp):

    if not timestamp > 1000000000:
        return -2  # Return a negative value for invalid timestamps

    elapsed_time = Current_Time - timestamp
    days_elapsed = elapsed_time / (60 * 60 * 24)
    return round(days_elapsed, 2)

def file_modified_older_than(file_path, num_days):
    try:

        days_in_seconds = num_days * 24 * 60 * 60

        # Get the file's last modified time
        file_mod_time = os.path.getmtime(file_path)
        
        # Get the current time and calculate the threshold
        file_age = Current_Time - file_mod_time
        
        # Return True if the file was modified more than 'num_days' ago
        return file_age > days_in_seconds
    
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return False
    except Exception as e:
        print(f"Error checking file modification time: {e}")
        return False