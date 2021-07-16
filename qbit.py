import qbittorrentapi
import json

qb = qbittorrentapi.Client(host='192.168.1.62', port=8080)
# display qBittorrent info
print(f'qBittorrent: {qb.app.version}')
for k,v in qb.app.build_info.items(): print(f'{k}: {v}')

torrents = qb.torrents_info()

i = 0
listOfFiles=[]


with open("trackers.json", "r") as read_file:
    trackers_list = json.load(read_file)


#print(trackers)
for torrent in torrents:
#    print (torrent)
    i = i + 1
    private = False
    correctly_marked = False

    trackers = qb.torrents_trackers(torrent['hash'])
    files = qb.torrents_files(torrent['hash'])
    save_path = torrent['save_path']

    dupeFound = False
    for file in files:
       filename=save_path+file['name']
       if (filename in listOfFiles):
           dupefound = True
#          print (filename, "is in the list")
       else:
          listOfFiles.append(filename)

    if (dupeFound):
       qb.torrents_add_tags("cross-seed", torrent['hash'])

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
                   qb.torrents_add_tags(tracker_entry['name'], torrent['hash'])
                   if (torrent['amount_left'] == 0 or torrent['dlspeed'] == 0):
                       if (tracker_entry['throttle'] > 0):
                           qb.torrents_set_upload_limit(tracker_entry['throttle'] * 1024, torrent['hash'])
                           print("setting ", torrent['name']," to ",tracker_entry['throttle']*1024," bps upload -- ", tracker_url)
                       else:
                           qb.torrents_set_upload_limit(-1, torrent['hash'])
                   else:
                       if (tracker_entry['throttle_dl'] > 0):
                           qb.torrents_set_upload_limit(tracker_entry['throttle_dl'] * 1024, torrent['hash'])
                           print("setting ", torrent['name']," to ",tracker_entry['throttle_dl']*1024," bps upload -- [DL]", tracker_url)
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

                       qb.torrents_set_share_limits(ratio,time, torrent['hash'])
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
                       qb.torrents_set_upload_limit(tracker_entry['throttle'] * 1024, torrent['hash'])
                       print("setting ", torrent['name']," to ",tracker_entry['throttle']*1024," bps upload -- ", torrent['tracker'])
                   else:
                       qb.torrents_set_upload_limit(-1, torrent['hash'])
               else:
                   if (tracker_entry['throttle_dl'] > 0):
                       qb.torrents_set_upload_limit(tracker_entry['throttle_dl'] * 1024, torrent['hash'])
                       print("setting ", torrent['name']," to ",tracker_entry['throttle_dl']*1024," bps upload -- ", torrent['tracker'])
                   else:
                       qb.torrents_set_upload_limit(-1, torrent['hash'])

    if (private == True and correctly_marked == False):
        qb.torrents_add_tags("private_not", torrent['hash'])

print ("Processed ", i, " torrents")
