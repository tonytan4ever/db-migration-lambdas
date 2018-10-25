import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import json
import base64
import hmac
import hashlib
import logging
import os
from bs4 import BeautifulSoup

## Inputs / Constants
# 'https://api.github.com/repos/foghorn-systems/docs.foghorn-systems.com'
github_url = os.environ['github_url']
gh_username = os.environ['gh_username']  # 'michael@goenvoy.co'
gh_token = os.environ['gh_token']  # '0174f7a02326199bb231d4048fd7ba6280e280ca'
zendesk_url = os.environ['zendesk_url']  # 'https://envoymichael.zendesk.com'
zendesk_username = os.environ['zendesk_username']  # 'michael@goenvoy.co'
zendesk_password = os.environ['zendesk_password']  # 'envoy'
secret = 'cY/HYUUpBWez1v3F41kJ5t0mYSNUvM82I+O4T7og'
verbose_logging = False
category_map = {}
section_map = {}
article_map = {}
xml_parser = "xml"

# backend authentication
# check the SHA-1 encryption w/ secret matches


def check_auth(content, signature):

    # verify the inbound request is from GitHub
    if not signature:
        return False
    sha_name, sig = signature.split('=')

    # "sha1" should be in Github header
    if sha_name != 'sha1':
        return False

    # compare the signature to the calculated hmac
    calculated_signature = hmac.new(
        secret.encode('UTF-8'),
        content.encode('UTF-8'),
        hashlib.sha1).hexdigest()
    if not hmac.compare_digest(calculated_signature, str(sig)):
        return False

    return True


def requests_retry_session(
    retries=3,
    backoff_factor=15,
    status_forcelist=(413, 429, 503, 500, 502, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


# process invoker
def lambda_handler(event, context):

    # perform authentication check
    authorized = True  # check_auth(event['rawBody'],event['X-Hub-Signature'])
    if not authorized:
        raise Exception("unauthorized")

    if 'ref' in event and event['ref'] != 'refs/heads/master':
        return {
            "statusCode": 204,
            "body": "Not a change on master"
        }

    # activate logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # parse contents
    data_received = event
    if verbose_logging:
        logger.info(data_received)

    # retrieve the full dita map
    ditamap = git_dita_map()

    # if full run, update all impacted files
    try:
        #full_run = event['headers']['full-run']
        update_all = event["queryStringParameters"]['updateall']
    except(KeyError, TypeError):
        update_all = False

    if update_all:

        # review every file in the map
        for category in ditamap['children']:

            category_name = category['title']

            for section in category['children']:

                if 'children' in section:

                    for article in section['children']:

                        section_name = section['title']
                        article_title = article['title']
                        article_href = article['href']
                        if '.ditamap' not in article_href:
                            raw_json = github_get(
                                github_url + '/contents/DITA/' + article_href)
                            article_content = decode_content(
                                raw_json['content'])
                            article_content = update_conrefs(
                                article_content)  # replace conrefs
                            try:
                                # convert article content to html
                                converted_content = convert_xml(
                                    article_content)
                            except BaseException:
                                log('xml conversion error: ' + article_href)
                                continue
                            create_or_update_zendesk_article(
                                category_name, section_name, article_title, 
                                converted_content)

                else:

                    section_name = category_name
                    article_title = section['title']
                    article_href = section['href']
                    if '.ditamap' not in article_href:
                        raw_json = github_get(
                            github_url + '/contents/DITA/' + article_href)
                        article_content = decode_content(raw_json['content'])
                        article_content = update_conrefs(
                            article_content)  # replace conrefs
                        try:

                            # convert article content to html
                            converted_content = convert_xml(article_content)
                        except BaseException:
                            log('xml conversion error: ' + article_href)
                            continue
                        create_or_update_zendesk_article(
                            category_name, section_name, article_title, converted_content)
    else:

        # review each commit from the webhook
        if 'commits' in data_received:
            commits = data_received['commits']
            for commit in commits:

                # fetch the commit
                commit_id = commit['id']
                commit_url = github_url + '/commits/' + commit_id
                full_commit = github_get(url=commit_url)
                if verbose_logging:
                    logger.info(full_commit)

                # identify impacted files
                impacted_files = full_commit['files']

                # get file contents
                for f in impacted_files:

                    # .ditamap files do not need to be checked
                    # they are handled through the get_dita_map function
                    if '.ditamap' not in f['filename']:

                        contents_url = f['contents_url']
                        if verbose_logging:
                            logger.info(contents_url)
                        content_json = github_get(url=contents_url)
                        content = decode_content(content_json['content'])

                        # check if each file is mapped
                        file_mapping = get_file_mapping(contents_url,
                                                        ditamap)
                        if file_mapping:

                            # get article title
                            article_title = get_article_title(content)

                            # replace conrefs
                            content = update_conrefs(content)

                            # convert article content to html
                            converted_content = convert_xml(content)

                            # identify category name & section name
                            category_name = file_mapping[0]
                            section_name = file_mapping[1] or category_name

                            # handle updated .dita file
                            create_or_update_zendesk_article(
                                category_name, section_name, article_title,
                                converted_content)

    # if delete allowed - delete unmapped categories/sections/articles
    try:
        delete_enabled = event["queryStringParameters"]['delete']
    except(KeyError, TypeError):
        delete_enabled = False

    if delete_enabled:

        category_dict = {}

        # delete extra categories
        for c in list_zendesk_categories():
            category_dict[c['id']] = c['name']
            if not is_category_mapped(ditamap, c['name']):
                delete_zendesk_item('categories', c['id'])

        # delete extra sections
        for s in list_zendesk_sections():
            category_name = category_dict[s['category_id']]
            if not is_section_mapped(ditamap, category_name, s['name']):
                delete_zendesk_item('sections', s['id'])

            # delete extra articles
            for a in list_zendesk_articles():
                if a['section_id'] == s['id']:
                    section_name = s['name']
                    if not is_article_mapped(
                            ditamap, category_name, section_name, a['title']):
                        delete_zendesk_item('articles', a['id'])

    return {
        "statusCode": 200,
        "body": "complete"
    }

# memoize stores function results for faster processing


class Memoize:
    def __init__(self, f):
        self.f = f
        self.memo = {}

    def __call__(self, *args):
        if args not in self.memo:
            self.memo[args] = self.f(*args)
        return self.memo[args]

# DELETE function for Zendesk


def delete_zendesk_item(obj_type, id):
    url = zendesk_url + '/api/v2/help_center/{}/{}.json'.format(obj_type, id)
    auth = (zendesk_username, zendesk_password)
    r = requests.delete(url=url, auth=auth)

# find category in ditamap


def is_category_mapped(ditamap, category_name):
    for category in ditamap['children']:
        if category['title'] == category_name:
            return True

# find section in ditamap


def is_section_mapped(ditamap, category_name, section_name):
    for category in ditamap['children']:
        if category['title'] == category_name:
            for section in category['children']:
                if section['title'] == section_name:
                    return True

# find article in ditamap


def is_article_mapped(ditamap, category_name, section_name, article_title):
    for category in ditamap['children']:
        if category['title'] == category_name:
            for section in category['children']:
                if section['title'] == section_name:
                    if 'children' in section:
                        for article in section['children']:
                            if article['title'] == article_title:
                                return True

# return a ditamap representation by recursively retrieving all ditamap files


def git_dita_map(path='fh_success_site.ditamap'):
    # return {"children":[]}
    ditamap_url = github_url + '/contents/DITA/' + path
    page_contents = github_get(ditamap_url)
    content = decode_content(page_contents['content'])
    soup = BeautifulSoup(content, features=xml_parser)
    map = soup.find('map')
    title = soup.find('title').text
    if map:
        topicrefs = map.findChildren('topicref', recursive=False)
        lst = []
        for topicref in topicrefs:
            if '.ditamap' in topicref['href']:
                lst.append(git_dita_map(topicref['href']))
            else:
                lst.append(git_dita_map(topicref['href']))
        return {'children': lst, 'title': title, 'href': path}
    return {'title': title, 'href': path}

# check if a file is in the ditamap
# due to the nature of the git_dita_map method, only .dita files need to
# be checked


def get_file_mapping(href, ditamap):
    if href and ditamap:
        for c in ditamap['children']:
            for s in c['children']:
                if 'children' in s:
                    for a in s['children']:
                        if a['href'] == href:
                            return (c['title'], s['title'])
                if s['href'] == href:
                    return (c['title'], '')

# retrieve the contents of an html/xml tag


def get_tag_content(raw_xml, tag_id):
    soup = BeautifulSoup(raw_xml, features='xml')
    tags = soup.findAll(attrs={"id": tag_id})
    return tags[0]

# replace xml conref content


def update_conrefs(raw_xml):

    soup = BeautifulSoup(raw_xml, features='xml')

    # find each conref
    for tag in soup.select('[conref]'):

        conref = tag['conref']  # conref attribute value

        try:
            path = conref.split("#")[0].replace(
                '../', '/')   # path to conref file
            tag_id = conref.split('/')[-1]  # conref tag id

        except(IndexError):
            # tag id or path is not available
            # todo: conref syntax incorrect - does this warrant logging an
            # error?
            continue

        # retrieve the file
        raw_json = github_get(github_url + '/contents/DITA' + path)

        try:
            file_content = decode_content(raw_json['content'])

        except(KeyError):
            # file not retrieved
            # todo: file not retrieved - does this warrant logging an error?
            return raw_xml

        try:
            # find the appropriate conref in the file
            tag_content = get_tag_content(file_content, tag_id)

            # update the original conref with the found content
            tag.replace_with(tag_content)

        except(IndexError):
            # todo: conref not found - does this warrant logging an error?
            continue

    return str(soup)

# convert dita xml to html


def convert_xml(raw_xml):

    def wrap(to_wrap, wrap_in):
        contents = to_wrap.replace_with(wrap_in)
        wrap_in.append(contents)

    raw_xml = raw_xml.replace('<?xml version="1.0" encoding="utf-8"?>', '')
    raw_xml = raw_xml.replace(
        '<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA Topic//EN" "topic.dtd">', '')

    soup = BeautifulSoup(raw_xml, features=xml_parser)

    # topic title
    topics = soup.find_all('topic')
    for topic in topics:
        titles = topic.findChildren('title', recursive=False)
        for ttl in titles:
            ttl.name = 'h1'
            ttl['class'] = "title topictitle1"

    # p, li, ol, ul
    for tag in ['p', 'li', 'ol', 'ul']:
        all = soup.find_all(tag)
        for i in all:
            i['class'] = tag

    # xref
    all = soup.find_all('xref')
    for i in all:
        i.name = 'a'
        i['class'] = 'xx'

    # note
    all = soup.find_all('note', attrs={'type': 'note'})
    for i in all:
        i.name = 'div'
        i['class'] = 'note note'
        new_tag = soup.new_tag('span', **{'class': 'notetitle'})
        new_tag.string = 'NOTE:'
        i.insert(1, new_tag)

    # tip
    all = soup.find_all('note', attrs={'type': 'tip'})
    for i in all:
        i.name = 'div'
        i['class'] = 'note tip'
        tip_tag = soup.new_tag('span', **{'class': 'tiptitle'})
        tip_tag.string = 'TIP:'
        i.insert(0, tip_tag)

    # <tm tmtype="tm">
    all = soup.find_all('tm', attrs={'tmtype': 'tm'})
    for i in all:
        s = i.text
        i.replaceWith(s + '™')

    # <tm tmtype="reg">
    all = soup.find_all('tm', attrs={'tmtype': 'reg'})
    for i in all:
        s = i.text
        i.replaceWith(s + '®')

    # warning
    all = soup.find_all('note', attrs={'type': 'warning'})
    for i in all:
        i.name = 'div'
        i['class'] = 'note warning note_warning'
        warning_tag = soup.new_tag('span', **{'class': 'note__title'})
        warning_tag.string = 'WARNING:'
        i.insert(0, warning_tag)

    # codeblock
    codeblocks = soup.find_all('codeblock')
    for codeblock in codeblocks:
        codeblock.name = 'code'
        pre_tag = soup.new_tag('pre')
        pre_tag['class'] = 'pre codeblock'
        wrap(codeblock, pre_tag)

    # codeph
    all = soup.find_all('codeph')
    for i in all:
        i.name = 'code'

    # userinput
    all = soup.find_all('userinput')
    for i in all:
        i.name = 'kbd'
        i['class'] = 'ph userinput'

    # b
    all = soup.find_all('b')
    for i in all:
        i.name = 'strong'
        i['class'] = 'ph b'

    # i
    all = soup.find_all('i')
    for i in all:
        i.name = 'em'
        i['class'] = 'ph i'

    # fig
    all = soup.find_all('fig')
    for i in all:
        i.name = 'figure'
        i['class'] = 'fig fignone'

        # fig title
        titles = i.findChildren('title', recursive=False)
        for t in titles:
            t.name = 'p'
            t['class'] = 'figcap'

        # fig span
        new_tag = soup.new_tag('span')
        new_tag['class'] = 'figtitleprefix'
        new_tag.string = "Figure: "
        if t:
            t.insert(0, new_tag)
        else:
            i.insert(0, new_tag)

    # image
    all = soup.find_all('image')
    for i in all:
        i.name = 'img'
        i['src'] = i['href']
        i['class'] = 'image'
        i.attrs = {'src': i['href'], 'class': 'image'}

    # tables
    count = 0
    all = soup.find_all('table')
    for tbl in all:
        count += 1
        tbl['class'] = 'tablenoborder'

        # caption
        cap = soup.new_tag('caption')

        # get the table's title
        title = ''
        titles = tbl.findChildren('title', recursive=False)
        for t in titles:
            title = t.string
            t.decompose()

        # add the first span
        span = soup.new_tag('span')
        span['class'] = 'tablecap'
        if title:
            span.string = title
        cap.insert(0, span)

        # table title span
        span2 = soup.new_tag('span')
        span2['class'] = 'table--title-label'
        span2.string = 'Table ' + str(count) + '.'
        span.insert(0, span2)
        tbl.insert(0, cap)

        # row > tr class="row"
        rows = tbl.findChildren('row')
        for row in rows:
            row.name = 'tr'
            row['class'] = 'row'

        # entry > th class="entry cellrowborder" style="text-align:left;"
        entries = tbl.findChildren('entry')
        for entry in entries:
            entry.name = 'th'
            entry['class'] = 'entry cellrowborder'
            entry['style'] = 'text-align:left;'

        # insert the whole table inside a div class="tablenoborder"
        new_tbl_div = soup.new_tag("div")
        new_tbl_div['class'] = 'tablenoborder'
        wrap(tbl, new_tbl_div)

    # section titles
    body = soup.find('body')
    for section in body.findChildren('section'):
        # title
        titles = section.findChildren('title', recursive=False)
        for ttl in titles:
            ttl.name = 'h2'
            ttl['class'] = 'title sectiontitle'

        # section > div
        section.name = 'div'
        section['class'] = 'section'

    # topic > body
    topics = soup.find_all('topic', recursive=False)
    for topic in topics:
        topic.name = 'body'
        # body > div class body
        for body in topic.findChildren('body', recursive=False):
            body.name = 'div'
            body['class'] = 'body'

    return str(soup)

# retrieve the title from the title xml element


def get_article_title(raw_xml):
    soup = BeautifulSoup(raw_xml, features=xml_parser)
    title = soup.find('title')
    if title:
        return title.text

# standard logging


def log(*args):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.info(':'.join([str(a) for a in args]))

# error reporting


def report_error(*args):
    # s = ':'.join([str(a) for a in args])
    log(args)
    # todo: error email notification?

# retrieve a github file by url or path


def github_get(url=github_url, path=''):
    auth = (gh_username, gh_token)
    if path:
        url = url + path
    r = requests.get(url=url, auth=auth)
    j = json.loads(r.text)
    return j


github_get = Memoize(github_get)

# decode github file contents


def decode_content(content):
    return base64.b64decode(content).decode("utf-8")

# zendesk response codes
# def response_codes(code):
    # if 199 < code < 300:
    # return 'success'
    # elif code == 404:
    # return 'not found'
    # elif code == 409:
    # return 'merge conflict'
    # elif code == 422:
    # return 'unprocessable entity'
    # elif code == 429:
    # return 'rate limited'
    # else:
    # return 'unhandled response code:' + code

# zendesk api handler


def zendesk_api_call(type, url, data=''):
    if 'http' not in url:
        url = zendesk_url + url
    s = requests.Session()
    s.auth = (zendesk_username, zendesk_password)
    s.headers.update({"Content-Type": "application/json"})
    try:
        if type == 'GET':
            r = requests_retry_session(sesssion=s).get(url=url)
            if 199 < r.status_code < 300:
                return True, r
            else:
                return False, r
        elif type == 'POST':
            data = json.dumps(data)
            r = requests_retry_session(sesssion=s).post(url=url, data=data)
            if 199 < r.status_code < 300:
                return True, r
            else:
                return False, r
        elif type == 'PUT':
            data = json.dumps(data)
            r = requests_retry_session(sesssion=s).put(url=url,
                                                       data=data)
            if 199 < r.status_code < 300:
                return True, r
            else:
                return False, r
        else:
            report_error(zendesk_api_call.__name__, 'Unsupported type', type)
    except Exception as e:
        log(str(e))
        return False, e

# create a Zendesk article


def create_zendesk_article(title, content, section_id):
    if not title or not content:
        report_error(
            create_zendesk_article.__name__,
            'missing title or content from github',
            title,
            content)
        return None
    url = '/api/v2/help_center/sections/{}/articles.json'.format(section_id)
    data = {"article": {"title": title, "body": str(content)}}  # <!-- -->
    success, r = zendesk_api_call('POST', url, data)
    if success:
        j = json.loads(r.text)
        article_id = j['article']['id']
        if section_id in article_map:
            article_map[section_id][title] = article_id
        else:
            article_map[section_id] = {title: article_id}
        log(create_zendesk_article.__name__, title, article_id)
        return article_id
    else:
        report_error(create_zendesk_article.__name__, r.status_code, r.text)

# create a Zendesk section


def create_zendesk_section(category_id, section_name):
    if not section_name or not category_id:
        report_error(
            create_zendesk_section.__name__,
            'missing section_name or category_id',
            section_name,
            category_id)
        return None
    url = '/api/v2/help_center/categories/{}/sections.json'.format(category_id)
    data = {"section": {"name": section_name}}
    success, r = zendesk_api_call('POST', url, data)
    if success:
        j = json.loads(r.text)
        section = j['section']
        section_id = section['id']
        section_key = title_to_key(section_name)
        if category_id in section_map:
            section_map[category_id][section_key] = section_id
        else:
            section_map[category_id] = {section_key: section_id}
        log(create_zendesk_section.__name__, section_key, section_id)
        return section_id
    else:
        report_error(create_zendesk_section.__name__, r.status_code, r.text)

# create a Zendesk category


def create_zendesk_category(category_name):
    if not category_name:
        report_error(
            create_zendesk_category.__name__,
            'missing category_name',
            category_name)
        return None
    url = '/api/v2/help_center/categories.json'
    data = {"category": {"name": category_name}}
    success, r = zendesk_api_call('POST', url, data)
    if success:
        j = json.loads(r.text)
        new_category = j['category']
        category_id = new_category['id']
        category_key = title_to_key(category_name)
        category_map[category_key] = category_id
        log(create_zendesk_category.__name__, category_name)
        return category_id
    else:
        report_error(create_zendesk_category.__name__, r.status_code, r.text)

# update a Zendesk article


def update_zendesk_article(title, content, article_id, locale="en-us"):
    if not title or not content or not article_id:
        report_error(update_zendesk_article.__name__,
                     'missing title or content from github', title,
                     content, article_id)
        return None
    url = '/api/v2/help_center/articles/{}/translations/{}.json'.format(
        article_id, locale)
    data = {"translation": {"title": title, "body": str(content)}}
    success, r = zendesk_api_call('PUT', url, data)
    if success:
        j = json.loads(r.text)
        translation_id = j['translation']['id']
        log(update_zendesk_article.__name__, title, translation_id)
        return translation_id
    else:
        report_error(update_zendesk_article.__name__, r.status_code, r.text)

# retrieve all Zendesk articles


def list_zendesk_articles(locale="en-us"):
    url = '/api/v2/help_center/{}/articles.json'.format(locale)
    articles = []
    while url:
        success, r = zendesk_api_call('GET', url)
        if success:
            j = json.loads(r.text)
            articles.extend(j['articles'])
            url = j['next_page']
        else:
            report_error(list_zendesk_articles.__name__, r.status_code, r.text)
            break
    for a in articles:
        article_id = a['id']
        section_id = a['section_id']
        article_title = a['title']
        if section_id in article_map:
            article_map[section_id][article_title] = article_id
        else:
            article_map[section_id] = {article_title: article_id}
    return articles


list_zendesk_articles = Memoize(list_zendesk_articles)

# retrieve all Zendesk sections


def list_zendesk_sections(locale="en-us"):
    url = '/api/v2/help_center/{}/sections.json'.format(locale)
    sections = []
    while url:
        success, r = zendesk_api_call('GET', url)
        if success:
            j = json.loads(r.text)
            sections.extend(j['sections'])
            url = j['next_page']
        else:
            report_error(list_zendesk_sections.__name__, r.status_code, r.text)
            break
    for s in sections:
        section_id = s['id']
        category_id = s['category_id']
        section_name = s['name']
        section_key = title_to_key(section_name)
        if category_id in section_map:
            section_map[category_id][section_key] = section_id
        else:
            section_map[category_id] = {section_key: section_id}
    return sections


list_zendesk_sections = Memoize(list_zendesk_sections)

# retrieve all Zendesk categories


def list_zendesk_categories(locale="en-us"):
    url = '/api/v2/help_center/{}/categories.json'.format(locale)
    categories = []
    while url:
        success, r = zendesk_api_call('GET', url)
        if success:
            j = json.loads(r.text)
            categories.extend(j['categories'])
            url = j['next_page']
        else:
            report_error(
                list_zendesk_categories.__name__,
                r.status_code,
                r.text)
            break
    for c in categories:
        category_key = title_to_key(c['name'])
        category_map[category_key] = c['id']
    return categories


list_zendesk_categories = Memoize(list_zendesk_categories)

# returns Zendesk article id


def zendesk_article_id(section_id, title):
    try:
        return article_map[section_id][title]
    except(KeyError):
        for a in list_zendesk_articles():
            if a['section_id'] == section_id:
                if a['title'].lower() == title.lower():
                    return a['id']

# returns Zendesk section id


def zendesk_section_id(category_id, section_name):
    section_key = title_to_key(section_name)
    try:
        return section_map[category_id][section_key]
    except(KeyError):
        for s in list_zendesk_sections():
            if s['category_id'] == category_id:
                if s['name'].lower() == section_name.lower():
                    return s['id']

# returns Zendesk article id


def zendesk_category_id(category_name):
    category_key = title_to_key(category_name)
    try:
        return category_map[category_key]
    except(KeyError):
        for c in list_zendesk_categories():
            if c['name'].lower() == category_name.lower():
                return c['id']

# convert a string a reliable dict key


def title_to_key(title):
    return title.strip().replace(' ', '_').lower()

# creates categories,sections,articles


def create_or_update_zendesk_article(
        category_name,
        section_name,
        article_title,
        article_content):

    # search for the category the section should be in
    category_id = zendesk_category_id(category_name)

    if not category_id:

        # create the category
        category_id = create_zendesk_category(category_name)

    # search for the section it should be in
    section_id = zendesk_section_id(category_id, section_name)

    if not section_id:

        # create the section
        section_id = create_zendesk_section(category_id, section_name)

    # search for the existing article
    article_id = zendesk_article_id(section_id, article_title)

    if article_id:

        # update the article with this new content
        update_zendesk_article(article_title, article_content, article_id)

    else:

        # create the article in Zendesk under this section
        create_zendesk_article(article_title, article_content, section_id)

    # todo: add error logging to this function (e.g. if category not created)
