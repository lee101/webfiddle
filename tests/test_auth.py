import os
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ["DATASTORE_EMULATOR_HOST"] = "localhost:8081"
os.environ["GOOGLE_CLOUD_PROJECT"] = "test"
from fastapi.testclient import TestClient
from main import app, DATABASE_PATH, init_db


def setup_module(module):
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)
    init_db()


def test_register_and_login():
    with TestClient(app) as client:
        resp = client.post('/register', data={'username': 'alice', 'password': 'secret'}, follow_redirects=False)
        assert resp.status_code == 302
        assert client.cookies.get('session')

        client.get('/logout', follow_redirects=False)

        resp = client.post('/login', data={'username': 'alice', 'password': 'secret'}, follow_redirects=False)
        assert resp.status_code == 302
        assert client.cookies.get('session')

        resp = client.post('/login', data={'username': 'alice', 'password': 'wrong'}, follow_redirects=False)
        assert resp.status_code == 400


