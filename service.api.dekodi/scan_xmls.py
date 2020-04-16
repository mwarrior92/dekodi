import os
import json
import xml.etree.ElementTree as xee

def find_addonxmls():
    root = '/home/dekodi/.kodi/addons/'
    dirs = os.listdir(root)
    xmls = list()
    for d in dirs:
        for _, _, files in os.walk(root+d):
            if 'addon.xml' in files:
                xmls.append(root+d+'/addon.xml')
            break
    return xmls


def parse_xmls(xmls):
    data = list()
    for fname in xmls:
        root = xee.parse(fname).getroot()
        ainfo = dict()
        ainfo['reqs'] = list()
        for elem in root.iter():
            if elem.tag == 'addon':
                ainfo.update(elem.attrib)
            elif elem.tag == 'import':
                ainfo['reqs'].append(elem.attrib)
            elif elem.tag in ['license', 'platform', 'language', 'website', 'forum', 'email', 'source']:
                ainfo[elem.tag] = ''.join(elem.itertext())
            elif 'lang' in elem.attrib:
                if 'lang' not in ainfo:
                    ainfo['lang'] = set()
                ainfo['lang'].add(elem.attrib['lang'])
        data.append(ainfo)
    for i in range(len(data)):
        if 'lang' in data[i]:
            data[i]['lang'] = list(data[i]['lang'])
    return data


if __name__ == '__main__':
    try:
        gxmls = find_addonxmls()
        gdata = parse_xmls(gxmls)
        with open('/home/dekodi/output/addon_metadata.xml', 'w') as f:
            json.dump(gdata, f)
    except Exception as e:
        with open('/home/dekodi/output/addon_metadata.xml', 'w') as f:
            f.write(str(e))
