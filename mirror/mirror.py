#!/usr/bin/env python
# Copyright 2008-2014 Brett Slatkin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import pickle
from models import Fiddle

__author__ = "Brett Slatkin (bslatkin@gmail.com)"

import hashlib
import logging
import urllib

from google.appengine.api import memcache
from google.appengine.api import urlfetch
import webapp2
from google.appengine.ext.webapp import template
from google.appengine.runtime import apiproxy_errors

import transform_content


# ##############################################################################

DEBUG = False
EXPIRATION_DELTA_SECONDS = 3600

# DEBUG = True
# EXPIRATION_DELTA_SECONDS = 1

HTTP_PREFIX = "http://"

IGNORE_HEADERS = frozenset([
    "set-cookie",
    "expires",
    "cache-control",

    # Ignore hop-by-hop headers
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
])

TRANSFORMED_CONTENT_TYPES = frozenset([
    "text/html",
    "text/css",
])

MAX_CONTENT_SIZE = 10 ** 64

###############################################################################

def get_url_key_name(url):
    url_hash = hashlib.sha256()
    url_hash.update(url)
    return "hash_" + url_hash.hexdigest()


###############################################################################

class MirroredContent(object):
    def __init__(self, original_address, translated_address,
                 status, headers, data, base_url):
        self.original_address = original_address
        self.translated_address = translated_address
        self.status = status
        self.headers = headers
        self.data = data
        self.base_url = base_url


    @staticmethod
    def store(key, value, chunksize=950000):
        serialized = pickle.dumps(value, 2)
        values = {}
        for i in xrange(0, len(serialized), chunksize):
            values['%s.%s' % (key, i // chunksize)] = serialized[i: i + chunksize]
        return memcache.set_multi(values, time=EXPIRATION_DELTA_SECONDS)

    @staticmethod
    def retrieve(key):
        result = memcache.get_multi(['%s.%s' % (key, i) for i in xrange(32)])
        serialized = ''.join([v for k, v in sorted(result.items()) if v is not None])
        return pickle.loads(serialized)

    @staticmethod
    def fetch_and_store(key_name, base_url, root_url, translated_address, mirrored_url):
        """Fetch and cache a page.

        Args:
          key_name: Hash to use to store the cached page.
          base_url: The hostname of the page that's being mirrored.
          translated_address: The URL of the mirrored page on this site.
          mirrored_url: The URL of the original page. Hostname should match
            the base_url.

        Returns:
          A new MirroredContent object, if the page was successfully retrieved.
          None if any errors occurred or the content could not be retrieved.
        """
        logging.debug("Fetching '%s'", mirrored_url)
        try:
            response = urlfetch.fetch(mirrored_url)
        except (urlfetch.Error, apiproxy_errors.Error):
            logging.exception("Could not fetch URL")
            return None

        adjusted_headers = {}
        for key, value in response.headers.iteritems():
            adjusted_key = key.lower()
            if adjusted_key not in IGNORE_HEADERS:
                adjusted_headers[adjusted_key] = value

        content = response.content
        page_content_type = adjusted_headers.get("content-type", "")
        for content_type in TRANSFORMED_CONTENT_TYPES:
            # startswith() because there could be a 'charset=UTF-8' in the header.
            if page_content_type.startswith(content_type):
                content = transform_content.TransformContent(base_url, root_url, mirrored_url,
                                                             content)
                break

        # If the transformed content is over 1MB, truncate it (yikes!)
        if len(content) > MAX_CONTENT_SIZE:
            logging.warning("Content is over 1MB; truncating")
            content = content[:MAX_CONTENT_SIZE]

        new_content = MirroredContent(
            base_url=base_url,
            original_address=mirrored_url,
            translated_address=translated_address,
            status=response.status_code,
            headers=adjusted_headers,
            data=content)
        if not MirroredContent.store(key_name, new_content):
            logging.error('memcache.add failed: key_name = "%s", '
                          'original_url = "%s"', key_name, mirrored_url)

        return new_content


###############################################################################

class WarmupHandler(webapp2.RequestHandler):
    def get(self):
        pass


class BaseHandler(webapp2.RequestHandler):
    def get_relative_url(self):
        slash = self.request.url.find("/", len(self.request.scheme + "://"))
        if slash == -1:
            return "/"
        return self.request.url[slash:]

    def is_recursive_request(self):
        if "AppEngine-Google" in self.request.headers.get("User-Agent", ""):
            logging.warning("Ignoring recursive request by user-agent=%r; ignoring")
            self.error(404)
            return True
        return False


class HomeHandler(BaseHandler):
    def get(self):
        if self.is_recursive_request():
            return

        # Handle the input form to redirect the user to a relative url
        form_url = self.request.get("url")
        if form_url:
            # Accept URLs that still have a leading 'http://'
            inputted_url = urllib.unquote(form_url)
            if inputted_url.startswith(HTTP_PREFIX):
                inputted_url = inputted_url[len(HTTP_PREFIX):]
            return self.redirect("/" + inputted_url)

        # Do this dictionary construction here, to decouple presentation from
        # how we store data.
        secure_url = None
        if self.request.scheme == "http":
            secure_url = "https://%s%s" % (self.request.host, self.request.path_qs)
        context = {
            "secure_url": secure_url,
        }
        self.response.out.write(template.render("main.html", context))


class MirrorHandler(BaseHandler):
    def get(self, fiddle_name, base_url):
        if self.is_recursive_request():
            return

        assert base_url

        #TODO remove redundant root_url
        url_find = base_url.find('/')
        if url_find is -1:
            root_url = base_url
        else:
            root_url = base_url[0:url_find]
        base_url = fiddle_name + '/' + base_url
        root_url = fiddle_name + '/' + root_url

        # Log the user-agent and referrer, to see who is linking to us.
        logging.debug('User-Agent = "%s", Referrer = "%s"',
                      self.request.user_agent,
                      self.request.referer)

        translated_address = self.get_relative_url()[1:]  # remove leading /
        translated_address = translated_address[translated_address.index('/') + 1:]
        mirrored_url = HTTP_PREFIX + translated_address

        # Use sha256 hash instead of mirrored url for the key name, since key
        # names can only be 500 bytes in length; URLs may be up to 2KB.
        key_name = get_url_key_name(mirrored_url)

        content = MirroredContent.retrieve(key_name)
        cache_miss = False
        if content is None:
            logging.debug("Cache miss")
            cache_miss = True
            content = MirroredContent.fetch_and_store(key_name, base_url, root_url,
                                                      translated_address,
                                                      mirrored_url)
        if content is None:
            return self.error(404)

        for key, value in content.headers.iteritems():
            self.response.headers[key] = value
        if not DEBUG:
            self.response.headers["cache-control"] = \
                "max-age=%d" % EXPIRATION_DELTA_SECONDS

        self.response.out.write(content.data)
        # TODO rewrite data here
        if content.headers['content-type'].startswith('text/html'):
            fiddle = Fiddle.byUrlKey(fiddle_name)
            self.response.out.write('<script id="webfiddle-js">' + fiddle.script + '</script>')
            self.response.out.write('<style id="webfiddle-css">' + fiddle.style + '</style>')

###############################################################################

app = webapp2.WSGIApplication([
                                  (r"/", HomeHandler),
                                  (r"/main", HomeHandler),
                                  (r"/([^/]+).*", MirrorHandler),
                                  (r"/warmup", WarmupHandler),
                              ], debug=DEBUG)
