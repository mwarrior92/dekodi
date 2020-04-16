import os
import requests
import json
import time
from collections import defaultdict
import zlib
import xml.etree.cElementTree as ET
import multiprocessing
from timeout_wrapper import time_wrapper

cred = ()
rate_reset = -1
ratelimit_limit = -1


def get_cred(fname='git_api.config'):
    ''' load credentials '''
    global cred
    if not cred:
        with open(fname, 'r') as f:
            data = json.load(f)
        cred = (data['git_user'], data['git_pw'])
    return cred


def github_get(url, **kwargs):
    if 'auth' not in kwargs:
        kwargs['auth'] = get_cred()
    res = time_wrapper(requests.get, (url,), kwargs)
    if res and res.status_code == 403 and int(res.headers['X-RateLimit-Remaining']) == 0:
        print(res.status_code)
        print(res.headers)
        wait = int(res.headers['X-RateLimit-Reset']) - time.time()
        time.sleep(wait + 1)
        res = time_wrapper(requests.get, (url,), kwargs)
    if res and res.status_code != 200:
        return None
    return res


def next_page_link(links):
    links = links.replace('"', '')
    links = links.split(', ')
    links = [z.split('; ') for z in links]
    links = {k.split('=')[-1]: link for link,k in links}
    if 'next' in links:
        return links['next'][1:-1] # remove angle brackets
    return ''


def search_github(query_str, **params):
    ''' searches public code on git for given string (qstr) '''
    params['q'] = query_str
    params['per_page'] = 100
    url = 'https://api.github.com/search/code?'
    return github_get(url, params=params)


def get_repo(repo, user=None, **params):
    '''
    NOTE: if user is None, assumes repo is formatted "user/repo"
    '''
    if user:
        repo = user+'/'+repo
    url = 'https://api.github.com/repos/'+repo
    return github_get(url, params=params)


def get_search_results(res):
    items = list()
    items = res.json()['items']
    return items


def get_tree(item=None, repo=None):
    if item:
        sha = item['html_url'].split('blob/')[-1].split('/')[0]
        tree_url = item['repository']['trees_url'].split('{')[0]+'/'+sha+'?recursive=1'
    else:
        repo = repo.json()
        sha = get_sha(repo['full_name'])
        tree_url = repo['trees_url'].split('{')[0]+'/'+sha+'?recursive=1'
    try:
        tree = github_get(tree_url)
        return tree.json()['tree']
    except:
        return []


def tree_to_dirs(tree):
    dirs = defaultdict(list)
    for item in tree:
        pieces = item['path'].split('/')
        f = pieces.pop(-1)
        if pieces:
            dirs['/'.join(pieces)].append(f)
        else:
            dirs['__ROOT__'].append(item['path'])
    return dirs


def is_repo(item=None, repo=None):
    tree = get_tree(item, repo)
    dirs = tree_to_dirs(tree)
    if repo:
        repo = repo.json()
    else:
        repo = item['repository']
    xmlfiles =  [z['path'] for z in tree if os.path.basename(z['path']) == 'addons.xml']
    return xmlfiles


def contains_addon(item=None, repo=None):
    tree = get_tree(item, repo)
    addons = ['/'.join(z['path'].split('/')[:-1]) for z in tree if z['path'].endswith('addon.xml') or z['path'].endswith('addon.xml.gz')]
    return addons


def get_archive_link(item=None, repo=None):
    if item:
        return item['repository']['url']+'/zipball'
    else:
        return repo.json()['url']+'/zipball'


def get_next_result(query=None, res=None, **kwargs):
    if res is None:
        return search_github(query, **kwargs)
    nextpage = next_page_link(res.headers['Link'])
    if nextpage:
        return github_get(nextpage)
    return None


def get_repo_size(item=None, repo=None):
    if item:
        repo = github_get(item['repository']['url'])
    print(repo.json())
    return repo.json()['size']

def construct_xml_a(repo, dst):
    xmlfiles = is_repo(repo=repo)
    name = repo.json()['full_name']
    if xmlfiles:
        path = '/'.join(min(xmlfiles, key=lambda z: len(z)).split('/')[:-1])
        url = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml'
        url2 = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml.m5'
        url3 = 'https://raw.githubusercontent.com/'+name+'/master/'+path
        print(url3)
    if not os.path.exists(dst):
        os.makedirs(dst)
    with open(dst+'addon.xml','w') as f:
        f.write('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="dekodi.tmp.addon.a" name="'''+name+'''" version="-1" provider-name="dekodi">
<extension point="xbmc.addon.repository">
    <info compressed="false">'''+url+'''</info>
    <checksum>'''+url2+'''</checksum>
    <datadir zip="true">'''+url3+'''</datadir>
  </extension>
</addon>''')


def construct_xml_b(repo, dst):
    xmlfiles = is_repo(repo=repo)
    name = repo.json()['full_name']
    if xmlfiles:
        path = '/'.join(min(xmlfiles, key=lambda z: len(z)).split('/')[:-1])
        url = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml'
        url2 = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml.m5'
        url3 = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons/'
        print(url3)
    if not os.path.exists(dst):
        os.makedirs(dst)
    with open(dst+'addon.xml','w') as f:
        f.write('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="dekodi.tmp.addon.b" name="'''+name+'''" version="-1" provider-name="dekodi">
<extension point="xbmc.addon.repository">
    <info compressed="false">'''+url+'''</info>
    <checksum>'''+url2+'''</checksum>
    <datadir zip="true">'''+url3+'''</datadir>
  </extension>
</addon>''')

def construct_xml_c(repo, dst):
    xmlfiles = is_repo(repo=repo)
    name = repo.json()['full_name']
    if xmlfiles:
        path = '/'.join(min(xmlfiles, key=lambda z: len(z)).split('/')[:-1])
        url = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml'
        url2 = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'addons.xml.m5'
        url3 = 'https://raw.githubusercontent.com/'+name+'/master/'+path+'zips/'
        print(url3)
    if not os.path.exists(dst):
        os.makedirs(dst)
    with open(dst+'addon.xml','w') as f:
        f.write('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="dekodi.tmp.addon.c" name="'''+name+'''" version="-1" provider-name="dekodi">
<extension point="xbmc.addon.repository">
    <info>'''+url+'''</info>
    <checksum>'''+url2+'''</checksum>
    <datadir>'''+url3+'''</datadir>
  </extension>
</addon>''')


def find_addons(queries=[('import addon "xbmc.python"', {'l': 'XML'})]):
    paths = dict()
    addons = dict()
    new_addons = dict()
    if os.path.exists('addon_sources.json'):
        with open('addon_sources.json', 'r') as f:
            for line in f:
                if line:
                    tmp = json.loads(line).items()
                    name, data = tmp[0]
                    addons[name] = data
    for query, kwargs in queries:
        res = get_next_result(query, **kwargs)
        while res:
            items = get_search_results(res)
            for item in items:
                print(item['repository']['name'])
                addon_paths = contains_addon(item)
                if not addon_paths:
                    print('failed on '+item['repository']['name'])
                    continue
                url = get_archive_link(item)
                new_addons[item['repository']['name']+'_FRMGTHB_'] = ['from_github', url]
                paths[url] = addon_paths
                print(url)
                print((item['repository']['name'], ('from_github', url)))
            print('sleeping...')
            time.sleep(30)
            res = get_next_result(res=res, **kwargs)
    addons.update(new_addons)
    return [{item[0]: item[1]} for item in addons.items()], paths


def url_to_repo(url):
    if '://' not in url:
        return None
    offset = 0
    if 'zipball' in url:
        offset = 1
    body = url.split('://')[1]
    chunks = body.split('/')
    if len(chunks) < 3 or not chunks[0].endswith('github.com'):
        return None
    return '/'.join(chunks[1+offset:3+offset])


def get_sha(repo, user=None):
    if user:
        repo = user+'/'+repo
    print(repo)
    commits = github_get('https://api.github.com/repos/'+repo+'/git/refs/')
    if commits:
        final = commits.json()[-1]
        return final['object']['sha']
    return 'bla'


def process_url(url):
    repostr = url_to_repo(url)
    if not repostr:
        return None
    repo = get_repo(repostr)
    if not repo:
        return None
    addon_paths = contains_addon(repo=repo)
    if not addon_paths:
        return None
    url = get_archive_link(repo=repo)
    repod = repo.json()
    return {'url': url, 'paths': addon_paths}


if __name__ == '__main__':
    print(find_addons())
