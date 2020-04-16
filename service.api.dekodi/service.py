import xbmcgui
import xbmc
from SimpleXMLRPCServer import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
import xmlrpclib
import traceback
import os
import shutil
from threading import Thread
from SocketServer import ThreadingMixIn
from collections import defaultdict
import time
import subprocess
import random
import inspect
import sys
import socket
import urllib
try:
    import json
except:
    import simplejson as json
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
try:
    from geoip import geoiplite2 as geolite2
except:
    try:
        from geoip import geolite2
    except:
        pass

'''
NOTES:
    - blocking on calls that change window (select/back/home) is problematic; kodi blocks all new
      incoming RPC calls until the original blocking call is done
'''

# load params from file since I'm not sure how to pass them from Kodi...
f = os.path.abspath(inspect.stack()[0][1])  # source [1]
d = "/".join(f.split("/")[:-1]) + "/"
try:
    with open('addon_params.json', 'r') as f:
        params = json.load(f)
except IOError:
    # for some reason the path info gets messed up sometimes....
    with open(d+'addon_params.json', 'r') as f:
        params = json.load(f)
g_outputdir = os.path.expanduser('~')+'/output/'


#-------------------------------------------
# local tools
#-------------------------------------------

g_key_press_count = 0
def save_key():
    pos = get_current_position()
    wdw = get_full_window()
    with open(g_outputdir+"keys_pressed.list", "a+") as f:
        s = json.dumps(list(pos) + list(wdw))+"\n"
        f.write(s)
    global g_key_press_count
    g_key_press_count += 1
    get_screenshot(0)


def jiggle():
    xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "Input.Down"}))
    xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "Input.UP"}))
    return True

def jiggle_it(t):
    time.sleep(t)
    jiggle()

class TimerThread(Thread):
    def __init__(self, t, func, *args, **kwargs):
        self._waittime = t
        self._keepgoing = True
        self._func = func
        self._fargs = args
        self._fkwargs = kwargs
        Thread.__init__(self, str(time.time()))
    def run(self):
        t = self._waittime + time.time()
        while t > time.time() and self._keepgoing:
            time.sleep(random.randint(1,t))
        if self._keepgoing:
            self._func(*self._fargs, **self._fkwargs)
    def join(self, timeout=None):
        self._keepgoing = False
        Thread.join(self, timeout)

class threadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    '''
    class to thread server so it doesn't get stuck on blocking calls
    '''
    def _dispatch(self, method, params):
        try:
            return SimpleXMLRPCServer._dispatch(self, method, params)
        except Exception as e:
            t, value, tb = sys.exc_info()
            raise xmlrpclib.Fault(1, ''.join(traceback.format_exception(t,
                value, tb))+', '+str(g_actions_list[-1])+'************'+str(e))

class stuck_alarm(Exception):
    pass

def alarm_handler():
    raise stuck_alarm

# globals
g_played_video = False
g_actions_list = list()
g_suppress_push = False
g_stuck_flag = False
g_last_screenshot = ''
g_tries = 3
g_actionid = 0
g_kill_flag = 0
g_blocking_limit = 0
g_players = []
g_confirms = ['apply', 'ok', 'okay', 'yes', 'done', 'confirm', 'continue', 'english']
g_current_state = ('')
g_navs = [] # [(down, 'down'), (up, 'up'), (left, 'left'), (right, 'right')]
g_inverses = {'down': 'up', 'up': 'down', 'left': 'right', 'right': 'left'}
with open(d+'listitem_vals.txt', 'r') as f:
    listitem_vals = list()
    for line in f:
        listitem_vals.append(line.split(',')[0][1:-1])

def kill_timer(func, *args, **kwargs):
    '''
    decorator; forces kodi to restart if wrapped function takes longer than
    g_kill_flag if g_kill_flag > 0
    '''
    def wrapper(*args, **kwargs):
        if g_kill_flag > 0:
            res = False
            rthread = TimerThread(g_kill_flag, restart_kodi)
            rthread.start()
            res = func(*args, **kwargs)
            rthread.join()
            return res
        else:
            return func(*args, **kwargs)
    return wrapper

def block_timer(func, *args, **kwargs):
    '''
    decorator; forces kodi to restart if wrapped function takes longer than
    g_blocking_limit if g_blocking_limit > 0
    '''
    def wrapper(*args, **kwargs):
        if g_blocking_limit > 0:
            res = False
            jthread = TimerThread(g_blocking_limit, jiggle)
            jthread.start()
            res = func(*args, **kwargs)
            jthread.join()
            return res
        else:
            return func(*args, **kwargs)
    return wrapper

def suppress_it(func, *args, **kwargs):
    '''
    decorator; keeps func call from being pushed to g_actions_list if
    g_suppress_push is set to True
    '''

    global g_suppress_push
    tmp = g_suppress_push
    g_suppress_push = True
    try:
        res = func(*args, **kwargs)
    finally:
        g_suppress_push = tmp
    return res

def push_action(func):
    '''
    decorator; pushes wrapped func call to g_actions_list;
    '''
    def wrapper(*args, **kwargs):
        global g_actions_list
        global g_suppress_push
        unset_stuck_flag()
        aid = g_actionid
        name = func.__name__
        if not g_suppress_push:
            global g_actionid
            g_actionid += 1
            g_actions_list.append([name, args, kwargs, g_current_state,
                time.time(), aid])
            '''
            with open(g_outputdir+'actions.txt', 'w') as f:
                f.write(str(g_actions_list))
            '''
        res = func(*args, **kwargs)
        if not g_suppress_push:
            g_actions_list.append([name, res, g_current_state, '', time.time(),
                aid])
            '''
            with open(g_outputdir+'actions.txt', 'w') as f:
                f.write(str(g_actions_list))
            '''
        return res
    return wrapper

def retry_it(func):
    '''
    decorator; makes wrapped function try again if it fails
    '''
    def wrapper(*args, **kwargs):
        if g_blocking_limit > 0:
            for i in range(g_tries):
                res = False
                jthread = TimerThread(g_blocking_limit, jiggle)
                jthread.start()
                res = func(*args, **kwargs)
                jthread.join()
                return res
        else:
            for attempt in range(g_tries):
                res = func(*args, **kwargs)
                if res:
                    break
                time.sleep(1)
            return res
    return wrapper

# setup rpc server
server = threadedXMLRPCServer((params['host'], params['port']), SimpleXMLRPCRequestHandler)

server.register_introspection_functions()

def set_jiggle_timer(t):
    global g_blocking_limit
    g_blocking_limit = t
    return True
server.register_function(set_jiggle_timer, 'set_jiggle_timer')

def add_confirm_label(l):
    global g_confirms
    g_confirms.append(l)
    return True
server.register_function(add_confirm_label, 'add_confirm_label')

def set_kill_flag(val):
    '''
    sets g_kill_flag value; set this to automat
    '''
    global g_kill_flag
    g_kill_flag = val
    return True
server.register_function(set_kill_flag, 'set_kill_flag')

def set_tries(t):
    '''
    sets max number of retries performed by retry_it decorator
    '''
    global g_tries
    g_tries = t
    return True
server.register_function(set_tries, 'set_tries')

def set_stuck_flag():
    '''
    sets g_stuck_flag variable, which indicates whether or not navigation related
    operations have failed. Also captures screenshot
    '''
    global g_stuck_flag
    g_stuck_flag = True
    try:
        func = g_actions_list[-1][0]
        path = os.path.expanduser('~')+'/output/'
        if not os.path.exists(path):
            os.makedirs(path)
        path = path+func+'.'+str(time.time())+'.png'
        #get_screenshot(path)
    except:
        pass
    return True
server.register_function(set_stuck_flag, 'set_stuck_flag')

def remove_screenshots():
    try:
        if '/' in g_last_screenshot:
            p = '/'.join(g_last_screenshot.split('/')[:-1])
            shutil.rmtree(p)
        global g_last_screenshot
        g_last_screenshot = ''
    except:
        pass
    return True
server.register_function(remove_screenshots, 'remove_screenshots')

def is_stuck():
    return g_stuck_flag
server.register_function(is_stuck, 'is_stuck')

def unset_stuck_flag():
    global g_stuck_flag
    g_stuck_flag = False
    remove_screenshots()
    return True
server.register_function(unset_stuck_flag, 'unset_stuck_flag')

def reset_actions_list():
    '''
    empties g_actions_list variable
    '''
    global g_actions_list
    global g_actionid
    g_actions_list = list()
    g_actionid = 0
    return True
server.register_function(reset_actions_list, 'reset_actions_list')

def get_actions_list():
    '''
    returns list of actions performed, along with the path to the most recently
    captured screenshot. Each action is stored in two parts: a starting tuple
    (including the function name, the parameters and an acitonid) and an ending
    tuple (including the function name, the return, and an actionid)
    '''
    return {'screen': g_last_screenshot, 'actions': g_actions_list}
server.register_function(get_actions_list, 'get_actions_list')

#-------------------------------------------
# Dev commands
#-------------------------------------------
# general purpose function for testing new commands
def doit(code):
    '''
    executes arbitrary code
    '''
    return eval(code) or True
server.register_function(doit, 'doit')

def get_info_label(il):
    '''
    returns requested kodi info label
    '''
    return xbmc.getInfoLabel(il)
server.register_function(get_info_label, 'get_info_label')

def get_screenshot(path=None):
    '''
    :params path (str): desired path for screenshot to save to
    :params path (int): automatically creates path based on sequence key:
        0 -> screenshot before click
        1 -> screenshot immediately after click
        2 -> screenshot after click return flag
    :return: path to screenshot
    captures screenshot and returns path
    '''
    global g_last_screenshot
    if type(path) is int:
        path = ["scrn0.png", "scrn1.png", "scrn2.png", "scrn3.png"][path]
    if path is None:
        path = 'scrn.png'
    if '/' in path:
        dirs = '/'.join(path.split('/')[:-1])
        if not os.path.exists(dirs):
            os.makedirs(dirs)
    if path[-1] == '/':
        path = path+str(time.time())
    if path[0] != '/':
        path = g_outputdir + path
    xbmc.executebuiltin('TakeScreenshot('+path+')', True)
    g_last_screenshot = path
    return path
server.register_function(get_screenshot, 'get_screenshot')

#-------------------------------------------
# Navigation Commands
#-------------------------------------------
@push_action
def up():
    '''
    nav: move up
    '''
    xbmc.executebuiltin('Action(Up)', True)
    return True
server.register_function(up, 'up')

@push_action
def down():
    '''
    nav: move down
    '''
    xbmc.executebuiltin('Action(Down)', True)
    return True
server.register_function(down, 'down')

@push_action
def left():
    '''
    nav: move left
    '''
    xbmc.executebuiltin('Action(Left)', True)
    return True
server.register_function(left, 'left')

@push_action
def right():
    '''
    nav: move right
    '''
    xbmc.executebuiltin('Action(Right)', True)
    return True
server.register_function(right, 'right')

@push_action
def back(new_window_wait=True, *args):
    '''
    go back

    NOTE: hard to know when this works or not
    '''
    if new_window_wait:
        old_state = get_state()
    xbmc.executebuiltin('Action(Back)')
    if new_window_wait:
        return change_wait(old_state, *args)
    return True
server.register_function(back, 'back')

g_navs = [(down, 'down'), (up, 'up'), (left, 'left'), (right, 'right')]

@push_action
def select(new_window_wait=True, timeout=10):
    '''
    :param new_window_wait: indicates whether to attempt blocking
    execute select action

    NOTE: hard to know when this works or not
    '''
    save_key()
    old_state = get_state()
    xbmc.executebuiltin('Action(Select)')
    if new_window_wait:
        change_wait(old_state, timeout)
        # catch infinite busy hang
        if is_busy():
            back()
            # catch popups explaining preceding busy hang
            if get_window_id() == 12002:
                select(True, 2)
    get_screenshot(1)
    return sum([1 for i,z in enumerate(g_current_state) \
            if old_state[i] != g_current_state[i]])
server.register_function(select, 'select')

@push_action
def run_addon(aid, new_window_wait=True, root=True):
    '''
    :param aid: name of addon to launch
    :param new_window_wait: indicates whether to attempt blocking

    NOTE: useful for benchmarking
    '''
    home()
    if new_window_wait:
        old_state = get_state()
    xbmc.executebuiltin('RunAddon('+aid+')')
    if new_window_wait:
        if change_wait(old_state):
            if in_dialog():
                if not escape_dialog():
                    return False
        else:
            return False
    wid, path = get_full_window()
    expected_path = 'plugin://'+aid+'/'
    if path.startswith(expected_path) and len(path) > expected_path \
            and root==True:
                return activate_window(wid, expected_path)
    return True
server.register_function(run_addon, 'run_addon')

@retry_it
@push_action
def home(reset_pos=False, timeout=10):
    '''
    :param reset_pos: (bool) indicates whether to go back to position 0 on home

    returns to home page
    '''
    t = time.time() + 5
    while in_dialog() and time.time() < t:
        old_state = get_state()
        xbmc.executebuiltin('Dialog.Close(all,true)', True)
        xbmc.executebuiltin('Action(Back)')
        change_wait(old_state)
    if list(get_state())[:3] == [10000, 9999, '']:
        return True
    old_state = get_state()
    xbmc.executebuiltin('ActivateWindow(10000)', True)
    xbmc.executebuiltin('Dialog.Close(all,true)', True)
    xbmc.executebuiltin('Action(Back)')
    changed = change_wait(old_state, timeout=float(timeout)/3.0)
    if not changed:
        xbmc.executeJSONRPC(json.dumps({ "jsonrpc": "2.0", "id": 1,
            "method": "Input.Home"}))
        changed = change_wait(old_state, timeout=float(timeout)*2.0/3.0)
    if reset_pos:
        set_focus(9000, 0)
    if xbmcgui.getCurrentWindowId() == 10000:
        return True
    elif changed:
        return home(reset_pos, timeout)
    else:
        set_stuck_flag()
        return False
server.register_function(home, 'home')

@retry_it
@push_action
def activate_window(*wid):
    '''
    :params *wid: window id, [dir, return]
    for usage, see https://kodi.wiki/view/List_of_built-in_functions
    ActivateWindow()

    example usage with get_full_window:
        window = get_full_window()
        activate_window(*window)

    NOTE: useful for benchmarking
    '''
    if get_full_window()[:len(wid)] == wid:
        return True
    if not home():
        set_stuck_flag()
        return False
    old_state = get_state()
    wids = ', '.join([str(z) for z in wid])
    xbmc.executebuiltin('ActivateWindow('+wids+')')
    change_wait(old_state)
    if get_full_window()[:len(wid)] != wid:
        set_stuck_flag()
        return False
    ctrl = get_current_position()[1:]
    timeout = time.time() + 5
    while timeout > time.time() and not suppress_it(set_focus, *ctrl)[0]:
        time.sleep(0.25)
    return True
server.register_function(activate_window, 'activate_window')

@push_action
def goto_addon_browser():
    '''
    legacy function; specifically launches addon browser window

    NOTE: we probably won't need this since we can launch addons directly and
    enable addons directly
    '''
    xbmc.executebuiltin('ActivateWindow(10040)')
    if xbmcgui.getCurrentWindowId() != 10040 and xbmcgui.getCurrentWindowDialogId() != 10040:
        set_stuck_flag()
        return False
    return True
server.register_function(goto_addon_browser, 'goto_addon_browser')

@push_action
def dialog_close():
    '''
    closes all open dialog windows

    NOTE: some addons don't handle this well
    '''
    if not in_dialog():
        return True
    old_state = get_state()
    xbmc.executebuiltin('Dialog.Close(all,true)', True)
    change_wait(old_state)
    while is_busy():
        time.sleep(1)
    if in_dialog():
        set_stuck_flag()
        return False
    return True
server.register_function(dialog_close, 'dialog_close')

@push_action
def set_focus(cid, ind=None, zero=False):
    '''
    :param cid: control id
    :param ind: if control is list, ind refers to desired position in list
    :param zero: set zero to true for versions of kodi where "absolute" doesn't work...
    :return: success (bool) and current position

    example usage with get_current_position():
        tmp = get_current_position()
        set_focus(*tmp[1:])

    NOTE: useful for benchmarking
    '''
    offset = 0
    if zero:
        cid = int(cid)
        if ind is not None:
            xbmc.executebuiltin('SetFocus('+str(cid)+',0,absolute)', True)
            pos = get_current_position()
            if pos is not None and len(pos) == 3 and pos[2] != 0:
                while pos[2] > 1:
                    suppress_it(up)
                    pos = get_current_position()
                if pos[2] == 1:
                    suppress_it(up)
                    pos = get_current_position()
                    if not (pos is not None and len(pos) == 3 and pos[2] == 0):
                        suppress_it(down)
                pos = get_current_position()
                offset = pos[2]
        else:
            ind = 0
    elif ind is None:
        ind = 0

    xbmc.executebuiltin('SetFocus('+str(cid)+','+str(ind)+',absolute)', True)
    pos = get_current_position()
    # account for lists that start from 1.....
    if pos is not None and len(pos) == 3 and pos[2] != ind+offset:
        bump = 0
        while pos is not None and len(pos) == 3 and pos[2] > ind+offset and \
                pos[1] == cid:
            bump += 1
            old = pos
            xbmc.executebuiltin('SetFocus('+str(cid)+','+str(ind-bump)+',absolute)', True)
            pos = get_current_position()
            if pos == old:
                break
        while pos is not None and len(pos) == 3 and pos[2] < ind+offset and \
                pos[1] == cid:
            bump -= 1
            old = pos
            xbmc.executebuiltin('SetFocus('+str(cid)+','+str(ind-bump)+',absolute)', True)
            pos = get_current_position()
            if pos == old:
                break
    ind += offset
    if list(pos[1:]) != [cid, ind][:len(pos)-1]:
        if zero:
            set_stuck_flag()
            return False, pos
        else:
            time.sleep(2)
            return suppress_it(set_focus, cid, ind, True)
    return True, pos
server.register_function(set_focus, 'set_focus')

@push_action
def set_focus_and_select(pos, timeout=10, zero=False, new_window_wait=True):
    if set_focus(zero=zero, *pos):
        return select(new_window_wait, timeout)
    elif get_state()[1] == 12002 and pos[0] != 11: # check for popup
        #get_screenshot('unexpected_okdialog/')
        get_screenshot(0)
        xbmc.executebuiltin("SendClick(11)")
        get_screenshot(1)
        time.sleep(2)
        if set_focus(zero=zero, *pos):
            return select(new_window_wait, timeout)
    return False
server.register_function(set_focus_and_select, 'set_focus_and_select')

@push_action
def escape_dialog():
    '''
    attempts to exit dialog by hunting down and pressing confirm buttons until
    no longer inside of dialogs

    NOTE: this is bad for some dialogs (for example, those that ask if you'd
    like to sign in or perform optional setup)
    '''
    while in_dialog():
        found, button = find_confirm_button()
        if found:
            set_focus_and_select(button)
        elif not found and not is_busy() and in_dialog():
            set_stuck_flag()
            return False
    return True
server.register_function(escape_dialog, 'escape_dialog')

#-------------------------------------------
# GUI Reading Commands
#-------------------------------------------

def get_state():
    '''
    gets current, relevant state information: window id, window dialog id,
    folder path, and control position
    '''
    global g_current_state
    g_current_state = (xbmcgui.getCurrentWindowId(),
            xbmcgui.getCurrentWindowDialogId(),
            xbmc.getInfoLabel('Container.FolderPath'),  get_current_position())
    return g_current_state
server.register_function(get_state, 'get_state')

@push_action
def change_wait(old_state, timeout=5, wait=0.1):
    '''
    :param old_state: state (from get_state) before change trigger executed
    :param timeout: how long to wait for state to change from old_state
    :param wait: how long to pause between spin wait loop iterations

    attempts to block until desired state change has occurred; returns False if
    no change occurs
    '''
    new_state = old_state
    start = time.time()
    timeout = start + timeout
    max_timeout = max([20+start, timeout]) # give extra time for busy window...
    while (timeout > time.time() and (old_state == new_state \
            or is_busy() or 10138 in new_state or 10160 in new_state) \
            and not is_playing()) \
            or (max_timeout > time.time() and is_busy()):
        # checks for changes and makes sure we're not in a busy dialog
        time.sleep(wait)
        new_state = get_state()
        if new_state == [12005, 9999, '', ['', '']]:
            break

    if new_state != old_state and not is_busy():
        return True
    else:
        set_stuck_flag()
        return False
server.register_function(change_wait, 'change_wait')

@push_action
def new_window_wait(old_state, timeout=5, wait=0.1):
    '''
    :param old_state: state (from get_state) before change trigger executed
    :param timeout: how long to wait for state to change from old_state
    :param wait: how long to pause between spin wait loop iterations

    attempts to block until desired state change has occurred; returns False if
    no change occurs
    '''
    new_state = old_state
    timeout = time.time() + timeout
    while timeout > time.time() and (old_state[-1] == new_state[-1] \
            or sum([1 for i in range(len(old_state)) if \
            old_state[i] == new_state[i]]) < 2 \
            or is_busy() or 10138 in new_state or 10160 in new_state):
        # checks for changes and makes sure we're not in a busy dialog
        time.sleep(wait)
        new_state = get_state()
    if new_state != old_state and not is_busy():
        return True
    else:
        set_stuck_flag()
        return False
server.register_function(change_wait, 'change_wait')

@push_action
def full_wait(old_state, timeout=5, wait=0.1):
    '''
    (see change_wait())

    attempts to block like change_wait(), but waits for "substantial" state
    change before returning

    NOTE: this doesn't work well; hard to know when to expect substantial
    state changes
    '''
    new_state = old_state
    timeout = time.time() + timeout
    while timeout > time.time() and (len([i for i,z in enumerate(old_state) \
            if new_state[i] == z]) > 1 or is_busy()):
        # checks for changes and makes sure we're not in a busy dialog
        new_state = get_state()
        if 9999 in old_state and in_dialog() and not is_busy():
            # account for when our target state is just opening a dialog
            # (because in that case, window won't change...)
            old_state = tuple([-1]+list(old_state[1:]))
        time.sleep(wait)
    if new_state != old_state and not is_busy():
        return True
    else:
        set_stuck_flag()
        return False
server.register_function(full_wait, 'full_wait')

def is_busy():
    '''
    indicates if known busy window or dialog is active
    '''
    if xbmcgui.getCurrentWindowDialogId() in [10138, 10160] \
            or xbmcgui.getCurrentWindowId() in [10138, 10160] \
                or (len(xbmc.getInfoLabel('System.CurrentControlID')) == 0 \
                and xbmcgui.getCurrentWindowId() not in [12005]):
                return True
    return False
server.register_function(is_busy, 'is_busy')

@push_action
def busy_wait(timeout=5, wait=0.1):
    '''
    :param timeout: max time to wait
    :param wait: how long to pause between spin wait interations

    attempts to wait until is_busy() returns False
    '''
    timeout = time.time() + timeout
    while is_busy() and timeout > time.time():
        time.sleep(wait)
    return is_busy()
server.register_function(busy_wait, 'busy_wait')

def in_dialog():
    '''
    returns True if kodi appears to be currently in a dialog window
    '''
    if xbmcgui.getCurrentWindowDialogId() != 9999 and not is_busy():
        return True
    window_id = xbmcgui.getCurrentWindowId()
    if 10099 <= window_id <= 10160 and not is_busy():
        return True
    return False
server.register_function(in_dialog, 'in_dialog')

def in_progress_dialog():
    '''
    returns True if kodi appears to be in progress dialog window
    '''
    if xbmcgui.getCurrentWindowDialogId() == 10101 or xbmcgui.getCurrentWindowId() == 10101:
        return True
    return False
server.register_function(in_progress_dialog, 'in_progress_dialog')

def get_dialog_id():
    '''
    returns dialog window id

    NOTE: seems like kodi returns 9999 when not in dialog
    '''
    return xbmcgui.getCurrentWindowDialogId()
server.register_function(get_dialog_id, 'get_dialog_id')

def get_list_length(cid=None):
    '''
    :param cid: id of control to check for; if None, checks current control
    :return: length of control if control is list; else -1
    '''
    if cid is None:
        cid = xbmc.getInfoLabel('System.CurrentControlID')
    try:
        return int(xbmc.getInfoLabel('Container('+str(cid)+').NumAllItems'))
    except:
        try:
            return int(xbmc.getInfoLabel('Container('+str(cid)+').NumItems'))
        except:
            return -1
server.register_function(get_list_length, 'get_list_length')

def has_parent_item(cid=None):
    '''
    :param cid: id of control to check for; if None, checks current control
    sometimes, the positions in a list control are offset by 1 if there's a
    '[..]' (parent) item in the list; this checks for that case

    NOTE: this won't work in versions of Kodi where NumAllItems hasn't been
    implemented...
    '''
    if cid is None:
        cid = xbmc.getInfoLabel('System.CurrentControlID')
    try:
        allitems = int(xbmc.getInfoLabel('Container('+str(cid)+').NumAllItems'))
        items = int(xbmc.getInfoLabel('Container('+str(cid)+').NumItems'))
        return allitems - items
    except:
        return -1
server.register_function(has_parent_item, 'has_parent_item')

def get_num_folder_items(cid=None):
    '''
    :param cid: id of control to check for; if None, checks current control
    :return: number of folder items if control is list; else -1
    '''
    if cid is None:
        cid = xbmc.getInfoLabel('System.CurrentControlID')
    try:
        return get_list_length(cid) \
                - int(xbmc.getInfoLabel('Container('+str(cid)+').NumNonFolderItems'))
    except:
        return -1
server.register_function(get_num_folder_items, 'get_num_folder_items')

def get_num_nonfolder_items(cid=None):
    '''
    :param cid: id of control to check for; if None, checks current control
    :return: # non-folder items if control is list; else -1
    '''
    if cid is None:
        cid = xbmc.getInfoLabel('System.CurrentControlID')
    try:
        return get_list_length(cid) \
                - int(xbmc.getInfoLabel('Container('+str(cid)+').NumNonFolderItems'))
    except:
        return -1
server.register_function(get_num_nonfolder_items, 'get_num_nonfolder_items')

def get_current_control():
    return xbmc.getInfoLabel('System.CurrentControl')
server.register_function(get_current_control, 'get_current_control')

def get_window_name():
    return xbmc.getInfoLabel('System.CurrentWindow')
server.register_function(get_window_name, 'get_window_name')

def get_window_id():
    try:
        return int(xbmcgui.getCurrentWindowId())
    except:
        try:
            return int(xbmcgui.getCurrentWindowDialogId())
        except:
            return -1
server.register_function(get_window_id, 'get_window_id')

def get_full_window():
    '''
    :return: window id and folder path
    '''
    return get_window_id(), get_current_path()
server.register_function(get_full_window, 'get_full_window')

def get_item_path():
    '''
    :return: path that item is pointing to (and probably will probably navigate
    to upon select() call if it's a folder item)
    '''
    return xbmc.getInfoLabel('ListItem.FileNameAndPath')
server.register_function(get_item_path, 'get_item_path')

def get_current_control_id():
    try:
        return int(xbmc.getInfoLabel('System.CurrentControlId'))
    except:
        return -1
server.register_function(get_current_control_id, 'get_current_control_id')

def get_window_xml():
    '''
    based on code from service.xbmc.tts
    https://github.com/ruuk/service.xbmc.tts/blob/master/lib/windows/windowparser.py
    '''
    base = xbmc.getInfoLabel('Window.Property(xmlfile)')
    if os.path.exists(base):
        return base
    skin_dirs = list()
    translated_path = xbmc.translatePath('special://skin')
    for pathname, dirs, files in os.walk(translated_path):
        skin_dirs = [pathname+d for d in dirs]
        break
    aspect = xbmc.getInfoLabel('Skin.AspectRatio')
    xmlpath = os.path.join(translated_path, 'addon.xml')
    if os.path.exists(xmlpath):
        with open(xmlpath, 'r') as f:
            lines = f.readlines()
        for l in lines:
            if 'aspect="{0}"'.format(aspect) in l:
                folder = l.split('folder="', 1)[-1].split('"',1)[0]
                skin_dirs.append(os.path.join(translated_path, folder))
    for skin_dir in skin_dirs:
        path = os.path.join(skin_dir, base)
        if os.path.exists(path):
            return path
        path = os.path.join(skin_dir, base.lower())
        if os.path.exists(path):
            return path
    else:
        return -1
server.register_function(get_window_xml, 'get_window_xml')

def get_current_position():
    '''
    :return: control label, control id [, position in list control]
    '''
    current = xbmc.getInfoLabel('System.CurrentControl')
    cid = xbmc.getInfoLabel('System.CurrentControlID')
    if len(cid) == 0:
        return '', cid
    ll = get_list_length(int(cid))
    if ll > 0:
        return current, int(cid), int(xbmc.getInfoLabel('Container('+cid+').CurrentItem'))
    else:
        return current, int(cid)
server.register_function(get_current_position, 'get_current_position')

def get_container_items(cid, infolabel='Label'):
    '''
    :param cid: id of control of interest
    :param infolabel: desired infolabel to capture
    returns (label, control id, and pos) for each item in cid without
    navigating to each item

    NOTE: for list controls
    '''
    cid = str(cid)
    ll = get_list_length(cid)
    items = list()
    original_spot = get_current_position()[1:]
    if ll > 0:
        for pos in range(ll):
            label = xbmc.getInfoLabel(
                    'Container('+cid+').ListItemAbsolute('+str(pos)+').'+infolabel)
            items.append([label, int(cid), pos])
        if suppress_it(set_focus, cid, 0)[1][2] != 0:
            for i in range(len(items)):
                items[i][-1] += 1
        suppress_it(set_focus, *original_spot)
        items = [tuple(z) for z in items]
    return items
server.register_function(get_container_items, 'get_container_items')

def get_listitem_data(pos=None):
    '''
    uses external list of all listitem infolabels; iterates through list and
    returns all non-empty labels as dictionary
    '''
    if pos == None:
        pos = get_current_control()[1:]
    if len(pos) < 2:
        pos.append(0)
    data = dict()
    for k in listitem_vals:
        val = xbmc.getInfoLabel('container('+str(pos[0]) \
                +').listitemabsolute('+str(pos[1])+').'+k)
        if val != '':
            data[k] = val
    return data
server.register_function(get_listitem_data, 'get_listitem_data')

def get_simple_neighbors(checked=None):
    start = get_current_position()
    if checked is None:
        checked = defaultdict(set)
    if start is None:
        return checked
    startpos = start[1:]
    for nav, d in g_navs:
        if d in checked[startpos]:
            continue
        suppress_it(nav)
        ninfo = get_current_position()
        if ninfo is not None:
            pos = ninfo[1:]
            if pos != startpos and pos not in checked:
                checked[pos].add(g_inverses[d])
        checked[startpos].add(d)
        if not suppress_it(set_focus, *startpos)[0]:
            break
    return checked

def get_control_neighbors(checked=None, thorough=False):
    '''
    navigates up/down/left/right to find all control neighbors by brute force
    '''
    start = get_current_position()
    if checked is None:
        checked = defaultdict(set)
    done = set()
    labels = dict()
    # make sure we're on a control
    i = 0
    while start is None and i < len(g_navs):
        suppress_it(g_navs[i][0])
        start = get_current_position()
        i += 1
    if start is None:
        return checked, done
    startpos = start[1:]
    if get_list_length() > 0:
        items = get_container_items(startpos[0])
        positions = [z[2] for z in items]
        minmaxpos = {min(positions), max(positions)}
        for item in items:
            if item[2] in minmaxpos and len(checked[item[1:]]) < 4:
                suppress_it(set_focus, *startpos)
                checked = get_simple_neighbors(checked)
            else:
                checked[item[1:]] = ['up', 'down', 'left', 'right']
            done.add(item[1:])
            labels[item[1:]] = item[0]
    else:
        checked = get_simple_neighbors(checked)
        done.add(startpos)
        labels[startpos] = start[0]
    return checked, done, labels

@push_action
def find_controls():
    '''
    attempts to find all controls in window by brute force (navigation)
    returns list of position tuples of all controls. For tuple formatting, see
    get_current_position()
    '''
    checked, done, all_labels = get_control_neighbors()
    #xbmc.log(str(inspect.getframeinfo(inspect.currentframe()).lineno))
    nodes = [z for z in checked if len(checked[z]) < 4]
    count = 0
    # cap button checks at 20 so we don't get in a weird long lasting loop...
    while len(nodes) > 0 and count < 20:
        count += 1
        current = nodes.pop()
        done.add(current)
        if suppress_it(set_focus, *current)[0]:
            checked, tmpdone, labels = get_control_neighbors(checked)
            all_labels.update(labels)
            done.update(tmpdone)
            nodes.extend(done.symmetric_difference(checked))
    ret = []
    for z in done:
        try:
            ret.append((all_labels[z], z[0], z[1]))
        except (IndexError, KeyError):
            ret.append((all_labels[z], z[0]))
    return ret


server.register_function(find_controls, 'find_controls')

def find_confirm_button():
    '''
    attempts to find confirm button (such as 'ok')
    returns success bool (was confirm control found), and position
    of first apparent confirm button found (-1 if not found)
    '''
    try:
        if xbmc.geInfoLabel('Control.GetLabel(11)').lower() in g_confirms:
            return True, 11
    except:
        pass
    controls = find_controls()
    controls = {z[0].lower():z[1:] for z in controls}
    for c in g_confirms:
        if c in controls:
            try:
                return True, controls[c]
            except KeyError:
                return True, ('keyerror?', c, list(controls.keys()))
    return False, -1
server.register_function(find_confirm_button, 'find_confirm_button')

def get_current_path():
    return xbmc.getInfoLabel('Container.FolderPath')
server.register_function(get_current_path, 'get_current_path')

#-------------------------------------------
# Player Commands
#-------------------------------------------

@push_action
def stop_player():
    '''
    stops video playback
    '''
    if len(g_players) == 0:
        if not is_playing():
            return True
    for player in g_players:
        xbmc.executebuiltin('Action(Stop)')
        xbmc.executeJSONRPC(json.dumps({ "jsonrpc": "2.0", "id": 1,
            "method": "Player.Stop", "params": {"playerid": player}}))
    return True
server.register_function(stop_player, 'stop_player')

def get_active_players_count():
    try:
        players = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'id': 1,
            'method': 'Player.GetActivePlayers'})))['result']
        global g_players
        g_players = [z['playerid'] for z in players]
        return len(g_players)
    except Exception as e:
        return str(e)
server.register_function(get_active_players_count, 'get_active_players_count')

def is_playing():
    '''
    returns True if active player count is greater than 0

    NOTE: useful for benchmarking
    '''
    if get_active_players_count() > 0:
        return True
    elif get_window_id() == 12005 or get_dialog_id() == 12005:
        return True
    elif g_played_video:
        return True
    return False
server.register_function(is_playing, 'is_playing')

def get_video_url():
    t = 4 + time.time()
    while t > time.time() and not g_played_video:
        time.sleep(0.1)
    video = g_played_video
    global g_played_video
    g_played_video = ''
    if len(video) and video[0]:
        with open(g_outputdir+"keys_to_video.list", "a+") as f:
            s = json.dumps(str(g_key_press_count)+", "+video[0]+"\n")
            f.write(s)
    return video
server.register_function(get_video_url, 'get_video_url')

@push_action
def play_from_path(path):
    '''
    :param path: path to content

    example usage:
        p = get_item_path() # gets current item path; would probably put p in file
        play_from_path(p) # probably would load known p from file

    NOTE: useful for benchmarking
    '''
    xbmc.executebuiltin('PlayMedia('+path+')')
    return True
server.register_function(play_from_path, 'play_from_path')


#-------------------------------------------
# Addon Commands
#-------------------------------------------

@push_action
def enable_addon(aid):
    '''
    :param aid: name of addon

    enables addon
    '''
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.SetAddonEnabled',
            'params': {
                'addonid': aid,
                'enabled': True
                }
            }
    json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    return True
server.register_function(enable_addon, 'enable_addon')

@push_action
def disable_addon(aid):
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.SetAddonEnabled',
            'params': {
                'addonid': aid,
                'enabled': False
                }
            }
    json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    return True
server.register_function(disable_addon, 'disable_addon')

def get_installed_addons_info():
    '''
    :param aid: name of addon
    '''
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': {
                'installed': True,
                'properties': ['dependencies', 'version','extrainfo', 'disclaimer','name','path','rating','summary','description', 'author']
                }
            }
    res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    try:
        return res['result']['addons']
    except:
        return res
server.register_function(get_installed_addons_info, 'get_installed_addons_info')

def get_all_addons_info():
    '''
    :param aid: name of addon
    '''
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': {
                'installed': False,
                'properties': ['dependencies', 'version','extrainfo', 'disclaimer','name','path','rating','summary','description', 'author']
                }
            }
    res = dict()
    while len(json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))) > len(res):
        res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    try:
        addons = res['result']['addons']
    except:
        return res
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': {
                'installed': True,
                'properties': ['dependencies', 'version','extrainfo', 'disclaimer','name','path','rating','summary','description', 'author']
                }
            }
    res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    try:
        addons += res['result']['addons']
    except:
        return res
    return addons
server.register_function(get_all_addons_info, 'get_all_addons_info')

def get_addon_info(aid):
    '''
    :param aid: name of addon
    '''
    # do for uninstalled set
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': {
                'installed': False,
                'properties': ['dependencies', 'version','extrainfo', 'disclaimer','name','path','rating','summary','description', 'author']
                }
            }
    res = dict()
    while len(json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))) > len(res):
        res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    try:
        addons = res['result']['addons']
    except:
        return res
    # do it again for installed set (if addon wasn't found already)
    for addon in addons:
        if aid == addon['addonid']:
            return addon
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': {
                'installed': True,
                'properties': ['dependencies', 'version','extrainfo', 'disclaimer','name','path','rating','summary','description', 'author']
                }
            }
    res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    try:
        addons = res['result']['addons']
    except:
        return res
    for addon in addons:
        if aid == addon['addonid']:
            return addon
    return {}
server.register_function(get_addon_info, 'get_addon_info')

def get_addons_list(params=None):
    '''
    returns list of addons as dictionary {addon id: addon type}
    '''
    if not params:
        params = dict()
    json_cmd = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'Addons.GetAddons',
            'params': params
            }
    xbmc.executebuiltin('UpdateLocalAddons', True)
    res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
    time.sleep(2)
    while len(json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))) > len(res):
        if 'installed' not in json_cmd['params']:
            res = get_all_addons_list(json_cmd['params'])
        if json_cmd['params']['installed'] == 'all':
            res = get_all_addons_list(json_cmd['params'])
        else:
            res = json.loads(xbmc.executeJSONRPC(json.dumps(json_cmd)))
            time.sleep(2)
    try:
        return {z['addonid']: z['type'] for z in res['result']['addons']}
    except:
        return res
server.register_function(get_addons_list, 'get_addons_list')

def get_all_addons_list(params=None):
    '''
    returns list of addons as dictionary {addon id: addon type}
    '''
    if not params:
        params = dict()
    res = get_installed_addons_list(params)
    res.update(get_uninstalled_addons_list(params))
    return res
server.register_function(get_all_addons_list, 'get_all_addons_list')

def update_addons_list():
    xbmc.executebuiltin('UpdateLocalAddons', True)
    return True
server.register_function(update_addons_list, 'update_addons_list')

def get_installed_addons_list(params=None):
    '''
    returns list of installed addons as dictionary
    '''
    if not params:
        params = dict()
    params['installed'] = True
    return get_addons_list(params)
server.register_function(get_installed_addons_list, 'get_installed_addons_list')

def get_uninstalled_addons_list(params=None):
    '''
    returns list of uninstalled addons
    '''
    if not params:
        params = dict()
    params['installed'] = False
    return get_addons_list(params)
server.register_function(get_uninstalled_addons_list, 'get_uninstalled_addons_list')

def get_installed_video_addons_list(params=None):
    '''
    returns list of installed video addons
    '''
    if not params:
        params = dict()
    params['content'] = 'video'
    return get_installed_addons_list(params)
server.register_function(get_installed_video_addons_list,
        'get_installed_video_addons_list')

def get_uninstalled_video_addons_list(params=None):
    '''
    returns list of installed video addons
    '''
    if not params:
        params = dict()
    params['content'] = 'video'
    return get_uninstalled_addons_list(params)
server.register_function(get_uninstalled_video_addons_list,
        'get_uninstalled_video_addons_list')

def get_installed_repo_addons_list(params=None):
    '''
    returns list of installed video addons
    '''
    if not params:
        params = dict()
    params['type'] = 'xbmc.addon.repository'
    return get_installed_addons_list(params)
server.register_function(get_installed_repo_addons_list,
        'get_installed_repo_addons_list')

def get_uninstalled_repo_addons_list(params=None):
    '''
    returns list of installed video addons
    '''
    if not params:
        params = dict()
    params['type'] = 'xbmc.addon.repository'
    return get_uninstalled_addons_list(params)
server.register_function(get_uninstalled_repo_addons_list,
        'get_uninstalled_repo_addons_list')

@push_action
def install_addon(aid, timeout=60):
    '''
    :param aid: addon id
    :param timeout: how long to spin wait
    installs addon

    NOTE: blocks until addon appears in installed addons list
    '''
    addons = get_installed_addons_list()
    timeout = time.time()+timeout
    xbmc.executebuiltin('InstallAddon('+aid+')')
    xbmc.executebuiltin('UpdateLocalAddons', True)
    newaddons = list(set(get_installed_addons_list()).difference(addons))
    if aid in newaddons:
        return True
    get_screenshot('post_installation.png')
    return False
server.register_function(install_addon, 'install_addon')

def in_installation_dialog(aid):
    return in_dialog() and not in_progress_dialog() and \
            not is_installed(aid)

server.register_function(in_installation_dialog, 'in_installation_dialog')

def is_installed(aid):
    return aid in get_installed_addons_list()
server.register_function(is_installed, 'is_installed')

@push_action
def continue_installation(aid):
    if in_installation_dialog(aid):
        found, button = find_confirm_button()
        if found:
            set_focus_and_select(button, new_window_wait=False)
        else:
            set_stuck_flag()
            return False
    else:
        time.sleep(5)
    xbmc.executebuiltin('UpdateLocalAddons', True)
    return is_installed(aid)
server.register_function(continue_installation, 'continue_installation')

@push_action
def open_install_from_zip_window():
    old_state = get_state()
    xbmc.executebuiltin('InstallFromZip')
    change_wait(old_state)
    return True
server.register_function(open_install_from_zip_window,
        'open_install_from_zip_window')

def follow_path(zip_path):
    home()
    old_state = get_state()
    xbmc.executebuiltin('InstallFromZip')
    change_wait(old_state)
    paths = {z[0]: z[1:] for z in get_container_items(450, 'FolderPath')}
    old_match = ''
    match = ''
    # navigate to zip file in menu and select it
    while len(match) < len(zip_path):
        for path in paths.keys():
            if zip_path.startswith(path) and len(path) > len(match):
                match = path
        if len(match) >= len(zip_path):
            set_focus(*paths[match])
            break
        if len(match) > len(old_match):
            old_match = match
            set_focus_and_select(paths[match])
            if in_dialog():
                paths = {z[0]: z[1:] for z in get_container_items(450, 'FolderPath')}
            else:
                set_stuck_flag()
                return False
        else:
            break
    return True
server.register_function(follow_path, 'follow_path')

@push_action
def install_addon_from_zip(zip_path, timeout=30):
    '''
    :param zip_path: path to zip file
    :param timeout: how long to spin wait
    installs addon from zip

    NOTE: if install is successfully initiated, this will block until kodi's
    installed addons list increases in size
    '''
    home()
    addons = get_installed_addons_list().keys()
    old_state = get_state()
    xbmc.executebuiltin('InstallFromZip')
    change_wait(old_state)
    paths = {z[0]: z[1:] for z in get_container_items(450, 'FolderPath')}
    old_match = ''
    match = ''
    # navigate to zip file in menu and select it
    while len(match) < len(zip_path):
        for path in paths.keys():
            if zip_path.startswith(path) and len(path) > len(match):
                match = path
        if len(match) > len(old_match):
            old_match = match
            set_focus_and_select(paths[match])
            if in_dialog():
                paths = {z[0]: z[1:] for z in get_container_items(450, 'FolderPath')}
            else:
                set_stuck_flag()
                get_screenshot('zip_not_found.png')
                return False
        else:
            get_screenshot(1)
            break
    newaddons = list(set(get_installed_addons_list()).difference(addons))
    if match == zip_path and len(newaddons) > 0 and not in_dialog():
        return True
    else:
        get_screenshot('zip_not_found.png')
        return False
server.register_function(install_addon_from_zip, 'install_addon_from_zip')


@push_action
def add_source(url, name, timeout=10):
    '''
    this works, but it turns out each source is set up differently, so it's
    probably best to just do whatever source related stuff manually
    '''
    f = os.path.abspath(inspect.stack()[0][1])  # source [1]
    infile = "/".join(f.split("/")[:-1]) + "/sources.xml"
    outfile = "/".join(f.split("/")[:-3]) + "/userdata/sources.xml"
    try:
        try:
            # first try to get the file from kodi
            with open(outfile, 'r') as f:
                s0 = f.readline()
                while '<files>' not in s0:
                    s0 +=  f.readline()
                s0 += f.readline()
                s1 = f.read()
        except:
            # if that file doesn't exist, get it from template
            with open(infile, 'r') as f:
                s0 = f.readline()
                while '<files>' not in s0:
                    s0 +=  f.readline()
                s0 += f.readline()
                s1 = f.read()

        s = '<source><name>'+name+'</name><path pathversion="1">' \
                +url+'</path><allowsharing>true</allowsharing></source>'
        fstr = s0+s+s1
        with open(outfile, 'w') as f:
            f.write(fstr)
    except Exception as e:
        return False, str(e)
    return True, 'success'
server.register_function(add_source,
        'add_source')

def killit():
    '''
    stops addon if internal kodi abort signal received
    '''
    global server
    m = xbmc.Monitor()
    m.waitForAbort()
    server.shutdown()

kthread = Thread(target=killit)

def kill_kodi():
    '''
    kills kodi via kill signal
    '''
    try:
        subprocess.Popen('kill_kodi.sh')
    except OSError:
        # for some reason the path info gets messed up sometimes....
        import inspect
        f = os.path.abspath(inspect.stack()[0][1])  # source [1]
        d = "/".join(f.split("/")[:-1]) + "/"
        subprocess.Popen(d+'kill_kodi.sh')
        return True
server.register_function(kill_kodi, 'kill_kodi')

def restart_kodi():
    '''
    runs bash script to retart kodi
    '''
    try:
        subprocess.Popen('restart_kodi.sh')
    except OSError:
        # for some reason the path info gets messed up sometimes....
        import inspect
        f = os.path.abspath(inspect.stack()[0][1])  # source [1]
        d = "/".join(f.split("/")[:-1]) + "/"
        subprocess.Popen(d+'restart_kodi.sh')
        return True
server.register_function(restart_kodi, 'restart_kodi')

def get_zip_addons_list():
    path = os.path.expanduser('~')+'/zip_addons/'
    return os.listdir(path)
server.register_function(get_zip_addons_list, 'get_zip_addons_list')

def save_url(url):
    global g_played_video
    g_played_video = url
    return True
server.register_function(save_url, 'save_url')

def in_simple_dialog():
    return xbmcgui.getCurrentWindowDialogId() in [10100, 12002]
server.register_function(in_simple_dialog, 'in_simple_dialog')

def is_downloading():
    cmd = ["netstat", "-ntu"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    out = [z.split()[4] for z in out.split('\n') if 'established' in z.lower()]
    return out
server.register_function(is_downloading, 'is_downloading')


def correct_video_data(video):
    if type(video) is not dict:
        video = json.loads(video)
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
            return json.dumps(video)
        if '%' in URL and not video['headers']:
            url = URL
            if 'url=' in url:
                url = [z for z in url.split('&') if 'url=' in z][0].split('url=')[1]
            try:
                url = urllib.unquote(url).decode('utf8')
            except:
                video['real_url'] = URL
                return json.dumps(video)
            video['real_url'] = url
            if url.startswith('https://') or url.startswith('http://') :
                try:
                    video.update(get_video_info(url))
                except:
                    pass
                return json.dumps(video)
        if '%' in URL and not video['headers']:
            url = URL
            if '=http' in url:
                url = [z for z in url.split('&') if '=http' in z][0].split('=http')[1]
                url = 'http' + url
            try:
                url = urllib.unquote(url).decode('utf8')
            except:
                video['real_url'] = URL
                return json.dumps(video)
            video['real_url'] = url
            if url.startswith('https://') or url.startswith('http://') :
                try:
                    video.update(get_video_info(url))
                except:
                    pass
                return json.dumps(video)
        if '://' in URL:
            video['real_url'] = URL
            return json.dumps(video)
        video['real_url'] = ''
    except:
        pass
    return json.dumps(video)
server.register_function(correct_video_data, 'correct_video_data')

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

def get_location(ip):
    try:
        GEOIP = geolite2.lookup(ip).to_dict()
        if GEOIP is None:
            GEOIP = {}
        else:
            GEOIP['subdivisions'] = list(GEOIP['subdivisions'])
    except:
        GEOIP = {}
    return GEOIP

def do_ffprobe(video):
    if type(video) is not dict:
        video = json.loads(video)
    if 'real_url' not in video:
        return json.dumps(video)
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
    return json.dumps(video)
server.register_function(do_ffprobe, 'do_ffprobe')

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
    ret = {
            'url': url,
            'ip': ip_url,
            'netloc': parsed_uri.netloc,
            'geoip': str(GEOIP),
            'headers': out2,
            'herrs': err2,
            }
    return json.dumps(ret)
server.register_function(proc_url, 'proc_url')


try:
    kthread.start()
    server.serve_forever()
except KeyboardInterrupt:
    pass
