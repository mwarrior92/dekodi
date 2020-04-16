import sys
import xmlrpclib

p = xmlrpclib.ServerProxy('http://127.0.0.1:8888')

p.save_url(sys.argv[1:])

try:
    with open('/home/dekodi/output/urlsbla.txt','a') as f:
        f.write(str(sys.argv[1:])+'\n')
except:
    pass
