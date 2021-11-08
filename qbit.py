import qbittorrentapi
import json
import sys
import getopt
from datetime import datetime
from datetime import timedelta
from collections import defaultdict

findDeleted = False

host = '192.168.1.62'

try:
    opts, args = getopt.getopt(sys.argv[1:], "dh:")
except getopt.GetoptError:
    print("Invalid Command Line")
    sys.exit(2)
for opt, arg in opts:
    if (opt == '-h'):
        host = arg
    if (opt == '-d'):
        findDeleted = True

if (findDeleted == True):
    print("Will Find torrents ready to delete")
else:
    print("Will not find forrents ready to delete. Use -d for a full scan")


print("Connecting to:", host)
qb = qbittorrentapi.Client(host=host, port=8080)
# display qBittorrent info
print(f'qBittorrent: {qb.app.version}')
for k, v in qb.app.build_info.items():
    print(f'{k}: {v}')

torrents = qb.torrents_info()

i = 0
listOfFiles = {}


with open("trackers.json", "r") as read_file:
    trackers_list = json.load(read_file)


if (findDeleted == True):
    firstFileDict = defaultdict(list)
    deletelist = defaultdict(list)

    for torrent in torrents:
        rarred = False

        save_path = torrent['save_path']
        if (not save_path.endswith("/")):
            save_path = save_path + "/"
        files = qb.torrents_files(torrent['hash'])
        for file in files:
            filename = save_path+file.name
            firstFileDict[filename].append(torrent['hash'])
            deletelist[filename].append(torrent['hash'])
            if (file.name.endswith(".rar")):
                rarred = True

        if (rarred):
            qb.torrents_add_tags("RARRED", torrent['hash'])

# print(trackers)
for torrent in torrents:
    #    print (torrent)
    i = i + 1
    private = False
    correctly_marked = False

    trackers = qb.torrents_trackers(torrent['hash'])

    files = qb.torrents_files(torrent['hash'])
    save_path = torrent['save_path']
    if (not save_path.endswith("/")):
        save_path = save_path + "/"

    dupeFound = False
    crossSeeded = False
    if (findDeleted == True):
        for file in files:

            filename = save_path+file.name
            for hash in firstFileDict[filename]:
                #           print (filename, "has hash", hash)
                if (hash != torrent['hash']):
                    #                print (torrent.name,"Is cross-seeded")
                    crossSeeded = True
                    break

#       filename=save_path+file['name']
#       print("    ", filename)
            if (crossSeeded == True):
                qb.torrents_add_tags("cross-seed", torrent['hash'])
                break

    for tracker in trackers:
        if (tracker['msg'].__contains__("private")):
            private = True
            correctly_marked = True

        if (tracker['msg'].__contains__("Unregistered")):
            qb.torrents_add_tags("unregistered", torrent['hash'])

        for tracker_entry in trackers_list:
            done = False
            #print (tracker_entry)
            for tracker_url in tracker_entry['trackers']:
                if (tracker['url'].__contains__(tracker_url)):
                    if (findDeleted == True and torrent['force_start'] == False):
                        torrentcompleted = datetime.fromtimestamp(
                            torrent['completion_on'])
        #                print ("completed on", torrentcompleted)
                        torrentthreshold = torrentcompleted + \
                            timedelta(days=tracker_entry['delete'])
                        if ((datetime.now() > torrentthreshold and tracker_entry['delete'] != 0 and torrent['completion_on'] > 1000000000) or (tracker['msg'].__contains__("Unregistered"))):
                            if (torrent['num_complete'] > 1 or private == False):
                                print("torrent is too old removing hash", torrent['hash'], "for file", filename,
                                      "from deletelist because tracker_entry delete is", tracker_entry['delete'])
                                for file in files:
                                    filename = save_path+file.name
                                    deletelist[filename].remove(
                                        torrent['hash'])

                    qb.torrents_add_tags(
                        tracker_entry['name'], torrent['hash'])
                    if (torrent['amount_left'] == 0 or torrent['dlspeed'] == 0):
                        if (tracker_entry['throttle'] > 0):
                            qb.torrents_set_upload_limit(
                                tracker_entry['throttle'] * 1024, torrent['hash'])
                            print(
                                "setting ", torrent['name'], " to ", tracker_entry['throttle']*1024, " bps upload -- ", tracker_url)
                        else:
                            qb.torrents_set_upload_limit(-1, torrent['hash'])
                    else:
                        if (tracker_entry['throttle_dl'] > 0):
                            qb.torrents_set_upload_limit(
                                tracker_entry['throttle_dl'] * 1024, torrent['hash'])
                            print(
                                "setting ", torrent['name'], " to ", tracker_entry['throttle_dl']*1024, " bps upload -- [DL]", tracker_url)
                        else:
                            qb.torrents_set_upload_limit(-1, torrent['hash'])

                    if (tracker_entry['private'] == "True"):
                        private = True
                    if (tracker_entry['private'] == "False"):
                        private = False
                    if (tracker_entry['ratio'] > 0 or tracker_entry['time'] > 0):
                        time = tracker_entry['time']
                        ratio = tracker_entry['ratio']
                        if (tracker_entry['time'] == 0):
                            time = -2
                        if (tracker_entry['ratio'] == 0):
                            ratio = -2

                        qb.torrents_set_share_limits(
                            ratio, time, torrent['hash'])
                    done = True
                    break
                if (done == True):
                    break
            if (done == True):
                break
        if (done == True):
            break
    if (private == False):
        for tracker_entry in trackers_list:
            if (tracker_entry['name'] == 'public'):
                qb.torrents_add_tags("public", torrent['hash'])
                if (torrent['amount_left'] == 0 or torrent['dlspeed'] == 0):
                    if (tracker_entry['throttle'] > 0):
                        qb.torrents_set_upload_limit(
                            tracker_entry['throttle'] * 1024, torrent['hash'])
                        print("setting ", torrent['name'], " to ", tracker_entry['throttle']
                              * 1024, " bps upload -- ", torrent['tracker'])
                    else:
                        qb.torrents_set_upload_limit(-1, torrent['hash'])
                else:
                    if (tracker_entry['throttle_dl'] > 0):
                        qb.torrents_set_upload_limit(
                            tracker_entry['throttle_dl'] * 1024, torrent['hash'])
                        print("setting ", torrent['name'], " to ", tracker_entry['throttle_dl']
                              * 1024, " bps upload -- ", torrent['tracker'])
                    else:
                        qb.torrents_set_upload_limit(-1, torrent['hash'])

    if (private == True and correctly_marked == False):
        qb.torrents_add_tags("private_not", torrent['hash'])

if (findDeleted == True):
    for name, entry in deletelist.items():
        if (len(entry) == 0):
            print(name, "is Deletable")
            for item in firstFileDict[name]:
                qb.torrents_add_tags("delete", item)


print("Processed ", i, " torrents")
