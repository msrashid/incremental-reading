from ssl import _create_unverified_context
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from anki.notes import Note
from anki.utils import isMac, isWin
from aqt import mw
from aqt.utils import askUser, getText, openLink, showWarning

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView,
                             QDialog,
                             QDialogButtonBox,
                             QListWidget,
                             QListWidgetItem,
                             QVBoxLayout)

from bs4 import BeautifulSoup
from requests import get, post

from .lib.feedparser import parse

from .util import setField


class Importer:
    def __init__(self, settings):
        self.settings = settings
        self.log = self.settings['feedLog']

    def _fetchWebpage(self, url):
        if not urlsplit(url).scheme:
            url = 'http://' + url

        if isMac:
            context = _create_unverified_context()
            html = urlopen(url, context=context).read().decode('utf-8')
        else:
            headers = {'User-Agent': self.settings['userAgent']}
            html = get(url, headers=headers).text

        webpage = BeautifulSoup(html, 'html.parser')

        for tagName in self.settings['badTags']:
            for tag in webpage.find_all(tagName):
                tag.decompose()

        return webpage

    def _createNote(self, title, text, source):
        did = mw.col.conf['curDeck']
        model = mw.col.models.byName(self.settings['modelName'])
        note = Note(mw.col, model)
        setField(note, self.settings['titleField'], title)
        setField(note, self.settings['textField'], text)
        setField(note, self.settings['sourceField'], source)
        note.model()['did'] = did
        mw.col.addNote(note)
        mw.deckBrowser.show()

    def importWebpage(self, url=None):
        if not url:
            url, accepted = getText('Enter URL:', title='Import Webpage')
        else:
            accepted = True

        if not url or not accepted:
            return

        try:
            webpage = self._fetchWebpage(url)
        except HTTPError as error:
            showWarning('The remote server has returned an error:'
                        ' HTTP Error {} ({})'.format(
                            error.code,
                            error.reason))
            return

        body = '\n'.join(map(str, webpage.find('body').children))
        self._createNote(webpage.title.string, body, url)

    def importFeed(self):
        url, accepted = getText('Enter URL:', title='Import Feed')

        if not url or not accepted:
            return

        if not urlsplit(url).scheme:
            url = 'http://' + url

        try:
            feed = parse(url,
                         etag=self.log[url]['etag'],
                         modified=self.log[url]['modified'])
        except KeyError:
            self.log[url] = {'downloaded': []}
            feed = parse(url)

        entries = [{'text': e['title'], 'data': e} for e in feed['entries']]
        selected = self._select(entries)

        if selected:
            mw.progress.start(label='Importing feed entries...',
                              max=len(selected),
                              immediate=True)

            for i, entry in enumerate(selected, start=1):
                if not entry['link'] in self.log[url]['downloaded']:
                    self.importWebpage(entry['link'])
                    self.log[url]['downloaded'].append(entry['link'])
                mw.progress.update(value=i)

            self.log[url]['etag'] = feed.etag if hasattr(feed, 'etag') else ''
            self.log[url]['modified'] = (feed.modified
                                         if hasattr(feed, 'modified')
                                         else '')

            mw.progress.finish()

    def importPocket(self):
        redirectUri = 'https://github.com/luoliyan/incremental-reading-for-anki'

        if isWin:
            consumerKey = '71462-da4f02100e7e381cbc4a86df'
        elif isMac:
            consumerKey = '71462-ed224e5a561a545814023bf9'
        else:
            consumerKey = '71462-05fb63bf0314903c7e73c52f'

        response = post('https://getpocket.com/v3/oauth/request',
                        json={'consumer_key': consumerKey,
                              'redirect_uri': redirectUri},
                        headers={'X-Accept': 'application/json'})

        requestToken = response.json()['code']

        authUrl = 'https://getpocket.com/auth/authorize?'
        authParams = {'request_token': requestToken,
                      'redirect_uri': redirectUri}

        openLink(authUrl + urlencode(authParams))
        if not askUser('I have authenticated with Pocket.'):
            return

        response = post('https://getpocket.com/v3/oauth/authorize',
                        json={'consumer_key': consumerKey,
                              'code': requestToken},
                        headers={'X-Accept': 'application/json'})

        accessToken = response.json()['access_token']

        response = post('https://getpocket.com/v3/get',
                        json={'consumer_key': consumerKey,
                              'access_token': accessToken,
                              'contentType': 'article',
                              'count': 30,
                              'detailType': 'complete',
                              'sort': 'newest'},
                        headers={'X-Accept': 'application/json'})

        articles = [{'text': a['resolved_title'], 'data': a}
                    for a in response.json()['list'].values()]

        selected = self._select(articles)

        if selected:
            mw.progress.start(label='Importing Pocket articles...',
                              max=len(selected),
                              immediate=True)

            for i, article in enumerate(selected, start=1):
                self.importWebpage(article['given_url'])
                mw.progress.update(value=i)

            mw.progress.finish()

    def _select(self, choices):
        if not choices:
            return []

        dialog = QDialog(mw)
        layout = QVBoxLayout()
        listWidget = QListWidget()
        listWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)

        for c in choices:
            item = QListWidgetItem(c['text'])
            item.setData(Qt.UserRole, c['data'])
            listWidget.addItem(item)

        buttonBox = QDialogButtonBox(QDialogButtonBox.Close |
                                     QDialogButtonBox.Save)
        buttonBox.accepted.connect(dialog.accept)
        buttonBox.rejected.connect(dialog.reject)
        buttonBox.setOrientation(Qt.Horizontal)

        layout.addWidget(listWidget)
        layout.addWidget(buttonBox)

        dialog.setLayout(layout)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.resize(500, 500)
        choice = dialog.exec_()

        if choice == 1:
            return [listWidget.item(i).data(Qt.UserRole)
                    for i in range(listWidget.count())
                    if listWidget.item(i).isSelected()]
        else:
            return []
