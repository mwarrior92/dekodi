import sys
import hashlib
import os
import xml.etree.ElementTree as xee
import json

BLOCKSIZE = 65536

def single_file_hash(fname, hasher=None, txt=False):
    if hasher is None:
        hasher = hashlib.sha1()
    with open(fname, 'rb') as afile:
        buf = afile.read(BLOCKSIZE)
        while len(buf) > 0:
            hasher.update(buf)
            buf = afile.read(BLOCKSIZE)
    if txt:
        return hasher.hexdigest()
    else:
        return hasher


def multi_file_hash(fnames):
    fnames = sorted(fnames)
    hasher = hashlib.sha1()
    for fname in fnames:
        hasher = single_file_hash(fname, hasher)
    return hasher.hexdigest()


def get_aid(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'addon':
                return root.get('id') if root.get('id') else ''
    except:
        return ''


def get_name(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'addon':
                return root.get('name') if root.get('name') else ''
    except:
        return ''


def get_addon_name(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'addon':
                return root.get('name') if root.get('name') else ''
    except:
        return ''

def get_content_types(fname):
    root = xee.parse(fname).getroot()
    provides = list()
    try:
        for elem in root.iter():
            if elem.tag == 'provides':
                try:
                    provides.append(elem.text)
                except:
                    continue
    except:
        pass
    return provides

def get_addon_types(fname):
    root = xee.parse(fname).getroot()
    points = list()
    try:
        for elem in root.iter():
            if elem.tag == 'extension':
                try:
                    points.append(elem.get('point') if elem.get('point') else '')
                except:
                    continue
    except:
        pass
    return points


def get_platform(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'platform':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_description(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'description':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_summary(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'summary':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_license(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'license':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_forum(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'forum':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_website(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'website':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_email(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'email':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_source(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'source':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_news(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'news':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_disclaimer(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'disclaimer':
                return elem.text if elem.text else ''
    except:
        pass
    return ''

def get_optional_stuff(fname):
    d = dict()
    for z in ['platform', 'license', 'forum', 'website', 'email', 'source', 'news']:
        tmp = globals()['get_'+z](fname)
        if tmp:
            d[z] = tmp
    return d

def get_dependencies(fname):
    deps = list()
    try:
        root = xee.parse(fname).getroot()
        for elem in root.iter():
            if elem.tag == 'import':
                v = elem.get('version')
                v = v if v else '-1'
                deps.append({'addonid': elem.get('addon') if elem.get('addon') else '', 'version': v})
    except:
        pass
    return deps


def get_version(fname):
    root = xee.parse(fname).getroot()
    try:
        for elem in root.iter():
            if elem.tag == 'addon':
                return elem.get('version') if elem.get('version') else ''
    except:
        return 'unknown'


def dir_walk_hash(path):
    fnames = list()
    for root, dirs, files in os.walk(path):
        root = root + '/'
        for f in files:
            fnames.append(root+f)
    return multi_file_hash(fnames)


def addons_hashes(path):
    hashes = list()
    for root, dirs, files in os.walk(path):
        root = root + '/'
        if 'addon.xml' in files:
            aid = get_aid(root+'addon.xml')
            version = get_version(root+'addon.xml')
            h = dir_walk_hash(root)
            hashes.append((aid, version, root, h))
    return hashes


def text_hash(txt, hasher=None):
    if hasher is None:
        hasher = hashlib.sha1()
    hasher.update(txt)
    return hasher.hexdigest()


def generate_addonsxml(name, path, url):
    with open(path+'addon.xml','w') as f:
        f.write('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="'''+name+'''" name="" version="-1" provider-name="">
<extension point="xbmc.addon.repository">
    <info>'''+url+'''addons.xml</info>
    <checksum>'''+url+'''addons.xml.md5</checksum>
    <datadir>'''+url+'''addons/</datadir>
  </extension>
</addon>''')


if __name__ == '__main__':
    g_hashes = addons_hashes(sys.argv[1])
    with open(sys.argv[2], 'w') as f:
        json.dump(g_hashes, f)
