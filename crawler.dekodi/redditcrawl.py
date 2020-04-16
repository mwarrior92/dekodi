import re
import praw
import json
import requests
from bs4 import BeautifulSoup
from bs4.dammit import EncodingDetector
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import time
import gitcrawl
import os
from timeout_wrapper import time_wrapper


g_visited = set()


with open('reddit_api.config', 'r') as f:
    g_config = json.load(f)


def get_instance():
    return praw.Reddit(**g_config)


def search_reddit(query, subreddit='all'):
    red = get_instance()
    return red.subreddit(subreddit).search(query, limit=None)


def gettext(comment):
    try:
        return comment.body
    except:
        try:
            return comment.selftext
        except:
            return None


def get_links(comment):
    text = gettext(comment)
    if text:
        links = set(re.findall("(?<=\s|\()http[^\s(),']+(?=\s|\))", text))
        links.update(re.findall("(?<=[\s('\"])[^\s(),.'\"*]+\.?[^\s(),.'*]+\.[^\s(),.'*]+\/[^\s(),'\"]*(?=[\s)\"'])", text))
        return links
    return set()


def swap_for_redirect(url):
    resp = time_wrapper(requests.head, (url,), {'allow_redirects': False}, t=3)
    if resp and resp.status_code >= 300:
        resp = time_wrapper(requests.get, (url,), {'allow_redirects': False}, t=3)
        if resp and 299 < resp.status_code < 400:
            return resp.headers['Location']
    return url


def scrape_page(url):
    '''
    NOTE: this throws away any links that can't be addons (ie: assumes we're not going any deeper)
    '''
    resp = None
    links = set()
    if url.endswith('.jpg') or url.endswith('.png') or url.endswith('.gif') or url.endswith('.rar'):
        return set()
    head = time_wrapper(requests.head, (url,), t=3)
    if head:
        try:
            cl = int(head.headers['Content-Length'])
        except:
            cl = -1
        if cl < 1000000:
            resp = time_wrapper(requests.get, (url,), t=3)
    if not resp:
        return set()
    netloc = urlparse(url).netloc.split(':')[0]
    http_encoding = resp.encoding if 'charset' in resp.headers.get('content-type', '').lower() else None
    html_encoding = EncodingDetector.find_declared_encoding(resp.content, is_html=True)
    encoding = html_encoding or http_encoding
    soup = BeautifulSoup(resp.content, from_encoding=encoding)
    for link in soup.find_all('a', href=True):
        if ".zip" in link['href'] or 'github' in link['href']:
            href = link['href']
            if not href.startswith('http'):
                href = 'http://'+netloc+'/'+href
            if can_be_repo(href):
                links.add(href)
    return links


def can_be_repo(url):
    if '//' in url:
        try:
            tmp = url.split('//')[1]
            netloc = urlparse(url).netloc.split(':')[0]
            if (len(tmp.split('.')) > 2 or len(tmp.split('/')) > 2) \
                    and not netloc.endswith('.zip') and \
                    (url.endswith('.zip') or 'github' in netloc):
                return True
        except:
            pass
    return False


def process_link(link):
    '''
    goes from raw link to direct link to plugin
    - check if raw link is a zip file; if so, return that. otherwise:
    - open raw link with selenium
    - scan page for anything pointing at a zip file. if so, return that. otherwise:
    - give up, return None
    '''
    print('REDDIT: '+link)
    global g_visited
    if can_be_repo(link):
        return {link}
    dlink = swap_for_redirect(link) # get direct link
    links = set()
    for link in scrape_page(dlink):
        if link not in g_visited:
            try:
                print(link)
                g_visited.add(link)
                dlink = swap_for_redirect(link)
                if can_be_repo(dlink):
                    links.add(dlink)
            except:
                continue
    return links


def crawl_reddit(queries=['selftext:Kodi', 'selftext:xbmc', 'selftext:XBMC', 'selftext:Xbmc', 'selftext:kodi', 'url:kodi', 'url:xbmc'], subreddit='all'):
    addons = dict()
    if os.path.exists('addon_sources.json'):
        with open('addon_sources.json', 'r') as f:
            for line in f:
                if line:
                    tmp = json.loads(line).items()
                    name, data = tmp[0]
                    addons[name] = data
    subs = set()
    all_links = set()
    ids = set()
    global g_visited
    g_visited = set()
    print('LOOP 1')
    for query in queries:
        submissions = search_reddit(query, subreddit)
        if not submissions:
            continue
        for submission in submissions:
            if submission.id in ids:
                continue
            ids.add(submission.id)
            oldlen = len(all_links)
            print(submission.title)
            links = set()
            tmp_links = get_links(submission)
            [links.update(process_link(link)) for link in tmp_links if link not in g_visited]
            g_visited.update(tmp_links)
            all_links.update([z for z in links if z])
            for comment in submission.comments.list():
                links = set()
                tmp_links = get_links(comment)
                [links.update(process_link(link)) for link in tmp_links if link not in g_visited]
                g_visited.update(tmp_links)
                all_links.update([z for z in links if z])
            if len(all_links) > oldlen:
                # if we found something useful, remember this subreddit
                subs.add(submission.subreddit.display_name)
                print('links: '+str(len(all_links)))
    # repeat for each specific subreddit to get more results
    print('LOOP 2')
    for subreddit in subs:
        for query in queries:
            submissions = search_reddit(query, subreddit)
            if not submissions:
                continue
            for submission in submissions:
                if submission.id in ids:
                    continue
                oldlen = len(all_links)
                print(submission.title)
                ids.add(submission.id)
                links = set()
                tmp_links = get_links(submission)
                [links.update(process_link(link)) for link in tmp_links if link not in g_visited]
                g_visited.update(tmp_links)
                all_links.update([z for z in links if z])
                for comment in submission.comments.list():
                    links = set()
                    tmp_links = get_links(comment)
                    [links.update(process_link(link)) for link in tmp_links if link not in g_visited]
                    g_visited.update(tmp_links)
                    all_links.update([z for z in links if z])
                if len(all_links) > oldlen:
                    print('links: '+str(len(all_links)))
    pathsd = dict()
    count = 0
    new_addons = dict()
    for link in all_links:
        netloc = urlparse(link).netloc.split(':')[0]
        aid = None
        if link.endswith('.zip'):
            aid = link+'_FRMRDT_'
            data = ['from_zip', link]
            count += 1
        if aid:
            new_addons[aid] = data
            count += 1
        aid = None
        if 'github.com' in netloc:
            res = None
            try:
                res = gitcrawl.process_url(link)
            except Exception as e:
                print(e)
                continue
            if res:
                print('ADDED '+str(res))
                aid = res['url']+'_FRMRDT_'
                data = ['from_github', res['url']]
                paths = res['paths']
                pathsd[res['url']] = paths
            else:
                continue
        if aid:
            new_addons[aid] = data
            count += 1
    addons.update(new_addons)
    print('count: '+str(count))
    return [{item[0]: item[1]} for item in addons.items()], pathsd

if __name__ == '__main__':
    gt = time.time()
    g_links, g_pathsd = crawl_reddit()
    with open('reddit_sources.json', 'w') as f:
        for line in g_links:
            f.write(json.dumps(line)+'\n')
    print(time.time() - gt)
