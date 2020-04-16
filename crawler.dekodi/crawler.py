from __future__ import print_function
import sys
import xmlrpclib
import socket
from random import shuffle
from state_helpers import *
from collections import defaultdict
import multiprocessing
from itertools import izip, repeat, cycle, imap
from os.path import basename
import docker
import urllib
import zipfile
import shutil
import gitcrawl
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
try:
    import json
except:
    import simplejson as json
import time
import argparse
import traceback
import hashit
import sqlite3
import subprocess
import datetime

#***************************************************
# naming conventions
#***************************************************
# aid: add-on id
# crawler: instance of KodiCrawler
# g_*: global

#***************************************************
# global variables
#***************************************************

new_images = defaultdict(dict)
threads = list()

#***************************************************
# macro tasks
#***************************************************

def errout(e):
    print(e)
    with open('erroroutdata.txt','a') as f:
        f.write(e+'\n-----------------------\n')


def zip_is_addon(path):
    try:
        zf = zipfile.ZipFile(path)
        for f in zf.namelist():
            if f.endswith('addon.xml'):
                return f
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return False

def is_zip(addon):
    if 'is_zip' in addon and addon['is_zip']:
        return True
    return False

def zip_deps(addon):
    if 'zip_deps' in addon and addon['zip_deps']:
        return addon['zip_deps']
    return list()

def needs_aid(addon):
    if 'needs_aid' in addon and addon['needs_aid']:
        return True
    return False

def install_addon(crawler, aid=None, baid=None, addon_data=None):
    if not addon_data:
        addon_data = crawler.addon_data
    if not aid:
        aid = addon_data['aid']
    if not baid:
        baid = basename(aid)
    print(addon_data)
    print('installing '+aid)
    t0 = time.time()
    if is_zip(addon_data):
        for dep in zip_deps(addon_data):
            install_addon(crawler, addon_data=dep)
        success = crawler.install_addon_from_zip(addon_data['fpath'])
        aid = aid.split('/')[-1]
        print('post install state '+str(crawler.get_state())+' for '+baid)
    else:
        crawler.update_addons_list()
        success = crawler.install_addon(aid)
        print('post install state '+str(crawler.get_state())+' for '+baid)
    if not success:
        time.sleep(1)
        timeout = time.time() + 10
        added = 0
        wasdownloading = True
        while timeout + added > time.time():
            timeleft = timeout + added - time.time()
            print('continuing installation...'+str(timeleft)+' for '+baid)
            try:
                if not crawler.is_installed(baid):
                    print(str(crawler.get_state())+' for '+baid)
                    if crawler.in_installation_dialog(aid):
                        found, button = crawler.find_confirm_button()
                        if found:
                            print(str(crawler.set_focus_and_select(button))+' for '+baid)
                        if added < 50:
                            added += 5
                    else:
                        time.sleep(3)
                else:
                    print('installed '+baid)
                    success = True
                    break
            except Exception as e:
                if 'Connection refused' in str(e):
                    t1 = time.time()
                    crawler.set_install_timeframe(t0, t1)
                    crawler.save_output()
                    return False
            time.sleep(0.1)
            if added < 50 and len(crawler.is_downloading()) > 1:
                added += 5
                wasdownloading = True
            elif wasdownloading:
                wasdownloading = False
        print(timeout+added - time.time())
        if not success and not needs_aid(addon_data):
            if not crawler.get_unstuck('install_addon'):
                print('failed to install '+baid)
                return False
        elif not success and needs_aid(addon_data):
            print('not sure if zip installed ('+aid+')')
    t1 = time.time()
    crawler.set_install_timeframe(t0, t1)
    crawler.save_output()
    print('-------------------------------')
    newaddons = crawler.get_addon_changes()['new_installed']
    if not newaddons and not success:
        new_to_install_addons = crawler.get_addon_changes()['new_installed']
        if not new_to_install_addons and not success:
            print('failed to install '+baid)
            return False
        print(baid+': not-installed new addons: '+str(new_to_install_addons))
    print(str(newaddons)+': '+baid)
    print('-------------------------------')
    for a in newaddons:
        crawler.enable_addon(a)
    print('done installing '+baid)
    return True


def download_file(d, url):
    print(url)
    local_filename = d+'tmpfile.zip'
    is_404 = False
    if os.path.exists(local_filename):
        print('removing old file')
        try:
            os.remove(local_filename)
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
    headers = {'User-Agent': "Kodi/18.0-BETA4 (X11; Linux x86_64) Ubuntu/16.04 App_Bitness/64 Version/18.0-BETA4-Git:20181008-3f3d68b"}
    print(headers)
    try:
        with requests.get(url, headers=headers, stream=True) as r:
            print('...')
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk: # filter out keep-alive new chunks
                        f.write(chunk)
                        # f.flush()
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
        if '404 Client Error' in estr:
            is_404 = True
    return local_filename, is_404


def run_addon(crawler):
    try:
        aid = crawler.real_aid
        print('running addon '+aid)
        if not crawler.run_addon(aid):
            if not crawler.run_addon(aid): # try twice (in case of setup menu)
                if not crawler.get_unstuck('run_addon'):
                    print('failed to run '+aid)
                    return False
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return True


def get_repo_addons(crawler):
    ret = list()
    try:
        conn = sqlite3.connect(crawler.outputdir+'/Addons27.db')
        c = conn.cursor()
        links = list(c.execute('select * from addonlinkrepo'))
        repos = {str(z[0]): z[1] for z in list(c.execute('select * from repo'))}
        addons = {str(z[0]):z[2] for z in list(c.execute('select * from addons'))}
        addon_info = {z['addonid']: z for z in crawler.get_all_addons_info()}
        reposet = set()
        for rtid, atid in links:
            try:
                repo = repos[str(rtid)]
                reposet.add(repo)
            except:
                print('repo id missing: '+str(rtid))
                continue
            try:
                aid = addons[str(atid)]
            except:
                print('addon id missing: '+str(atid))
                time.sleep(5)
                continue
            try:
                z = addon_info[aid]
            except:
                print('addon data missing: '+str(aid))
                continue
            try:
                if repo == crawler.addon_data['aid']:
                    ret.append(build_addon_data(z, sources=[crawler.addon_data['aid']]))
            except Exception as e:
                print(e)
        print(reposet)
    except Exception as e:
        print('ERROR GETTING REPO ADDONS LIST: '+str(e))
    return ret


def get_origin_repo(crawler, myaid):
    ret = ''
    try:
        conn = sqlite3.connect(crawler.outputdir+'/Addons27.db')
        c = conn.cursor()
        links = list(c.execute('select * from addonlinkrepo'))
        repos = {str(z[0]): z[1] for z in list(c.execute('select * from repo'))}
        addons = {str(z[0]):z[2] for z in list(c.execute('select * from addons'))}
        for rtid, atid in links:
            try:
                repo = repos[str(rtid)]
            except:
                print('repo id missing: '+str(rtid))
                continue
            try:
                aid = addons[str(atid)]
            except:
                print('addon id missing: '+str(atid))
                time.sleep(5)
                continue
            if myaid == aid:
                return repo
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return ret


def build_addon_data(z, **kwargs):
    print(z)
    ret = {'aid': z['addonid'],
                'v': str(z['version']) if str(z['version']) else str(-1),
                'deps': z['dependencies'], 'type': [z['type']],
                'content': get_content_type(z['extrainfo']),
                'extra': get_other_info(z['extrainfo']),
                'name': z['name'], 'disclaimer': z['disclaimer'], 'path': z['path'],
                'sources': [], 'images': [], 'seeds': [], 'description': z['description'],
                'summary': z['summary']}
    ret.update(kwargs)
    return ret

def post_install_handler(crawler, e=None):
    out = dict()
    try:
        print('START crawl done; collecting '+crawler.fmt_aid)
        out['new_image'] = None
        out['old_image'] = crawler.image
        out['addon'] = crawler.aid
        out['changes'] = list()
        out['installed'] = False
        out['name'] = crawler.container_name
        out['screen'] = crawler.screen_identifier
        proxy = xmlrpclib.ServerProxy(g_control_server)
        crawler.addon_data['data_path'] = [crawler.relative_data_path]
        crawler.addon_data['last_keys'] = [crawler.get_last_key()]
        if crawler.docker_issue:
            crawler.addon_data['docker'] = crawler.docker_issue
        if crawler.kodi_crashes:
            crawler.addon_data['crashes'] = crawler.kodi_crashes
        if crawler.urls:
            crawler.addon_data['plays'] = list(crawler.urls)
            crawler.addon_data['keys2vid'] = crawler.keys_to_first_video
        out['addon_data'] = crawler.addon_data
        try:
            crawler.restart_kodi()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            if crawler.is_installed(crawler.aid) or \
                    ('aid' in crawler.addon_data and \
                    crawler.is_installed(crawler.addon_data['aid'])) or \
                    ('name' in crawler.addon_data and \
                    crawler.is_installed(crawler.addon_data['name'])):
                out['installed'] = True
                crawler.addon_data['installs'] = 1
                try:
                    crawler.update_addons_list()
                except:
                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                    errout(estr)
                new_image_name = 'dekodirepo'
                image_tag = crawler.get_root_name(out['addon'])
                out['new_image'] = crawler.fmt_image(new_image_name + ':' + image_tag)
                changes = crawler.get_system_state_changes()
                installs = changes['new_installed']
                if crawler.real_aid and crawler.real_aid in installs \
                        and 'needs_aid' in crawler.addon_data and crawler.addon_data['needs_aid']:
                            try:
                                addon_info = crawler.get_addon_info(crawler.real_aid)
                                addon_data = build_addon_data(addon_info,
                                        sources=crawler.addon_data['sources'],
                                        seeds=crawler.addon_data['seeds'],
                                        images=crawler.addon_data['images'])
                                orepo = get_origin_repo(crawler, crawler.real_aid)
                                if orepo:
                                    addon_data['origin_repo'] = orepo
                                crawler.addon_data.update(addon_data)
                            except:
                                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                                errout(estr)
                crawler.save_output()
                addon_data = {'images': [out['new_image']]}
                if crawler.real_aid and crawler.real_aid in installs \
                        and installs[crawler.real_aid] == 'xbmc.addon.repository':
                    crawler.restart_kodi()
                    crawler.get_addons_list()
                    crawler.addon_data['addons'] = get_repo_addons(crawler)
                    [z.update(addon_data) for z in crawler.addon_data['addons']]
                    out['changes'] = json.loads(proxy.keep_checking(json.dumps(crawler.addon_data['addons'])))
            else:
                crawler.addon_data['fails'] = 1
            try:
                if 'deps' in crawler.addon_data and crawler.addon_data['deps']:
                    all_istalled = {(z['addonid'], z['version']) \
                            for z in crawler.get_installed_addons_info()}
                    out['missing'] = list()
                    try:
                        for d in crawler.addon_data['deps']:
                            if (d['addonid'], d['version']) not in all_istalled:
                                out['missing'].append(d)
                    except:
                        estr = ''.join(traceback.format_exception(*sys.exc_info()))
                        errout(estr)
            except Exception as p:
                print(p)
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
            if 'missing' in out and out['missing']:
                crawler.addon_data['missing'] = out['missing']

        except Exception as e:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
            if '111]' not in str(e):
                raise e
        try:
            crawler.save_system_state_changes()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        crawler.save_performance()
        print('RETURNING '+crawler.fmt_aid)
        proxy.done_data(json.dumps([crawler.addon_data]))
        out['addon_data'] = crawler.addon_data
        print("DONE SENDING DONE")
        crawler.stop_container()
        return out
    except Exception as E:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
        print(E)
        try:
            crawler.save_system_state_changes()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.save_output()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.save_performance()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.stop_container()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            proxy.done_data(json.dumps([crawler.addon_data]))
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
    return out

def crawl_wrapper(cargs, ckwargs, args, kwargs):
    proxy = xmlrpclib.ServerProxy(g_control_server)
    if 'is_in_default' in ckwargs['addon_data'] and ckwargs['addon_data']['is_in_default']:
        return {}
    if not proxy.starting_crawl(json.dumps(ckwargs['addon_data'])):
        return {}
    crawler = KodiCrawler(*cargs, **ckwargs)
    try:
        try:
            print('START CRAWL')
            crawl_video_addon(crawler, *args, **kwargs)
            print('STOP CRAWL')
        except Exception as e:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        print('START crawl done; collecting '+crawler.fmt_aid)
        return post_install_handler(crawler)
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
        try:
            return post_install_handler(crawler)
        except Exception as p:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.save_system_state_changes()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.save_output()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.save_performance()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            proxy = xmlrpclib.ServerProxy(g_control_server)
            proxy.done_data(json.dumps([crawler.addon_data]))
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        try:
            crawler.stop_container()
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)

    return post_install_handler(crawler)



def crawl_video_addon(crawler, timeout=300, clicksperpage=15, waitforvideo=4,
        restartsperpage=5, clickspername=2, bfs=False):
    print('installing '+crawler.aid+'...')
    if not install_addon(crawler):
        print('failed to install...')
        return False
    newaddons = crawler.get_addon_changes()['new_installed']
    if 'xbmc.python.pluginsource' not in newaddons.values():
        return False
    if not run_addon(crawler):
        return False
    # escape any initial dialogs
    print('escaping dialogs')
    if not crawler.escape_dialog():
        print(crawler.get_state())
        if not crawler.get_unstuck('run_addon'):
            print(crawler.get_state())
            return False
    # make sure we're actually on the add-on page
    if crawler.real_aid not in crawler.get_current_path():
        if not crawler.get_unstuck('run_addon'):
            return False
    # the estuary skin defaults to putting all the items we care about in
    # container with id 55
    print('setting focus on list')
    if not crawler.set_focus(55):
        if not crawler.get_unstuck('set_focus:55'):
            return False
    window = crawler.get_full_window()
    window_items = crawler.get_container_items(55)
    # clickables: [window, button, crawl sequence]
    clickables = [[window, z, []] for z in window_items]
    if not bfs:
        shuffle(clickables) # shuffle to reduce odds of hitting parent/next btn
    print('top level buttons count: '+str(len(clickables)))
    print(clickables)
    if len(clickables) < 3:
        # make sure it loaded properly
        crawler.home()
        if not run_addon(crawler):
            return False
        # escape any initial dialogs
        print('escaping dialogs')
        if not crawler.escape_dialog():
            print(crawler.get_state())
            if not crawler.get_unstuck('run_addon'):
                print(crawler.get_state())
                return False
        # make sure we're actually on the add-on page
        if crawler.real_aid not in crawler.get_current_path():
            if not crawler.get_unstuck('run_addon'):
                return False
        # the estuary skin defaults to putting all the items we care about in
        # container with id 55
        print('setting focus on list')
        if not crawler.set_focus(55):
            if not crawler.get_unstuck('set_focus:55'):
                return False
        window = crawler.get_full_window()
        window_items = crawler.get_container_items(55)
        # clickables: [window, button, crawl sequence]
        clickables = [[window, z, []] for z in window_items]
        if not bfs:
            shuffle(clickables) # shuffle to reduce odds of hitting parent/next btn
        print('top level buttons count: '+str(len(clickables)))
        print(clickables)
    timeout = time.time() + timeout
    maxtimeout = timeout + 300
    tries = 0
    maxtries = 2
    visited = set()
    clicks = defaultdict(lambda: 0)
    nameclicks = defaultdict(lambda: 0)
    restarts = defaultdict(lambda: 0)
    crawler.play_pages = set()
    # keeps doing nearly depth first crawl until it runs out of time or places to crawl
    while len(clickables) > 0 and \
            min([maxtimeout, timeout + 60*len(crawler.play_pages)])> time.time():
        try:
            print('remaining: '+str(len(clickables)))
            print('time left: '+str(min([maxtimeout, timeout + 60*len(crawler.play_pages)])-time.time()))
            current = clickables.pop()
            # format button label so it doesn't break anything
            current[1][0] = format_label_text(current[1][0])
            button = current[1]
            # make sure we aren't on the parent folder button
            if button[2] < 2 and button[0] in ['..', '[..]']:
                tries = 0
                continue
            if clicks[tuple(current[0])] >= clicksperpage:
                tries = 0
                continue
            if nameclicks[current[1][0]] >= clickspername:
                tries = 0
                continue
            tries += 1
            crawler.reset_actions_list()
            if not crawler.home():
                if not crawler.get_unstuck(current[2]):
                    time.sleep(2)
                    tries = 0
                    clickables.append(current)
                    continue
            if not crawler.activate_window(*current[0]):
                if not crawler.get_unstuck(current[2]):
                    time.sleep(2)
                    clickables.append(current)
                    continue
            if not crawler.set_focus(55)[0]:
                if not crawler.get_unstuck(current[2]):
                    time.sleep(2)
                    if not crawler.set_focus(55)[0]:
                        tries = 0
                        continue
            visited.add(tuple(current[0]))
            # press the button
            print("pressing "+button[0])
            crawler.store_button_info(button[1:])
            change = crawler.set_focus_and_select(button[1:], waitforvideo)
            try:
                if crawler.is_video():
                    print('video found on '+str(button))
                    crawler.play_pages.add(tuple(current[0]))
                elif change < 2:
                    if not crawler.get_unstuck(current[2]):
                        # catch case where button does nothing
                        # this could be something bugging out, so try restarting kodi
                        print('select resulted in no apparent change')
                        # catch buggy state
                        if tries < maxtries and \
                                restarts[tuple(current[0])] < restartsperpage:
                            clickables.append(current)
                            if not crawler.restart_kodi():
                                break
                            restarts[tuple(current[0])] += 1
                            continue
                elif crawler.in_dialog():
                    if not crawler.get_unstuck(current[2]):
                        print('got stuck in dialog')
                        # if we're in a new dialog, capture it
                        crawler.set_stuck_flag()
                else:
                    window = crawler.get_full_window()
                    if window != current[0] and crawler.set_focus(55) \
                            and tuple(window) not in visited:
                        # if we are in a new place that's not a dialog, push it to stack
                        print('found new window')
                        items = crawler.get_container_items(55)
                        seq = current[2]+[current[1]]
                        for item in items:
                            clickables.append([window, item, seq])
                        if not bfs:
                            shuffle(clickables)
                    else:
                        crawler.get_unstuck(current[2])
            except (xmlrpclib.Fault, socket.error) as e: # catch when kodi restarts
                print(str(e))
                if tries < maxtries and \
                        restarts[tuple(current[0])] < restartsperpage:
                    clickables.append(current)
                    if not crawler.restart_kodi():
                        break
                    restarts[tuple(current[0])] += 1
                    continue
                elif not crawler.verify_kodi_state():
                    return None
        except Exception as e:
            print(e)
            crawler.restart_kodi()
            if tries < maxtries:
                clickables.append(current)
                continue
        nameclicks[current[1][0]] += 1
        clicks[tuple(current[0])] += 1
        tries = 0
    print('completed video crawl')
    print(clickables)
    #crawler.restart_kodi()
    time.sleep(10)


def just_install(crawler):
    addon = crawler.aid
    print('installing generic: '+addon)
    try:
        install_addon(crawler)
        run_addon(crawler)
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    print('ending generic addon install')


def check_zip_path((path, cid, image, screen_identifier,kwargs)):
    print(str((path, cid, image, screen_identifier, kwargs)))
    try:
        crawler = KodiCrawler(cid, container_name=cid, image=image,
                screen_identifier=screen_identifier,  **kwargs)
        crawler.follow_path(path)
        name = crawler.get_current_position()[0]
        crawler.select()
        crawler.update_addons_list()
        if 10126 not in crawler.get_state():
            ret = (True, path, name)
        else:
            ret = (False, path, {z[0] for z in crawler.get_container_items(450, 'FolderPath') \
                            if '..' not in z[0] and len(z[0])>0})
        crawler.stop_container()
    except Exception as e:
        try:
            print('Hit exception: '+str(e)+'; retrying '+path)
            crawler = KodiCrawler(cid, container_name=cid, image=image,
                    screen_identifier=screen_identifier, **kwargs)
            crawler.follow_path(path)
            name = crawler.get_current_position()[0]
            crawler.select()
            crawler.update_addons_list()
            if 10126 not in crawler.get_state():
                ret = (True, path, name)
            else:
                ret = (False, path, {z[0] for z in crawler.get_container_items(450, 'FolderPath') \
                                if '..' not in z[0] and len(z[0])>0})
            crawler.stop_container()
        except Exception as e:
            print('Failed to do '+path+'; '+str(e))
            ret = (False, path, {})
    print('wut')
    return ret


def crawl_source(url='https://lazykodi.com', name=None, num_crawlers=3,
        initial_image='dekodi_build5',
        container_home_path='/home/dekodi/', **kwargs):
    addons = dict()
    if os.path.exists('addon_sources.json'):
        with open('addon_sources.json', 'r') as f:
            for line in f:
                if line:
                    tmp = json.loads(line).items()
                    aid, data = tmp[0]
                    addons[aid] = data
    if name is None:
        name = url.split('//')[-1].replace('/','').replace('.','')
    kwargs['container_home_path'] = container_home_path
    crawler = KodiCrawler(name, container_name='source_crawler0', image=initial_image,
            screen_identifier=2, **kwargs)
    if not crawler.add_source(url, name):
        return []
    image = crawler.fmt_image(initial_image.split(':')[0]+':'+name)
    crawler.make_addon_image(image)
    crawler.restart_kodi()
    visited = set()
    crawler.open_install_from_zip_window()
    paths = {z[0]: z[1:] for z in crawler.get_container_items(450)}
    for path in paths:
        if name in path:
            crawler.set_focus_and_select(paths[path])
            break
    paths = {z[0] for z in crawler.get_container_items(450,
        'FolderPath') if z[0].startswith(url)}
    crawler.stop_container()
    crawlers = ['source_crawler'+str(z) for z in range(2*num_crawlers)]
    pool = multiprocessing.Pool(processes=num_crawlers)
    vscreens = range(5, 10+(2*num_crawlers))
    zips = dict()
    while len(paths) > 0:
        print(paths)
        current_paths = {z for z in paths if z not in visited \
                and z.startswith(url)}
        bad_paths = [z for z in paths if not z.startswith(url)]
        print(current_paths)
        if len(bad_paths) > 0:
            print("BAD PATHS: "+str(bad_paths))
        visited = visited.union(current_paths)
        paths = set()
        for (addon, path, info) in pool.imap(check_zip_path,
                izip(current_paths, cycle(crawlers),
                    repeat(image),
                    cycle(vscreens), repeat(kwargs))):
                    print(str((addon, path, info)))
                    if addon:
                        zips[path] = ['from_source', url]
                    else:
                        paths = paths.union(info)
    addons.update(zips)
    return [{z[0]: z[1]} for z in addons.items()]


def from_source(crawler, path, url):
    proxy = xmlrpclib.ServerProxy(g_control_server)
    print('downloading zip...')
    dest, is_404 = download_file(crawler.zip_addondir, path)
    if is_404:
        print(path+' not found...')
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': url, 'path': path, 'why': '404 status'})+'\n')
        return "", []
    print('downloaded!')
    with zipfile.ZipFile(dest, 'r') as f:
        f.extractall(crawler.zip_addondir+'/tmpdir/')
    depth = 0
    for root, dirs, files in os.walk(crawler.zip_addondir+'/tmpdir/'):
        print('getting xml data')
        aid = ''
        version = ''
        addon_data = dict()
        try:
            if os.path.isfile(root+'/addon.xml'):
                aid = hashit.get_aid(root+'/addon.xml')
                if aid:
                    print(aid)
                    addon_data['aid'] = aid
                    addon_data['deps'] = list(hashit.get_dependencies(root+'/addon.xml'))
                    addon_data['atype'] = hashit.get_addon_types(root+'/addon.xml')
                    addon_data['content'] = hashit.get_content_types(root+'/addon.xml')
                    addon_data['name'] = hashit.get_name(root+'/addon.xml')
                    addon_data['v'] = hashit.get_version(root+'/addon.xml')
                    addon_data['is_zip'] = True
                    addon_data['needs_aid'] = False
                    addon_data['summary'] = hashit.get_summary(root+'/addon.xml')
                    addon_data['description'] = hashit.get_description(root+'/addon.xml')
                    addon_data['disclaimer'] = hashit.get_disclaimer(root+'/addon.xml')
                    addon_data.update(hashit.get_optional_stuff(root+'/addon.xml'))
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        if depth > 1 or addon_data:
            break
        depth += 1
    print('adding source')
    o = urlparse(path)
    crawler.add_source(url, o.netloc.split(':')[0])
    crawler.restart_kodi()
    image = crawler.fmt_image('dekodirepo:'+basename(path))
    print('new image: '+image)
    global new_images
    new_images[image]['created'] = True
    crawler.make_addon_image(image)
    addon = {'aid': basename(path), 'sources': [path, url], 'v': '-1', 'seeds': [url], 'images': [image], 'fpath': path,
            'is_zip': True, 'needs_aid': True}
    if addon_data:
        addon.update(addon_data)
    oldlist = crawler.get_addons_list().keys()
    if addon['aid'] in oldlist:
        addon['is_in_default'] = True
    addons = json.loads(proxy.keep_checking(json.dumps([addon])))
    return image, addons


def get_content_type(ei):
    ret = list()
    other = list()
    try:
        for item in ei:
            k = item['key']
            v = item['value']
            if k == 'provides':
                ret.append(v)
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return ret

def get_other_info(ei):
    ret = list()
    other = list()
    try:
        for item in ei:
            k = item['key']
            v = item['value']
            if k != 'provides':
                ret.append({k: v})
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return ret



def from_default(crawler, *args):
    crawler.save_output()
    proxy = xmlrpclib.ServerProxy(g_control_server)
    conn = sqlite3.connect(crawler.outputdir+'/Addons27.db')
    c = conn.cursor()
    print('getting addons from default')
    addons = [build_addon_data(z, sources=['defaultrepo'], seeds=['defaultrepo'], images=[crawler.image]) \
            for z in crawler.get_all_addons_info()]
    for z in addons:
        repo = get_origin_repo(crawler, z['aid'])
        if repo:
            z['sources'].append(repo)
    addons = json.loads(proxy.keep_checking(json.dumps(addons)))
    print(len(addons))
    print('got addons from default')
    return crawler.image, addons


def from_zip(crawler, info, url):
    proxy = xmlrpclib.ServerProxy(g_control_server)
    dest = crawler.zip_addondir+'/new_addon.zip'
    idest = crawler.container_zip_addondir+'new_addon.zip'
    if os.path.exists(dest):
        os.remove(dest)
    head = requests.head(url)
    # make sure the file actually still exists before wasting time on it
    if head.status_code > 399:
        print(url+' not found...')
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': info, 'path': url,
                'why': str(head.status_code)+' status'})+'\n')
        return "", []
    # don't download anything above 100MB
    try:
        cl = int(head.headers['Content-Length'])
    except:
        cl = -1
    downloaded = False
    if cl <= 100000000:
        print('downloading...')
        urllib.urlretrieve(url, dest)
        downloaded = True
        print('DOWNLOADED!!! '+dest)
    else:
        print(head.headers.items())
    if not downloaded:
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': info, 'path': url, 'why': 'too big'})+'\n')
        return "", []
    if not zip_is_addon(dest):
        print(url+' is not addon; moving on')
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': info, 'path': url, 'why': 'false positive'})+'\n')
        return '', []
    with zipfile.ZipFile(dest, 'r') as f:
        f.extractall(crawler.zip_addondir+'/tmpdir/')
    addons = list()
    walkpath = crawler.zip_addondir+'/tmpdir/'
    for root, dirs, files in os.walk(walkpath):
        aid = ''
        version = ''
        addon_data = dict()
        try:
            if os.path.isfile(root+'/addon.xml'):
                aid = hashit.get_aid(root+'/addon.xml')
                if aid:
                    dest = zipdir(root, crawler.zip_addondir+'/')
                    idest = crawler.container_zip_addondir+basename(dest)
                    addon_data['aid'] = aid
                    addon_data['deps'] = list(hashit.get_dependencies(root+'/addon.xml'))
                    addon_data['atype'] = hashit.get_addon_types(root+'/addon.xml')
                    addon_data['content'] = hashit.get_content_types(root+'/addon.xml')
                    addon_data['name'] = hashit.get_name(root+'/addon.xml')
                    addon_data['v'] = hashit.get_version(root+'/addon.xml')
                    addon_data['fpath'] = idest
                    addon_data['is_zip'] = True
                    addon_data['sources'] = [info]
                    addon_data['summary'] = hashit.get_summary(root+'/addon.xml')
                    addon_data['description'] = hashit.get_description(root+'/addon.xml')
                    addon_data['disclaimer'] = hashit.get_disclaimer(root+'/addon.xml')
                    addon_data.update(hashit.get_optional_stuff(root+'/addon.xml'))
                    if '_FRMRDT_' in info:
                        addon_data['seeds'] = ['reddit']
                    elif '_FRMGTHB_' in info:
                        addon_data['seeds'] = ['reddit']
                    else:
                        addon_data['seeds'] = [info]
                    print('found addon: '+aid)
        except:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
        if addon_data:
            addons.append(addon_data)
            # if the root is an addon, we can stop walking
            if root in walkpath:
                break
    if not addons:
        print('no addons found')
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': info, 'path': url, 'why': 'false positive'})+'\n')
        return '', []
    paths = {z['aid']: z for z in addons}
    o = urlparse(url)
    image = crawler.fmt_image('dekodirepo:'+o.netloc.split(':')[0])
    [z.update({'images': [image]}) for z in addons]
    oldlist = crawler.get_addons_list().keys()
    for addon in addons:
        addon['zip_deps'] = list()
        for dep in addon['deps']:
            if dep['addonid'] in paths and dep['addonid'] not in oldlist:
                addon['zip_deps'].append(paths[dep['addonid']])
        if addon['aid'] in oldlist:
            addon['is_in_default'] = True
    paths = {z['aid']: {'aid': z['aid'],
        'name': z['name'], 'fpath': z['fpath'],
                'is_zip': True, 'zip_deps': list(z['zip_deps']) if 'zip_deps' in z else []} for z in addons}
    for addon in addons:
        addon['zip_deps'] = list()
        for dep in addon['deps']:
            if dep['addonid'] in paths and dep['addonid'] not in oldlist:
                addon['zip_deps'].append(paths[dep['addonid']])
    print(addons)
    addons = json.loads(proxy.keep_checking(json.dumps(addons)))
    if not addons:
        return '', []
    global new_images
    new_images[image]['created'] = True
    crawler.make_addon_image(image)
    return image, addons


def zipdir(path, newroot=''):
    if path.endswith('/'):
        path = path[:-1]
    dest = newroot+basename(path)
    if os.path.exists(dest):
        dest += '_dk2_'+''.join(str(time.time()).split('.'))
    if dest != path:
        shutil.copytree(src=path, dst=dest)
    paths = set()
    dpath = '/'.join(dest.split('/')[:-1])
    with zipfile.ZipFile(dest+'.zip', 'w') as zf:
        # ziph is zipfile handle
        for root, dirs, files in os.walk(dest):
            for f in files:
                fpath = os.path.join(root, f)
                if fpath in paths:
                    print(fpath)
                else:
                    paths.add(fpath)
                zf.write(fpath, arcname=os.path.relpath(fpath, dpath))
    return dest+'.zip'


def zipdir_github(path, newroot='./'):
    if path.endswith('/'):
        path = path[:-1]
    files = os.listdir(path)
    out = list()
    for f in files:
        if f.endswith('.zip'):
            zpath = zip_is_addon(path+'/'+f)
            if not zpath:
                continue
            tmppath = 'tmpaddonsdir/'+str(time.time())+'/'
            with zipfile.ZipFile(path+'/'+f, 'r') as fh:
                fh.extractall(tmppath)
            dirs = zpath.split(tmppath.split('tmpdir/')[-1])[-1].split('addon.xml')[0]
            out += zipdir_github(tmppath+dirs, newroot)
            try:
                os.remove(tmppath)
            except:
                try:
                    shutil.rmtree(tmppath)
                except:
                    pass
    try:
        os.remove(tmpaddonsdir)
    except:
        try:
            shutil.rmtree(tmpaddonsdir)
        except:
            pass
    try:
        if os.path.isfile(path+'/addon.xml'):
            aid = hashit.get_aid(path+'/addon.xml')
            addon_data = dict()
            addon_data['aid'] = aid
            addon_data['deps'] = list(hashit.get_dependencies(path+'/addon.xml'))
            addon_data['atype'] = hashit.get_addon_types(path+'/addon.xml')
            addon_data['content'] = hashit.get_content_types(path+'/addon.xml')
            addon_data['name'] = hashit.get_name(path+'/addon.xml')
            addon_data['v'] = hashit.get_version(path+'/addon.xml')
            addon_data['is_zip'] = True
            addon_data['summary'] = hashit.get_summary(path+'/addon.xml')
            addon_data['description'] = hashit.get_description(path+'/addon.xml')
            addon_data['disclaimer'] = hashit.get_disclaimer(path+'/addon.xml')
            addon_data.update(hashit.get_optional_stuff(path+'/addon.xml'))
            dest = newroot+aid
        if os.path.exists(dest):
            try:
                shutil.rmtree(dest)
            except:
                pass
        shutil.copytree(src=path, dst=dest)
        destzip = dest+'_'+str(time.time()).replace('.','')+'.zip'
        addon_data['fpath'] = destzip
        dpath = '/'.join(dest.split('/')[:-1])
        with zipfile.ZipFile(destzip, 'w') as zf:
            for root, dirs, files in os.walk(dest):
                for f in files:
                    fpath = os.path.join(root, f)
                    zf.write(fpath, arcname=os.path.relpath(fpath, dpath))
        out.append(addon_data)
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
    return out


def from_github(crawler, gid, url):
    print('from github')
    proxy = xmlrpclib.ServerProxy(g_control_server)
    dest = crawler.zip_addondir+'/new_addon.zip'
    uzdest = crawler.zip_addondir+'/new_addon'
    repodir = crawler.zip_addondir+'/new_repo'
    image = crawler.fmt_image('dekodirepo:'+str(time.time()).split('.')[0])
    addons = list()
    for the_file in os.listdir(crawler.zip_addondir):
        file_path = os.path.join(crawler.zip_addondir, the_file)
        try:
            if os.path.isfile(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
    print('removed old dirs')
    # we don't download anything above 100MB
    try:
        fsize = int(gitcrawl.github_get(url.split('/zipball')[0]).json()['size'])
    except:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
        with open('fruitless_sources.list', 'a') as f:
            f.write(json.dumps({'source': info, 'path': url, 'why': estr})+'\n')
        return "", []
    downloaded = False
    if fsize <= 1000000:
        print('downloading...')
        urllib.urlretrieve(url, dest)
        downloaded = True
        print('DOWNLOADED!!! ' + dest)
    else:
        print(url+' file too large: '+str(fsize))
    if downloaded:
        proxy = xmlrpclib.ServerProxy(g_control_server)
        addon_paths = proxy.get_path(url)
        zref = zipfile.ZipFile(dest, 'r')
        zref.extractall(uzdest)
        zref.close()
        for addon_path in addon_paths:
            dests = zipdir_github(uzdest+'/'+os.listdir(uzdest)[0]+'/'+addon_path,
                    crawler.zip_addondir+'/')
            for tmpaddon in dests:
                dest2 = tmpaddon['fpath']
                print(dest2)
                if not zip_is_addon(dest2):
                    continue
                addon = crawler.container_zip_addondir + basename(dest2)
                tmpaddon['fpath'] = addon
                addons.insert(0,tmpaddon)
    if '_FRMRDT_' in gid:
        seed = 'reddit'
    elif '_FRMGTHB_' in gid:
        seed = 'github'
    else:
        seed = gid
    [addon.update({'seeds': [seed], 'sources': [url], 'images': [image]}) for addon in addons]
    oldlist = crawler.get_addons_list().keys()
    paths = {z['aid']: z for z in addons}
    # go through and interconnect dependencies
    for addon in addons:
        addon['zip_deps'] = list()
        for dep in addon['deps']:
            if dep['addonid'] in paths and dep['addonid'] not in oldlist:
                dep['addon_data'] = paths[dep['addonid']]
                addon['zip_deps'].append(paths[dep['addonid']])
    addons = json.loads(proxy.keep_checking(json.dumps(addons)))
    crawler.make_addon_image(image)
    new_images[image]['created'] = True
    return image, addons


def crawl_addon_set(control_server, num_crawlers=3, initial_image='dekodi_build5',
        container_home_path='/home/dekodi/', real_screen = True, bfs=False, **kwargs):
    pool = multiprocessing.Pool(processes=num_crawlers)
    first_image = initial_image
    if 'crawl_id' not in kwargs:
        kwargs['crawl_id'] = str(time.time()).split('.')[0]
    kwargs['container_home_path'] = container_home_path
    global new_images
    global threads
    crawled = set()
    completed = list()
    installed = list()
    serverworks = False
    keeploopin = 1
    startcrawltime = time.time()
    while keeploopin:
        subprocess.call('./fix_permissions.sh')
        #keeploopin = False
        keeploopin += 1
        print('NEXT CRAWL--------------------------------------------------')
        initial_image = first_image
        crawlers = ['kodi_crawler'+str(z) for z in range(num_crawlers)]
        # [MV] create array of vscreens
        if real_screen:
            vscreens = cycle([0 for z in range(num_crawlers)])
        else:
            vscreens = cycle(range(2, 98)) # vscreen-1 is locked for some weird reason...
            # [MV]
            print('starting '+crawlers[0] + ' with scren-id: ' +str(99))
        addons = []
        source = None
        try:
            #1/0
            proxy = xmlrpclib.ServerProxy(control_server)
            source, (method, extra) = proxy.next_source(g_myname)
            with open('mysources.list','a') as f:
                f.write(source+'\n')
            if source == 'DONE!!!':
                print('DONE!!!')
                return
            serverworks = True
            '''
            while 'github' not in method:
                source, (method, extra) = proxy.next_source(g_myname)
            '''
            print(source)
            print(method)
            print(extra)
            crawler = KodiCrawler(source, crawlers[0], initial_image, 99,
                    **kwargs)
            for the_file in os.listdir(crawler.zip_addondir):
                file_path = os.path.join(crawler.zip_addondir, the_file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(e)
            crawler.update_addons_list()
            initial_image, addons = globals()[method](crawler, source, extra)
            print(addons)
            if not initial_image:
                continue
        except Exception as (e):
            estr = ''.join(traceback.format_exception(*sys.exc_info()))
            errout(estr)
            continue
            keeploopin = False
            if not serverworks:
                crawler = KodiCrawler('initialcrawler', crawlers[0], initial_image, 99,
                        **kwargs)
                crawler.update_addons_list()
                initial_image, addons = from_default(crawler)
                source, method, extra = 'defaultkodi', 'defaultkodi', 'defaultkodi'
            else:
                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                errout(estr)
                continue
        if (source, initial_image) in crawled:
            kwargs['crawl_id'] = str(time.time()).split('.')[0]
        if source is not None:
            print('saving '+source+' to '+crawler.datadir+'addonsource.json')
            with open(crawler.datadir+'addonsource.json', 'w') as f:
                json.dump({'aid': crawler.aid, 'source': source}, f)
            kwargs['addonsource'] = source
        crawler.save_output()
        crawler.save_performance()
        crawler.stop_container()
        crawled.add((source, initial_image))
        all_addons = {(z['aid'], z['v']) for z in addons}
        print("THIS MANY ADDONS::: "+str(len(addons)))
        remaining_images = defaultdict(int)
        for addon in addons:
            remaining_images[addon['images'][0]] += 1
        addons = sorted(addons, key=lambda z: z['v'], reverse=True)
        print('stopping inital container')
        t = time.time()
        fails = 0
        prev1 = None
        prev0 = None
        while len(addons) > 0 or len(threads) > 0:
            if time.time() - t > 600:
                print('10 MINUTE UPDATE ++++++++++++++++++++++++++++++++++++++++++++++++++')
                if prev1 is not None and prev1 == threads:
                    pool.terminate()
                    pool = multiprocessing.Pool(processes=num_crawlers)
                    break

                print(threads)
                prev1 = prev0
                prev0 = threads
                t = time.time()
            if len(crawlers) > 0 and len(addons) > 0:
                print('addons remaining: '+str(len(addons)))
                tmp = addons.pop()
                addon = tmp['aid']
                image = tmp['images'][0]
                print(addon+' ****************** '+image)
                if image != initial_image and image in new_images:
                    if not new_images[image]['created']:
                        madecrawler = False
                        try:
                            tmpadn, tmpimg = new_images[image]['addon']
                            remaining_images[tmpimg] -= 1
                            crawler = KodiCrawler(tmpadn, 'tmp_crawler', tmpimg, next(vscreens),
                                    **kwargs)
                            madecrawler = True
                            crawler.update_addons_list()
                            install_addon(crawler)
                            crawler.make_addon_image(image)
                            new_images[image]['created'] = True
                            crawler.stop_container()
                        except Exception as e:
                            estr = ''.join(traceback.format_exception(*sys.exc_info()))
                            errout(estr)
                            if madecrawler:
                                try:
                                    crawler.stop_container()
                                except Exception as E:
                                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                                    errout(estr)
                            continue
                curr_crawler = crawlers.pop()
                curr_vscreen = next(vscreens)
                print('starting '+curr_crawler + ' with scren-id: ' +str(curr_vscreen))
                cargs = (addon, curr_crawler, image, curr_vscreen,)
                print('crawling addon: '+addon)
                kwargs['addon_data'] = tmp
                thread = pool.apply_async(crawl_wrapper, args=(cargs, kwargs, [], {'bfs': bfs}))
                threads.append((cargs, thread))
            if len(threads) > 0:
                new_threads = list()
                for (addon, name, image, screen), thread in threads:
                    if thread.ready():
                        crawlers.append(name)
                        remaining_images[image] -= 1
                        try:
                            print('calling thread.get on '+addon)
                            out = thread.get()
                            if not out:
                                continue
                            if out['installed']:
                                new_image = out['new_image']
                                found = False
                                for atup in out['changes']:
                                    if atup['aid'] not in all_addons:
                                        found = True
                                        addons.insert(0, atup)
                                        all_addons.add(atup['aid'])
                                        remaining_images[new_image] += 1
                                if found:
                                    remaining_images[image] += 1 # for when new image is made
                                    new_images[new_image] = {'addon': (addon,
                                        image), 'created': False}
                            print('collecting dead thread '+addon)
                        except Exception as e:
                            print(str(e)+' hit unhandled exception on '+addon)
                            estr = ''.join(traceback.format_exception(*sys.exc_info()))
                            errout(estr)
                            try:
                                with open('soft_fails', 'a') as f:
                                    f.write(json.dumps([addon, image, source, ('screen',screen), str(e)])+'\n')
                            except:
                                estr = ''.join(traceback.format_exception(*sys.exc_info()))
                                errout(estr)
                            if '111]' in str(e) or '409 Client Error' in str(e):
                                raise Exception('please restart me!')
                    else:
                        new_threads.append(((addon, name, image, screen), thread))
                threads = new_threads
        tmp_images = list(new_images.keys())
        for tmpimg in tmp_images:
            if not remaining_images[tmpimg]:
                try:
                    print('GUESS WHO JUST CALLED DESTROY')
                    destroy_image(tmpimg)
                except:
                    estr = ''.join(traceback.format_exception(*sys.exc_info()))
                    errout(estr)
                del remaining_images[tmpimg]
                del new_images[tmpimg]
        client = docker.from_env()
        for container in client.containers.list():
            if 'kodi_crawler' in container.name:
                print('killing '+container.name+'...', file=sys.stderr)
                container.kill()
        images = client.images.list(name='dekodirepo')
        for z in images:
            z.reload()
            for tag in z.attrs['RepoTags']:
                if tag != 'dekodi_build5:latest':
                    try:
                        print('removing '+tag)
                        destroy_image(tag)
                    except Exception as e:
                        estr = ''.join(traceback.format_exception(*sys.exc_info()))
                        errout(estr)
        cmd = ['ps','aux']
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        if 'mitmproxy' not in out+err:
            raise Exception('mitmproxy is gone')
        tmp = time.time()
        runtime = tmp - startcrawltime
        runtime = (runtime / 60)/60 # convert to hours
        if runtime > 6 and datetime.datetime.now().hour >= 20:
            raise Exception('REBOOT ME!')



if __name__ == '__main__':
    try:
        # default parameters
        num_crawlers     = 3                         # concurrent number of crawlers
        host_path_prefix = 'research/dekodi/dev/'    # path used for folder organization

        # manage argparsing
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.connect(("8.8.8.8", 80))
	g_myname = s.getsockname()[0]
	s.close()
        parser = argparse.ArgumentParser(description='dekodi crawler')
        parser.add_argument("-n", "--ncrawlers", help = "number of concurrent crawlers to use (default: 3)")
        parser.add_argument("-p", "--path",      help = "path used for folder organization (default: research/dekodi/dev/)")
        parser.add_argument("-b", "--bfs",      help = "set true to crawl add-on menus using breadth \
                first search; false for random walk (default: 0)", default=0)
        parser.add_argument("-c", "--controladdr",      help = "addr of control \
                server (default: '127.0.0.1:9999')", default='127.0.0.1:9999')
        parser.add_argument("-i", "--image",      help = "addr of control \
                server (default: 'dekodi_build5:latest)", default='127.0.0.1:9999')
        args = parser.parse_args()
        if args.ncrawlers:
            num_crawlers = int(args.ncrawlers)
        if args.path:
            host_path_prefix = args.path
        g_control_server = control_server='http://'+args.controladdr

        # start crawling
        crawl_addon_set(num_crawlers=num_crawlers,
                host_path_prefix=host_path_prefix,
                initial_image=args.image, real_screen=False,
                control_server=g_control_server, bfs=args.bfs)
    except Exception as e:
        estr = ''.join(traceback.format_exception(*sys.exc_info()))
        errout(estr)
        print(str(e), file=sys.stderr)
        print('stopping containers', file=sys.stderr)
        client = docker.from_env()
        for container in client.containers.list():
            if 'kodi_crawler' in container.name:
                print('killing '+container.name+'...', file=sys.stderr)
                container.kill()
        print('destroying images', file=sys.stderr)
        for k in new_images:
            if k != 'dekodi_build5:latest':
                try:
                    print('removing '+k, file=sys.stderr)
                    destroy_image(k)
                except Exception as e:
                    print(e, file=sys.stderr)
