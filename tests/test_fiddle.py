import os
from pathlib import Path
from fastapi.testclient import TestClient
from main import app
from models import DATABASE_PATH, init_db, Fiddle
import fixtures


def setup_module(module):
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
    init_db()


def test_create_and_fetch_fiddle():
    fiddle = Fiddle(
        id='abc123',
        title='My Fiddle',
        description='desc',
        start_url='example.com',
        script='alert(1)',
        style='body{}',
        script_language=fixtures.SCRIPT_TYPES['js'],
        style_language=fixtures.STYLE_TYPES['css']
    )
    Fiddle.save(fiddle)

    fetched = Fiddle.byId('abc123')
    assert fetched is not None
    assert fetched.title == 'My Fiddle'

    fetched2 = Fiddle.byUrlKey('my-fiddle-abc123')
    assert fetched2 is not None
    assert fetched2.script == 'alert(1)'
