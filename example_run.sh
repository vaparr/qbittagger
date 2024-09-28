#!/bin/bash
pip3 install -r requirements.txt
cd /home/qbit
python3 ./qb-tagger.py -s "QB_SERVER_IP" -p "QB_PORT"
# Example: python3 ./qb-tagger.py -s "192.168.1.50" -p "8080"
