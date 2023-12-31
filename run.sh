#!/bin/bash
pip3 install qbittorrent-api
cd /home/qbit
# Start trottling and tagging torrents.. run this often
python3 ./qbit.py
# Find torrents ready for delete.. dont run this too often
#python3 ./qbit.py -d
