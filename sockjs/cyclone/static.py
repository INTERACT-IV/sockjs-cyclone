""" Various static handlers required for SockJS to function properly. """

import hashlib
import random
import sys

from sockjs.cyclone.basehandler import BaseHandler, PreflightHandler
from sockjs.cyclone.proto import json_encode


IFRAME_TEXT = '''<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <script>
    document.domain = document.domain;
    _sockjs_onload = function(){SockJS.bootstrap_iframe();};
  </script>
  <script src="%s"></script>
</head>
<body>
  <h2>Don't panic!</h2>
  <p>This is a SockJS hidden iframe. It's used for cross domain magic.</p>
</body>
</html>'''.strip()


class IFrameHandler(BaseHandler):
    """ SockJS IFrame page handler """

    def get(self):
        data = IFRAME_TEXT % self.server.settings['sockjs_url']

        hsh = hashlib.md5(data).hexdigest()

        value = self.request.headers.get('If-None-Match')
        if value:
            if value.find(hsh) != -1:
                # TODO: Fix me? Right now it is a hack to remove content-type
                # header
                self.clear()
                del self._headers['Content-Type']

                self.set_status(304)
                return

        self.enable_cache()

        self.set_header('Etag', hsh)
        self.write(data)


class GreetingsHandler(BaseHandler):
    """ SockJS greetings page handler """

    def initialize(self, server):
        self.server = server

    def get(self):
        self.enable_cache()

        self.set_header('Content-Type', 'text/plain; charset=UTF-8')
        self.write('Welcome to SockJS!\n')


class ChunkingTestHandler(PreflightHandler):
    """ SockJS chunking test handler """
    # FIXME
    pass


class InfoHandler(PreflightHandler):
    """ SockJS 0.2+ /info handler """
    def initialize(self, server):
        self.server = server
        self.access_methods = 'OPTIONS, GET'

    def get(self):
        self.preflight()
        self.disable_cache()
        self.set_header('Content-Type', 'application/json; charset=UTF-8')

        options = dict(websocket=self.server.websockets_enabled,
                       cookie_needed=self.server.cookie_needed,
                       origins=['*:*'],
                       entropy=random.randint(0, sys.maxint))

        self.write(json_encode(options))

