import sys
import os
import requests
import xmlrpclib
import subprocess
from random import shuffle
import docker
import re
try:
    import json
except:
    import simplejson as json
import time
import socket
import requests
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import shutil
import traceback
import xml.etree.ElementTree as xee
import urllib


#***************************************************
# need this for initially enabling service; should only have to do it once
# UPDATE: probably won't need this since we're using a docker image with
# everything already enabled
#***************************************************

def errout(e):
    print(e)
    with open('erroroutdata.txt','a') as f:
        f.write(e+'\n-----------------------\n')

def do_jsonrpc(method, server, timeout=3, **params):
    req = {
            'method': method,
            'jsonrpc': '2.0',
            'id': '0',
            }
    if len(params) > 0:
        req['params'] = params
    res = requests.post(server, json=req, timeout=timeout)
    try:
        return True, json.loads(res.text)
    except:
        return False, {}


def set_addon_enabled(addonid):
    return do_jsonrpc('Addons.SetAddonEnabled', addonid=addonid, enabled=True)

#***************************************************
# classes
#***************************************************

class KodiCrawler(object):
    def __init__(self, aid, container_name, image, screen_identifier, host_path_prefix='',
            container_home_path='', crawl_id='', no_out=False, **kwargs):
        socket.setdefaulttimeout(75)
        self.install_timeframe = []
        self.urls = dict()
        self.play_pages = set()
        self.uid = str(time.time()).split('.')[0]
        self._rpcfuncs = []
        self.no_out = no_out
        self.start_time = time.time()
        self.init_time = -1
        self.aid = aid
        self.real_aid = ''
        self.missing_reqs = []
        self.container_name = container_name
        self.clicks = 0
        self.image = image
        self.host_path_prefix = host_path_prefix
        self.container_home_path = container_home_path
        self.screen_identifier = str(screen_identifier)
        self._server = None
        self._proxy = None
        self.container = None
        self.changes = None
        self.new_images = list()
        self.kodi_state = None
        self.kodi_window = None
        self.failed = False
        self.rpc_durations = list()
        self.restarts = 0
        self.crawl_id = crawl_id
        self.hash = ''
        self.addon_data = dict()
        self.keys_to_first_video = -1
        self.docker_issue = ""
        self.kodi_crashes = 0
        for k in kwargs:
            setattr(self, k, kwargs[k])
        self.force_start_container()

    @property
    def is_zip(self):
        if 'is_zip' in self.addon_data and self.addon_data['is_zip']:
            return True
        return False

    @property
    def needs_aid(self):
        if 'needs_aid' in self.addon_data and self.addon_data['needs_aid']:
            return True
        return False

    @property
    def zip_deps(self):
        if 'zip_deps' in self.addon_data and self.addon_data['zip_deps']:
            return self.addon_data['zip_deps']
        return list()

    @property
    def addon_data_str(self):
        return json.dumps(self.addon_data)

    @property
    def fmt_aid(self):
        return self.aid.split('/')[-1].split('_dekodi_')[0]

    @property
    def imgstr(self):
        return '_'.join(self.image.split(':'))

    @property
    def relative_data_path(self):
        return self.crawl_id + '/' + self.imgstr + '/' + self.fmt_aid + '___UID'+ \
                self.uid+'/'

    @property
    def datadir(self):
        hosthome = os.path.expanduser('~') + '/'
        datadir = hosthome + self.host_path_prefix + 'dekodi_data/' \
                + self.crawl_id + '/' + self.imgstr + '/' + self.fmt_aid + '___UID'+ \
                self.uid+'/'
        if not os.path.exists(datadir):
            os.makedirs(datadir)
        self._datadir = datadir
        return datadir

    @property
    def crawldatadir(self):
        '''
        hosthome = os.path.expanduser('~') + '/'
        datadir = hosthome + self.host_path_prefix + 'dekodi_crawl_state/' \
                + self.crawl_id + self.fmt_aid + '___UID/'
        if not os.path.exists(datadir):
            os.makedirs(datadir)
        self._datadir = datadir
        return datadir
        '''
        hosthome = os.path.expanduser('~') + '/'
        datadir = hosthome + self.host_path_prefix + 'dekodi_crawl_state/' \
                + self.crawl_id + self.fmt_aid + '___UID/'
        return datadir

    @property
    def unstuckdir(self):
        self._unstuckdir = self.crawldatadir + 'unstuck.json'
        return self._unstuckdir

    @property
    def scriptdir(self):
        hosthome = os.path.expanduser('~') + '/'
        datadir = hosthome + self.host_path_prefix + 'dekodi_scripts/'
        if not os.path.exists(datadir):
            os.makedirs(datadir)
        self._scriptdir = datadir
        return datadir

    @property
    def addondir(self):
        hosthome = os.path.expanduser('~') + '/'
        addondir = hosthome + self.host_path_prefix + 'service.api.dekodi'
        self._addondir = addondir
        return addondir

    @property
    def mitmdir(self):
        hosthome = os.path.expanduser('~') + '/'
        addondir = hosthome + self.host_path_prefix + 'crawler.dekodi/mitmproxy'
        self._mitmdir = addondir
        return addondir

    @property
    def zip_addondir(self):
        p = subprocess.Popen('./fix_permissions.sh', shell=True, stdout=subprocess.PIPE)
        p.wait()
        path = self.scriptdir + 'zip_addons'
        self._zip_addondir = path
        return path

    @property
    def container_zip_addondir(self):
        return self.container_home_path + 'zip_addons/'

    @property
    def outputdir(self):
        '''
        datadir = self.datadir + 'output/'
        if not os.path.exists(datadir):
            os.makedirs(datadir)
        self._outputdir = datadir
        '''
        return self.datadir

    @property
    def container_running(self):
        self.container = None
        client = docker.from_env()
        try:
            self.container = client.containers.get(self.container_name)
            return 'running' in self.container.status
        except docker.errors.NotFound:
            return False
        except docker.errors.APIError as e:
            pass
        if self.container is None:
            return False
        else:
            return True

    @property
    def server(self):
        if self._server is not None:
            return self._server
        addr = None
        client = docker.from_env()
        for network in client.networks.list():
            network.reload()
            if 'Containers' not in network.attrs:
                continue
            for container in network.attrs['Containers'].values():
                if self.container_name == container['Name']:
                    addr = container['IPv4Address'].split('/')[0]
                    break
        if addr is not None:
            self._server = str('http://' + addr + ':8888')
            print('server: '+self._server +' '+self.fmt_aid)
        else:
            self._server = None
        return self._server

    @server.setter
    def server(self, val):
        self._server = val

    @property
    def found_videos_filepath(self):
        path = self.datadir + 'found_videos_'+str(time.time())+'.json'
        self._found_videos_filepath = path
        return path

    @property
    def fail_sequence_filepath(self):
        path = self.datadir + 'fail_sequences.json'
        self._fail_sequences_filepath = path
        return path

    @property
    def proxy(self):
        if self._proxy is None:
            if self.server is not None:
                self._proxy = xmlrpclib.ServerProxy(self.server)
        if self._proxy is not None:
            if not self.verify_kodi_state(timeout=5):
                print('kodi missing... '+self.fmt_aid)
        return self._proxy

    @property
    def rpcfuncs(self):
        if len(self._rpcfuncs) == 0:
            self._rpcfuncs = self.proxy.system.listMethods()
        return self._rpcfuncs

    @rpcfuncs.setter
    def rpcfuncs(self, val):
        self._rpcfuncs = val

    def __getattr__(self, attr):
        try:
            if attr in self.rpcfuncs:
                if attr == 'select':
                    self.clicks += 1
                func = getattr(self.proxy, attr)
                def wrapper(*args):
                    start = time.time()
                    result = func(*args)
                    stop = time.time()
                    self.rpc_durations.append({attr: stop-start})
                    print('--'+str(len(self.rpc_durations))+'-- rpcs -- image: '+self.imgstr+'; aid: '+self.fmt_aid)
                    if hasattr(result, '__len__') and len(result) > 21:
                        print(attr+': '+str(self.rpc_durations[-1])+ \
                        ', '+str(len(result)) +' items -- '+self.fmt_aid)
                    else:
                        print(attr+': '+str(self.rpc_durations[-1])+ \
                                ', '+str(result) +' -- '+self.fmt_aid)
                    return result
                return wrapper
            else:
                raise AttributeError(attr)
        except Exception as e:
            print(str(e) +' '+self.fmt_aid)
            raise AttributeError(attr)

    def start_container(self):
        '''
        starts container and returns xmlrpc server
        '''
        client = docker.from_env()
        print('screen id: '+str(self.screen_identifier) +' '+self.fmt_aid)
        fails = 0
        while True:
            print('CONTINUING TRY TO START '+self.fmt_aid)
            try:
                self.container = client.containers.run(self.image, name=self.container_name,
                        detach=True, tty=True, stdin_open=True, volumes={
                            '/tmp/.X11-unix': {'bind': '/tmp/.X11-unix', 'mode': 'rw'},
                            self.outputdir: {
                                'bind': self.container_home_path+'output',
                                'mode': 'rw'},
                            self.mitmdir: {
                                'bind': '/usr/share/ca-certificates/custom/',
                                'mode': 'rw'},
                            self.addondir: {
                                'bind': self.container_home_path + \
                                        '.kodi/addons/service.api.dekodi',
                                'mode': 'ro'},
                            self.zip_addondir: {
                                'bind': self.container_zip_addondir,
                                'mode': 'rw'}},
                        environment=["DISPLAY=:"+self.screen_identifier], auto_remove=True)
                break
            except Exception as e:
                tback = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(tback)
                self.docker_issue = tback
                fails += 1
                if fails < 3:
                    print('trying again on '+self.fmt_aid)
                    #self.stop_container()
                    time.sleep(3)
                else:
                    break
        if self.verify_container_state():
            print('PART 2 of START '+self.fmt_aid)
            client = docker.from_env()
            ran = False
            for i in range(2):
                try:
                    self.container = client.containers.get(self.container_name)
                    self.container.exec_run(self.container_home_path+'.kodi/addons/service.api.dekodi/start_tstat.sh', detach = True)
                    ran = True
                    break
                except docker.errors.APIError as e:
                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                    errout(estr)
                    self.docker_issue = estr
            if not ran:
                return False
            if not self.start_kodi():
                print('trying to start kodi again...'+self.fmt_aid)
                self.start_kodi()
            self.store_initial_state()
            return True
        else:
            print('failed to start: '+self.container_name +' '+self.fmt_aid)
            return False

    def force_start_container(self, maxtries=3, retry_wait_time=1):
        print('force starting...' +' '+self.fmt_aid)
        print('starting '+self.container_name+' for '+self.aid+'...')
        #self.start_container()
        tried = False
        while not tried or not self.container_running:
            tried = True
            try:
                print('gonna stop first')
                self.stop_container()
                print('done stoppin')
                self.start_container()
                print('started '+self.container_name +' '+self.fmt_aid)
                break
            except Exception as e:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr+' '+self.fmt_aid)
            raise Exception('failed to start container: '+self.fmt_aid+'; '+self.image)

        success = self.container_running
        if not success:
            self.failed = True
        self.init_time = time.time() - self.start_time
        if success and hasattr(self, 'addonsource'):
            with open(self.datadir+'addonsource.json', 'w') as f:
                json.dump({'aid': self.aid, 'source': self.addonsource,
                    'hash': self.hash}, f)
        return success

    def store_button_info(self, button_pos, **kwargs):
        data = self.proxy.get_listitem_data(button_pos)
        kwargs.update(data)
        self.button_info = kwargs

    def save_found_video(self, url):
        urldata = self.proxy.proc_url(url)
        self.button_info.update(json.loads(urldata))
        video = self.button_info
        video = self.proxy.correct_video_data(json.dumps(video))
        video = self.proxy.do_ffprobe(video)
        print('saving found video' +' '+self.fmt_aid)
        with open(self.found_videos_filepath, 'a+') as f:
            f.write(video+'\n')
            print('saved found video to '+self.found_videos_filepath +' '+self.fmt_aid)
        return json.loads(video)

    def save_fail_sequence(self):
        with open(self.fail_sequence_filepath, 'a+') as f:
            f.write(json.dumps(self.button_info)+'\n')

    @property
    def system_state_change_filepath(self):
        self._system_state_change_filepath = \
                self.datadir + 'system_state_change.diff'
        return self._system_state_change_filepath

    @property
    def nondefaults_filepath(self):
        self._nondefaults_filepath = \
                self.datadir + 'nondefaults.json'
        return self._nondefaults_filepath

    def store_initial_state(self):
        print('storing state '+self.fmt_aid)
        client = docker.from_env()
        for i in range(2):
            try:
                container = client.containers.get(self.container_name)
                container.exec_run('python '+self.container_home_path+'.kodi/addons/service.api.dekodi/get_size.py initial_size', detach = True)
                ran = True
                break
            except:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
                self.docker_issue = estr

        self.initial_state = {
                'installed': list(
                    self.proxy.get_installed_addons_list().keys()),
                'uninstalled': list(
                    self.proxy.get_uninstalled_addons_list().keys())
                }

    @property
    def container_diff(self):
        client = docker.from_env()
        container = client.containers.get(self.container_name)
        return container.diff()

    def get_addon_changes(self):
        try:
            print('getting installed list...')
            installed = self.proxy.get_installed_addons_list()
            print('getting uninstalled list...')
            uninstalled = self.proxy.get_uninstalled_addons_list()
        except Exception as e:
            print(str(e)+'... trying again')
            time.sleep(2)
            installed = self.proxy.get_installed_addons_list()
            uninstalled = self.proxy.get_uninstalled_addons_list()

        return  {
                'new_installed': {k:v for k,v in installed.items() \
                        if k not in self.initial_state['installed']},
                'new_uninstalled': {k:v for k,v in uninstalled.items() \
                        if k not in self.initial_state['uninstalled']},
                'containeer_diff': self.container_diff,
                }


    def get_system_state_changes(self):
        installed = self.proxy.get_installed_addons_list()
        uninstalled = self.proxy.get_uninstalled_addons_list()
        self.changes = {
                'new_installed': {k:v for k,v in installed.items() \
                        if k not in self.initial_state['installed']},
                'new_uninstalled': {k:v for k,v in uninstalled.items() \
                        if k not in self.initial_state['uninstalled']},
                'containeer_diff': self.container_diff,
                }
        return self.changes

    def set_install_timeframe(self, t0, t1):
        self.install_timeframe = [t0, t1]
        print('SET install timeframe: ')
        print(self.install_timeframe)

    @property
    def default_addons(self):
        with open('default_addons.json', 'r') as f:
            data = set(json.load(f).keys())
        return data

    def save_system_state_changes(self):
        client = docker.from_env()
        ran = False
        for i in range(2):
            try:
                container = client.containers.get(self.container_name)
                container.exec_run('python '+self.container_home_path+'.kodi/addons/service.api.dekodi/get_size.py final_size', detach = True)
                ran = True
                break
            except:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
                self.docker_issue = estr
        try:
            if self.changes is None:
                self.get_system_state_changes()
            with open(self.system_state_change_filepath, 'w+') as f:
                json.dump(self.changes, f)
            self.changes = None
        except:
            pass
        try:
            installed = set(list(self.proxy.get_installed_addons_list().keys()))
            with open(self.nondefaults_filepath, 'w') as f:
                json.dump(list(installed.difference(self.default_addons)), f)
        except:
            pass



    def verify_container_state(self, running=True, timeout=20):
        timeout = time.time() + timeout
        if running:
            while self.server is None and timeout > time.time():
                time.sleep(0.1)
            return self.container_running
        else:
            while self.server is not None and timeout > time.time():
                time.sleep(0.1)
            return not self.container_running

    def verify_kodi_state(self, running=True, timeout=30):
        timeout = time.time() + timeout
        success = False
        if running:
            while timeout > time.time():
                try:
                    if self._proxy is None:
                        self._proxy = xmlrpclib.ServerProxy(self.server)
                    self.kodi_state = self._proxy.get_state()
                    self.kodi_window = self._proxy.get_full_window()
                    success = True
                    break
                except Exception as e:
                    time.sleep(0.5)
        else:
            while timeout > time.time():
                try:
                    if self._proxy is None:
                        self._proxy = xmlrpclib.ServerProxy(self.server)
                    self.kodi_state = self._proxy.get_state()
                    self.kodi_window = self._proxy.get_full_window()
                except:
                    success = True
                    break
                time.sleep(0.1)
        return success

    def restart_kodi(self, goto=None):
        print('restarting kodi' +' '+self.fmt_aid)
        self.restarts += 1
        self.stop_kodi()
        self.save_log()
        if not self.start_kodi():
            print('trying to start kodi again...'+self.fmt_aid)
            self.start_kodi()
        tmp = self.verify_kodi_state(timeout=1)
        if tmp:
            print('restarted kodi' +' '+self.fmt_aid)
        else:
            print('failed to restart kodi' +' '+self.fmt_aid)
        if goto is not None:
            self.proxy.activate_window(*goto)
        return tmp

    def fmt_image(self, new_image_name, new_image_tag=None):
        if new_image_tag is not None:
            new_image_tag = self.get_root_name(new_image_tag)
            full_image_name = new_image_name +':'+new_image_tag
        elif ':' in new_image_name:
            new_image_name, new_image_tag = new_image_name.split(':')
            new_image_tag = self.get_root_name(new_image_tag)
            full_image_name = new_image_name+':'+new_image_tag
        else:
            new_image_tag = 'latest'
        new_image_name = re.sub('[^a-zA-Z0-9_\.]+', '_', new_image_name)
        new_image_tag = re.sub('[^a-zA-Z0-9_\.]+', '_', new_image_tag)
        full_image_name = new_image_name + ':' + new_image_tag
        return full_image_name

    def make_addon_image(self, new_image_name, new_image_tag=None):
        print('making addon image' +' '+self.fmt_aid)
        if new_image_tag is not None:
            new_image_tag = new_image_tag
            full_image_name = new_image_name +':'+new_image_tag
        elif ':' in new_image_name:
            new_image_name, new_image_tag = new_image_name.split(':')
            new_image_tag = new_image_tag
            full_image_name = new_image_name+':'+new_image_tag
        else:
            full_image_name = new_image_name+':latest'
            new_image_tag = 'latest'
        client = docker.from_env()
        try:
            client.images.remove(full_image_name)
        except:
            pass
        container = client.containers.get(self.container_name)
        container.commit(new_image_name, new_image_tag)
        self.new_images.append(full_image_name)
        return self.new_images[-1]

    def start_kodi(self):
        print('starting kodi (in func)' +' '+self.fmt_aid)
        client = docker.from_env()
        container = client.containers.get(self.container_name)

        # [MV] -- if needed start a virtual screen
        if self.screen_identifier != '0':
            print('starting virtual' +' '+self.fmt_aid)
            ran = False
            for i in range(2):
                try:
                    container.exec_run('sudo /etc/init.d/xvfb start ' + self.screen_identifier, detach = True)
                    ran = True
                    break
                except docker.errors.APIError as e:
                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                    errout(estr)
            if not ran:
                self.docker_issue = estr
                raise Exception(estr)
        else:
            print('real screen' +' '+self.fmt_aid)
        print('actually starting kodi-x11 '+self.fmt_aid)
        ran = True
        for i in range(2):
            try:
                container.exec_run(self.container_home_path+'kodi/kodi-build/kodi-x11',
                        detach=True)
                ran = True
                break
            except docker.errors.APIError as e:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
                self.docker_issue = estr
        if not ran:
            self.docker_issue = estr
            raise Exception(estr)
        print('verifying...')
        if self.verify_kodi_state():
            print('started kodi' +' '+self.fmt_aid)
            return True
        else:
            print('failed to start kodi' +' '+self.fmt_aid)
            self.screen_identifier = str(20+int(self.screen_identifier))
            return False

    def get_last_key(self):
        res = ''
        try:
            with open(self.outputdir+'keys_pressed.list', 'r') as f:
                lines = list()
                for line in f:
                    lines.append(line)
                res = lines[-1]
        except:
            pass
        return res

    def is_video(self):
        try:
            print('is it video?' +' '+self.fmt_aid)
            if self.proxy.is_playing():
                url = self.proxy.get_video_url()
                try:
                    with open(self.outputdir+'keys_to_video.list', 'r') as f:
                        self.keys_to_first_video = int(f.read().strip())
                except:
                    pass
                if url[0] in self.urls:
                    pass
                elif len(url) > 0 and url[0]:
                    print('it is playing!' +' '+self.fmt_aid)
                    self.urls[url[0]] = dict()
                    print('got url!' +' '+self.fmt_aid)
                    self.proxy.stop_player()
                    print('stopped player!' +' '+self.fmt_aid)
                    video = self.save_found_video(url)
                    if video:
                        self.urls[url[0]] = video
                    print('saved video info!' +' '+self.fmt_aid)
                self.restart_kodi()
                return True
            t = time.time() + 4
            while (self.proxy.in_simple_dialog() or self.proxy.is_playing()) \
                    and t > time.time():
                if self.proxy.is_playing():
                    print('it is playing!' +' '+self.fmt_aid)
                    url = self.proxy.get_video_url()
                    print('got url!' +' '+self.fmt_aid)
                    self.proxy.stop_player()
                    print('stopped player!' +' '+self.fmt_aid)
                    self.save_found_video(url)
                    print('saved video info!' +' '+self.fmt_aid)
                    return True
                elif self.proxy.in_simple_dialog():
                    self.proxy.escape_dialog()
            print('not video :(')
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
            if 'Errno 111' in estr or 'Connection refused' in estr:
                self.kodi_crashes += 1
                self.restart_kodi()
            else:
                raise
        return False

    def save_performance(self):
        t = time.time()
        with open(self.datadir+'performance.json', 'w+') as f:
            json.dump({'runtime': t-self.start_time,
                'restarts': self.restarts,
                'init_time': self.init_time,
                'stop_time': t,
                'rpc_durations': self.rpc_durations,
                'clicks': self.clicks,
                'install_timeframe': self.install_timeframe,
                'play_pages': list(self.play_pages)
                }, f)

    def stop_kodi(self):
        print('stopping kodi (in func)')
        client = docker.from_env()
        ran = True
        for i in range(2):
            try:
                container = client.containers.get(self.container_name)
                container.exec_run(self.container_home_path+'.kodi/addons/service.api.dekodi/kill_kodi.sh',
                        detach=True)
                ran = True
                break
            except docker.errors.APIError as e:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                print("TRYING AGAIN!!!!!")
        if not ran:
            print('failed to stop '+self.aid)
            return False
        for i in range(2):
            try:
                out = container.exec_run('ps aux')
                break
            except:
                pass
        t = time.time()
        while 'kodi-x11' in out and time.time() - t < 10:
            for i in range(2):
                try:
                    out = container.exec_run('ps aux')
                    break
                except:
                    pass
            time.sleep(0.1)
        return True

    def get_root_name(self, aid=None):
        if aid is None:
            aid = self.aid
        if '/' in aid:
            aid = aid.split('/')[-1]
        if '.zip' in aid:
            aid = aid.split('.zip')[0]
            return aid
        if len(aid) > 50:
            aid = aid[:50]
        return aid

    def is_installed(self, aid=None, zipcheck=False):
        ''' NOTE: only checks for NEWLY installed add-ons (not previously installed'''
        '''NOTE: this is only interested in the main aid of the crawler'''
        try:
            if aid is None:
                aid = self.aid
            print('checking if '+aid+' is installed...')
            if '.zip' in aid or zipcheck:
                if aid[0] == '/':
                    aid = aid.split('/')[-1]
                aid = aid.split('.zip')[0].lower()
                print("checking if "+aid+" is installed")
                changes = self.get_addon_changes()
                print(changes['new_installed'].keys())
                if len(changes['new_installed'].keys()) == 0:
                    return False
                flataid = flatten_name(aid)
                print(flataid)
                if len(flataid) == 0:
                    return False
                for addon in changes['new_installed'].keys():
                    lower = flatten_name(addon)
                    if len(lower):
                        if flataid in lower or lower in flataid:
                            if aid == self.aid or aid == os.path.basename(self.aid):
                                self.real_aid = addon
                            return addon
                        else:
                            return False
            else:
                if aid == self.aid:
                    self.real_aid = self.aid
                return self.proxy.is_installed(aid)
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
            return False

    def get_new_install_keys(self):
        client = docker.from_env()
        script = self.container_home_path+'.kodi/addons/service.api.dekodi/hashit.py'
        addons = self.container_home_path+'.kodi/addons/'
        output = self.container_home_path+'output/new_install_keys.json'
        ran = False
        for i in range(2):
            try:
                container = client.containers.get(self.container_name)
                container.exec_run(script+' '+addons+' '+output)
                ran = True
                break
            except docker.errors.APIError as e:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
                self.docker_issue = estr
        if not ran:
            raise Exception(estr)
        t = time.time()
        keys = list()
        while time.time() - t < 10:
            try:
                with open(self.outputdir+'new_install_keys.json','r') as f:
                    keys = [tuple(z[:-1]+[self.image, z[-1]]) for z in json.loads(f)]
                break
            except:
                time.sleep(0.1)
        return keys

    def stop_container(self):
        print('stopping container (in func)')
        client = docker.from_env()
        timeout = 30 + time.time()
        try:
            container = client.containers.get(self.container_name)
            for i in range(2):
                try:
                    container.exec_run(self.container_home_path+'.kodi/addons/service.api.dekodi/stop_tstat.sh')
                    break
                except:
                    pass
            time.sleep(0.1)
            try:
                container.reload()
            except:
                pass
            try:
                container.kill()
            except:
                pass
            try:
                container.stop()
            except:
                pass
            try:
                container.remove()
            except:
                pass
            while timeout > time.time() and 'removed' not in container.status:
                time.sleep(0.1)
                try:
                    container.reload()
                except Exception as e:
                    if '404' in str(e):
                        pass
                    else:
                        print(e)
                    break
        except Exception as e:
            if '404' in str(e):
                pass
            else:
                print(e)
        print('done stopping '+self.fmt_aid)
        return True

    def save_log(self):
        '''
        client = docker.from_env()
        try:
            container = client.containers.get(self.container_name)
            container.exec_run('cp -r ' \
                    +self.container_home_path+'.kodi/temp/kodi.log ' \
                    +self.container_home_path+'output/' \
                    +str(time.time()).split('.')[0]+'.log')
        except Exception as e:
            print(e)
        '''
        pass

    def get_dependencies(self):
        reqs = set()
        try:
            top = self.zip_addondir+'/tmpdepdir/'
            for r, _, files in os.walk(top):
                if 'addon.xml' in files:
                    fname = r+'/addon.xml'
                    root = xee.parse(fname).getroot()
                    for elem in root.iter():
                        if elem.tag == 'import':
                            reqs.add((elem.get('addon'), elem.get('version')))
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        return list(reqs)

    def save_output(self):
        if self.real_aid and not os.path.isfile(self.datadir+'real_aid.txt'):
            with open(self.datadir+'real_aid.txt', 'w') as f:
                f.write(self.real_aid)
        if self.missing_reqs and not os.path.isfile(self.datadir+'missing_reqs.json'):
            with open(self.datadir+'missing_reqs.json', 'w') as f:
                json.dump(self.missing_reqs)
        client = docker.from_env()
        try:
            ran = False
            for i in range(2):
                try:
                    container = client.containers.get(self.container_name)
                    container.exec_run('cp -r ' \
                            +self.container_home_path+'.kodi/userdata/Database/Addons27.db ' \
                            +self.container_home_path+'output/')
                    container.exec_run('python '+self.container_home_path+'.kodi/addons/service.api.dekodi/scan_xmls.py', detach = True)
                    ran = True
                    break
                except:
                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                if not ran:
                    return False
            self.save_log()
        except Exception as e:
            print(e)
            return False

    def remove_container(self):
        client = docker.from_env()
        try:
            container = client.containers.get(self.container_name)
            container.kill()
        except:
            pass
        try:
            container = client.containers.get(self.container_name)
            container.remove()
        except:
            pass

    @property
    def unstuck_key(self):
        return ''.join(str(self.proxy.get_state()).split())

    def get_unstuck(self, arrival_sequence):
        '''
        key = self.unstuck_key # store solutions in hash indexed by keys
        success = False
        try:
            with open(self.unstuckdir, 'r+') as f:
                data = json.load(f)
        except IOError:
            data = dict()
        if key in data:
            data[key]['hits'] += 1
            if len(data[key]['unstuck_sequences']) > 0:
                for ind, sequence in enumerate(data[key]['unstuck_sequences']):
                    if self.do_sequence(sequence, self.server):
                        success = True
                        data[key]['success'][ind] += 1
                        break
                    elif self.unstuck_key != key:
                        break
        else:
            data[key] = {'arrival_sequence': arrival_sequence,
                    'unstuck_sequences': [], 'hits': 1, 'success': []}
        with open(self.unstuckdir, 'w+') as f:
            json.dump(data, f)

        return success
        '''
        self.get_screenshot('crawler_stuck.png')
        return False

    def do_sequence(self, seq):
        for button in seq[:-1]:
            if type(button[0]) is str:
                button = button[1:]
            if not self.proxy.set_focus_and_select(button):
                return False
        return self.proxy.get_state() == seq[-1]


#***************************************************
# helper functions
#***************************************************

def format_label_text(text):
    return ''.join(text.replace('/', '_').split())

def get_location(ip):
    try:
        try:
            from geoip import geoiplite2 as geolite2
        except:
            from geoip import geolite2
        GEOIP = geolite2.lookup(ip).to_dict()
        if GEOIP is None:
            GEOIP = {}
        else:
            GEOIP['subdivisions'] = list(GEOIP['subdivisions'])
    except:
        GEOIP = {}
    return GEOIP


def get_video_info(url):
    cmnd =  ['timeout', '10', 'ffprobe', '-timeout', '10000000', '-show_format', '-pretty', '-loglevel', 'quiet', url[0]]
    p = subprocess.Popen(cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    parsed_uri = urlparse(url[0])
    try:
        ip_url = socket.gethostbyname(parsed_uri.netloc)
    except:
        ip_url = ''
    curl_url = url[0].split("|")[0]
    cmd = 'timeout 10 curl -I ' + curl_url
    p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out2, err2 = p.communicate()
    return {
            'ip': ip_url,
            'netloc': parsed_uri.netloc,
            'geoip': get_location(ip_url),
            'headers': out2,
            'herrs': err2,
            'ffprobe_out': out
            }


def correct_video_data(video):
    try:
        if type(video['url']) is list:
            URL = video['url'][0]
            video['url'] = URL
        else:
            URL = video['url']
        if (URL.startswith('http') and video['headers']) or URL.startswith('rtmp'):
            video['real_url'] = URL
            if not video['geoip'] or type(video['geoip']) is not dict:
                video['geoip'] = get_location(video['ip'])
            return video
        if '%' in URL and not video['headers']:
            url = URL
            if 'url=' in url:
                url = [z for z in url.split('&') if 'url=' in z][0].split('url=')[1]
            try:
                url = urllib.unquote(url).decode('utf8')
            except:
                video['real_url'] = URL
                return video
            video['real_url'] = url
            if url.startswith('https://') or url.startswith('http://') :
                try:
                    video.update(get_video_info(url))
                except:
                    pass
                return video
        if '%' in URL and not video['headers']:
            url = URL
            if '=http' in url:
                url = [z for z in url.split('&') if '=http' in z][0].split('=http')[1]
                url = 'http' + url
            try:
                url = urllib.unquote(url).decode('utf8')
            except:
                video['real_url'] = URL
                return video
            video['real_url'] = url
            if url.startswith('https://') or url.startswith('http://') :
                try:
                    video.update(get_video_info(url))
                except:
                    pass
                return video
        if '://' in URL:
            video['real_url'] = URL
            return video
        video['real_url'] = ''
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return video


def do_ffprobe(video):
    if 'real_url' not in video:
        return video
    URL = video['real_url']
    outs = list()
    for url in URL.split():
        cmnd =  ['timeout', '10', 'ffprobe', '-timeout', '10000000', '-print_format', 'json', '-show_streams', '-show_format', '-pretty', '-loglevel', 'quiet', url]
        print('p1')
        p = subprocess.Popen(cmnd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        print(out)
        print(err)
        outs.append(out)
    video['probes'] = outs
    return video


def proc_url(url):
    print('urlparse')
    parsed_uri = urlparse(url[0])
    try:
        print('geoip')
        ip_url = socket.gethostbyname(parsed_uri.netloc)
        GEOIP = get_location(ip_url)
    except:
        ip_url = ''
        GEOIP = {}
    curl_url = url[0].split("|")[0]
    cmd = 'timeout 10 curl -I ' + curl_url
    p = subprocess.Popen(cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out2, err2 = p.communicate()
    print('URL: '+url[0])
    print('SUBP OUT: '+str(out2))
    print('SUBP ERR: '+str(err2))
    return {
            'url': url,
            'ip': ip_url,
            'netloc': parsed_uri.netloc,
            'geoip': str(GEOIP),
            'headers': out2,
            'herrs': err2,
            }

def destroy_image(image):
    print('destroying image '+image)
    try:
        client = docker.from_env()
        client.images.remove(image, force=True)
        return True
    except Exception as e:
        print(e)
        return False

def flatten_name(aid):
    aid = re.sub('[0-9]+', '', aid).lower()
    if '/' in aid:
        aid = os.path.basename(aid)
    aidl = '_'.join([z for z in aid.split('.') if z not in ['video', 'kodi', 'xbmc', 'audio', 'plugin', 'script', 'skin', 'repo', 'repository', 'zip'] and len(z) > 0])
    if len(aidl) == 0:
        return aid
    aidl2 = '_'.join([z for z in aidl.split('-') if z not in ['video', 'kodi', 'xbmc', 'audio', 'plugin', 'script', 'skin', 'repo', 'repository'] and len(z) > 0])
    if len(aidl2) == 0:
        return aidl
    aidl = ''.join([z for z in aidl2.split('_') if z not in ['video', 'kodi', 'xbmc', 'audio', 'plugin', 'script', 'skin', 'repo', 'repository'] and len(z) > 0])
    if len(aidl) == 0:
        return aidl2
    return aidl
