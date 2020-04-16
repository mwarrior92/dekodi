import sys
import os
import json

s = 0
for r, _, files in os.walk('/home/dekodi/.kodi/addons/'):
    for f in files:
        s += os.stat(r+'/'+f).st_size

with open('/home/dekodi/output/'+sys.argv[1]+'.json','w') as f:
    json.dump([s], f)
