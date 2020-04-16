import sys
import os
import json
import xml.etree.ElementTree as xee
import time

def get_dependencies(path, output):
    reqs = set()
    t = time.time()
    try:
        while time.time()-t < 60:
            time.sleep(0.1)
            with open('/home/dekodi/output/tmpdeps.txt','a') as f:
                f.write(str(time.time())+'\n')
                for r, dirs, files in os.walk(path):
                    f.write(json.dumps((r, list(dirs), list(files)))+'\n')
                    if 'addon.xml' not in files:
                        continue
                    fname = r+'/addon.xml'
                    root = xee.parse(fname).getroot()
                    for elem in root.iter():
                        if elem.tag == 'import':
                            reqs.add((elem.get('addon'), elem.get('version')))
            reqs = list(reqs)
            with open(output, 'w') as f:
                json.dump(reqs, f)
    except Exception as e:
        with open('/home/dekodi/output/whatever.txt','w') as f:
            f.write(str(e))


if __name__ == '__main__':
    get_dependencies(sys.argv[1], sys.argv[2])
