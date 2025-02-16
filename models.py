import logging
import uuid

from google.cloud import ndb

client = ndb.Client()


def ndb_context():
    return client.context()


import fixtures

default_fiddle = None


class BaseModel(ndb.Model):
    def default(self, o):
        return o.to_dict()

    @classmethod
    def save(cls, obj):
        with client.context():
            return obj.put()

    @classmethod
    def delete(cls, obj):
        with client.context():
            return obj.key.delete()

    @classmethod
    def save_bulk(cls, objs):
        with client.context():
            return ndb.put_multi(objs)


_cache = {}

# we dont use this too much db
class CacheKey(ndb.Model):
    lookup_key = ndb.StringProperty()
    value = ndb.StringProperty()


class Fiddle(ndb.Model):
    name = ndb.StringProperty()
    id = ndb.StringProperty()
    script = ndb.TextProperty()
    style = ndb.TextProperty()
    script_language = ndb.StringProperty(choices=['js', 'coffee'])
    style_language = ndb.StringProperty(choices=['css', 'less', 'sass'])
    created = ndb.DateTimeProperty(auto_now_add=True)
    updated = ndb.DateTimeProperty(auto_now=True)
    title = ndb.StringProperty()
    description = ndb.TextProperty()
    start_url = ndb.StringProperty()
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.name:
            self.name = str(uuid.uuid4())[:8]
        if not self.id:
            self.id = str(uuid.uuid4())
    
    @classmethod
    def byId(cls, id):
        if not id:
            return None
        return cls.query(cls.id == id).get()
    
    @classmethod
    def byName(cls, name):
        if not name:
            return None
        query = cls.query(cls.name == name)
        return query.get()
    
    @classmethod
    def byUrlKey(cls, urlkey):
        """Get a fiddle by its URL key (title-id or just id)"""
        if not urlkey:
            return None
        # Split on last hyphen to separate title and id
        parts = urlkey.rsplit('-', 1)
        if len(parts) == 2:
            # We have both title and id
            return cls.byId(parts[1])
        # Just an id
        return cls.byId(urlkey)
    
    def put(self):
        if not self.name:
            self.name = str(uuid.uuid4())[:8]
        if not self.id:
            self.id = str(uuid.uuid4())
        return super().put()


default_fiddle = Fiddle()
default_fiddle.id = "d8c4vu"
default_fiddle.style = "body {\n" "    background-color: skyblue;\n" "}\n"

default_fiddle.script = (
    "// replace the first image we see with a cat\n"
    "document.images[0].src = 'http://thecatapi.com/api/images/get?format=src&type=gif';\n\n"
    "// replace the google logo with a cat\n"
    "document.getElementById('lga').innerHTML = '<a href=\"http://thecatapi.com\">"
    '<img src="http://thecatapi.com/api/images/get?format=src&type=gif"></a>\';\n'
)

default_fiddle.style_language = "css"
default_fiddle.script_language = "js"
default_fiddle.title = "cats"
default_fiddle.description = "cats via the cat api"
default_fiddle.start_url = "www.google.com"
