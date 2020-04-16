import sys
from SimpleXMLRPCServer import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
from SocketServer import ThreadingMixIn
import argparse
import json
import time
import os.path
from crawler import crawl_source
from multiprocessing import Process, Lock
import gitcrawl
import traceback
import xmlrpclib
import redditcrawl
import docker
from collections import defaultdict
import os

g_stop_me = False

class threadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    '''
    class to thread server so it doesn't get stuck on blocking calls
    '''
    def _dispatch(self, method, params):
        try:
            print('REMAINING: '+str(REMAINING))
            return SimpleXMLRPCServer._dispatch(self, method, params)
        except Exception as e:
            global g_stop_me
            t, value, tb = sys.exc_info()
            g_stop_me = ''.join(traceback.format_exception(t,
                value, tb))
            with open('hault_error.txt','a') as f:
                f.write(g_stop_me + '\n')
            raise xmlrpclib.Fault(1, ''.join(traceback.format_exception(t,
                value, tb))+'************'+str(e)+'||||||'+str(params)[:100])

DAY = 86400

REMAINING = -1

parser = argparse.ArgumentParser(description='dekodi crawler source server')
parser.add_argument("-p", "--port", default=9999)
parser.add_argument("-m", "--makesourcelist", default=0)
parser.add_argument("-s", "--sourcefile", help="file where each line is \
        a json block with the source formatted as {'path': ('method', extra)}",
        default='addon_sources.json')
parser.add_argument("-H", "--historyfile", help="json file indicating \
        when each source was last tested; formatted {'path': timestamp}",
        default='test_history.json')
parser.add_argument("-x", "--host_path_prefix", help="path used for folder \
        organization", default='research/dekodi/dev/')
parser.add_argument("-P", "--path",      help = "path used for folder organization (default: research/dekodi/dev/)")
args = parser.parse_args()
if args.path:
    g_host_path_prefix = args.path
else:
    g_host_path_prefix = 'research/dekodi/dev/'

addon_tree_paths = 'addontrees/'
if not os.path.exists(addon_tree_paths):
    os.makedirs(addon_tree_paths)


g_fails = 'fail_data.list'
g_installs = 'install_data.list'
g_videos = 'video_data.list'

args = parser.parse_args()

server = threadedXMLRPCServer(("", int(args.port)), SimpleXMLRPCRequestHandler)


g_history = list()
def load_history(path):
    global g_history
    g_history = list()
    with open(args.historyfile, 'r') as f:
        for line in f:
            g_history.append(tuple(json.loads(line)))

try:
    load_history(args.historyfile)
except Exception as e:
    print(str(e))
    g_history = list()


def load_sources(path):
    sources = dict()
    with open(path, 'r') as f:
        for line in f:
            try:
                sources.update(json.loads(line))
            except:
                traceback.print_exception(*sys.exc_info())
    return sources

def get_next_source(sources_path):
    if g_stop_me:
        print('hit an error... quitting now')
        return 'DONE!!!', ['DONE!!!', 'DONE!!!']
    print(sources_path)
    with g_lock:
        print('got lock')
        if os.path.exists(args.historyfile):
            load_history(args.historyfile)
        redos = list()
        done_redos = list()
        try:
            with open('done_redos.txt','r') as f:
                for line in f:
                    done_redos.append(line.strip())
            with open('redo_list.txt','r') as f:
                for line in f:
                    if line.strip() not in done_redos:
                        redos.append(line.strip())
        except:
            pass
        print('redos: '+str(len(redos)))
        global g_history
        global REMAINING
        sources = load_sources(sources_path)
        print('got sources '+str(len(sources)))
        all_sources = set(sources.keys())
        print(len(all_sources))
        if g_history:
            untested = all_sources.difference(zip(*g_history)[0])
        else:
            untested = all_sources
        untested = untested.union(redos)
        print(len(untested))
        REMAINING = len(untested)
        if len(untested):
            source = untested.pop()
            mthd = sources[source]
        else:
            print('ALL DONE!!!')
            return 'DONE!!!', ['DONE!!!', 'DONE!!!']
        g_history.append((source, time.time()))
        with open(args.historyfile, 'a') as f:
            f.write(json.dumps(list(g_history[-1]))+'\n')
        if source in redos:
            try:
                with open('done_redos.txt','a') as f:
                    f.write(source+'\n')
            except:
                pass
    return source, mthd


def get_tree_filename(aid, v):
    if v < '0':
        v = 'null'
    return addon_tree_paths+aid+'.json'


def check_tree(aid, v):
    path = get_tree_filename(aid,v)
    if not os.path.exists(path):
        return {
    'addons': [], 'deps': [], 'type': [], 'installs': 0, 'fails': 0,
    'sources': [], 'seeds': [], 'plays': [], 'content': [], 'tries': 0, 'aid': '', 'v': '',
    'images': []
    }
    with open(path, 'r') as f:
        ret = json.load(f)
    return ret


def in_default(addon):
    return 'is_in_default' in addon and addon['is_in_default']


def keep_checking(addons_data):
    print('checking addons vs system state....')
    keeps = list()
    if type(addons_data) is str:
        addons_data = json.loads(addons_data)
    for addon in addons_data:
        with g_lock:
            if addon['aid'].startswith('dekodi.tmp.addon.'):
                aid = os.path.basename(addon['sources'][0]) + '_'+addon['aid'][-1]+'dekodirepo'
            else:
                aid = addon['aid']
            v = addon['v'] if 'v' in addon else '-1'
            data = check_tree(aid, v)
            if not data or not data['aid']:
                data.update(addon)
                data['aid'] = aid
                keeps.append(addon)
                with open(get_tree_filename(aid,v), 'w') as f:
                    json.dump(data, f)
                continue
            if not data['installs'] and (data['tries'] < 4 or data['v'] < v) and not in_default(addon):
                keeps.append(addon)
            for source in addon['sources']:
                if source not in data['sources']:
                    data['sources'].append(source)
            for seed in addon['seeds']:
                if seed not in data['seeds']:
                    data['seeds'].append(seed)
            for key, val in addon.items():
                if type(val) is int and val <= 0:
                    continue
                if not val and type(val) is not bool:
                    continue
                if key not in data:
                    data[key] = val
                elif type(val) is list:
                    try:
                        data[key] = list(set(data[key]+val))
                    except:
                        data[key] += val
                elif not data[key]:
                    data[key] = val
            with open(get_tree_filename(aid,v), 'w') as f:
                json.dump(data, f)
    return json.dumps(keeps)
server.register_function(keep_checking, 'keep_checking')


def starting_crawl(addon):
    if type(addon) is str:
        addon = json.loads(addon)
    ret = False
    with g_lock:
        if addon['aid'].startswith('dekodi.tmp.addon.'):
            aid = os.path.basename(addon['sources'][0]) + '_'+addon['aid'][-1]+'dekodirepo'
        aid = addon['aid']
        v = addon['v'] if 'v' in addon else '-1'
        data = check_tree(aid, v)
        if not data or not data['aid']:
            data.update(addon)
        if not data['installs'] and data['tries'] < 3:
            data['tries'] += 1
            ret = True
        for key, val in addon.items():
            if type(val) is int and val <= 0:
                continue
            if not val and type(val) is not bool:
                continue
            if key not in data:
                data[key] = val
            elif type(val) is list:
                try:
                    data[key] = list(set(data[key]+val))
                except:
                    data[key] += val
            elif not data[key]:
                data[key] = val
        with open(get_tree_filename(aid,v), 'w') as f:
            json.dump(data, f)
    return ret
server.register_function(starting_crawl, 'starting_crawl')


def done_data(addons_data):
    if type(addons_data) is str:
        addons_data = json.loads(addons_data)
    print("DOING DONE")
    for addon in addons_data:
        with g_lock:
            if addon['aid'].startswith('dekodi.tmp.addon.'):
                aid = os.path.basename(addon['sources'][0]) + '_'+addon['aid'][-1]+'dekodirepo'
            aid = addon.pop('aid')
            v = addon.pop('v')
            data = check_tree(aid, v)
            if not data or not data['aid']:
                data.update(addon)
                with open(get_tree_filename(aid,v), 'w') as f:
                    json.dump(data, f)
                continue
            if 'installs' in addon:
                data['installs'] += addon.pop('installs')
                with open(g_installs, 'a') as f:
                    f.write(json.dumps({'aid': aid, 'v': v})+'\n')
            if 'fails' in addon:
                data['fails'] += addon.pop('fails')
                with open(g_fails, 'a') as f:
                    f.write(json.dumps({'aid': aid, 'v': v})+'\n')
            try:
                if 'deps' in addon and addon['deps']:
                    data['deps'] = addon.pop('deps')
                elif 'deps' in addon:
                    addon.pop('deps')
            except:
                traceback.print_exception(*sys.exc_info())
            for key, val in addon.items():
                if type(val) is int and val <= 0:
                    continue
                if not val and type(val) is not bool:
                    continue
                if key not in data:
                    data[key] = val
                elif type(val) is list:
                    try:
                        data[key] = list(set(data[key]+val))
                    except:
                        data[key] += val
                elif not data[key]:
                    data[key] = val
            with open(get_tree_filename(aid,v), 'w') as f:
                json.dump(data, f)
    return True
server.register_function(done_data, 'done_data')


def next_source(crawl_controller):
    print('getting next source...')
    tmp = get_next_source(args.sourcefile)
    print(tmp)
    return tmp
server.register_function(next_source, 'next_source')

def add_source(source, method):
    with g_lock:
        with open(args.sourcefile, 'a') as f:
            f.write(json.dumps({source: method})+'\n')
    return True
server.register_function(add_source, 'add_source')

def get_path(key):
    print('getting path!!!')
    if 'github' in key:
        with open('github_paths.json', 'r') as f:
            data = json.load(f)[key]
        return data
    return []
server.register_function(get_path, 'get_path')

def refresh_sources(lock):
    print('refreshing resources')
    nextday = time.time() + DAY
    try:
        try:
            addons, paths = redditcrawl.crawl_reddit()
            with lock:
                with open('addon_sources.json', 'w') as f:
                    for addon in addons:
                        print(addon)
                        f.write(json.dumps(addon)+'\n')
                if os.path.exists('github_paths.json'):
                    with open('github_paths.json', 'r') as f:
                        try:
                            old = json.load(f)
                            old.update(paths)
                        except:
                            old = paths
                with open('github_paths.json', 'w') as f:
                    json.dump(old, f)
                    time.sleep(40)
        except Exception as e:
            with open('Exceptions.txt', 'a') as f:
                f.write('REDDIT: '+str(e)+'\n')
        try:
            addons = crawl_source('http://lazykodi.com/', 'lazykodi',
                    host_path_prefix=g_host_path_prefix)
            with lock:
                with open('addon_sources.json', 'w') as f:
                    for addon in addons:
                        f.write(json.dumps(addon)+'\n')
        except Exception as e:
            with open('Exceptions.txt', 'a') as f:
                f.write('LAZYKODI: '+str(e)+'\n')
        try:
            addons, paths = gitcrawl.find_addons()
            with lock:
                with open('addon_sources.json', 'w') as f:
                    for addon in addons:
                        f.write(json.dumps(addon)+'\n')
                if os.path.exists('github_paths.json'):
                    with open('github_paths.json', 'r') as f:
                        try:
                            old = json.load(f)
                            old.update(paths)
                        except:
                            old = paths
                with open('github_paths.json', 'w') as f:
                    json.dump(old, f)
        except Exception as e:
            with open('Exceptions.txt', 'a') as f:
                f.write('GITHUB: '+str(e)+'\n')
        wait = time.time() - nextday
        if wait > 0:
            time.sleep(wait)
    except:
        traceback.print_exception(*sys.exc_info())



g_lock = Lock()
try:
    if args.makesourcelist:
        worker = Process(target=refresh_sources, args=(g_lock,))
        print('starting worker')
        worker.start()
    while True:
        try:
            server.serve_forever()
        except Exception as e:
            if e is KeyboardInterrupt:
                sys.exit()
            client = docker.from_env()
            for container in client.containers.list():
                if 'kodi_crawler' in container.name:
                    print('killing '+container.name+'...')
                    container.kill()


except KeyboardInterrupt:
    if args.makesourcelist:
        try:
            worker.terminate()
        except:
            traceback.print_exception(*sys.exc_info())
    else:
        traceback.print_exception(*sys.exc_info())
