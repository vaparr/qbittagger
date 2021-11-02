import qbittorrentapi
import json
import os

qb = qbittorrentapi.Client(host='192.168.1.62', port=8080)
# display qBittorrent info
#print(f'qBittorrent: {qb.app.version}')
#for k,v in qb.app.build_info.items(): print(f'{k}: {v}')

torrents = qb.torrents_info()

i = 0
listOfFiles=[]


# Get a list of all files
for torrent in torrents:
    files = qb.torrents_files(torrent['hash'])
    save_path = torrent['save_path'].replace("/plexmedia/","/mnt/user/plexmedia/")
    if (save_path.endswith("/")):
       rarred= False
    else:
       save_path = save_path + "/"

    dupeFound = False
    rarred = False
    for file in files:
       filename=save_path+file['name']
#       print(filename)
       if (filename in listOfFiles):
           dupefound = True
#          print (filename, "is in the list")
       else:
          listOfFiles.append(filename)

#print (listOfFiles)

# Find all files that are not in the list
for path, dirname, filenames in os.walk("/mnt/user/plexmedia/downloads/qBittorrent/downloads/"):
#    print (path,"path")
#    print (dirname,"dirname")
    if (path.endswith("/")):
       rarred= False
    else:
       path = path + "/"

    for fis in filenames:
        fullpath = path+fis
#        print (fullpath)
        if fullpath in listOfFiles:
            dupefound = True
#            print(fullpath)
        else:
            print(fullpath)
