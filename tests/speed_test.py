import os
import unittest
import requests

BASE_URL = os.environ.get('WEBFIDDLE_URL', 'http://localhost:8080')


def server_up():
    try:
        requests.get(BASE_URL, timeout=2)
        return True
    except requests.exceptions.RequestException:
        return False


@unittest.skipUnless(server_up(), f'webfiddle server not running at {BASE_URL}')
class SpeedTest(unittest.TestCase):

    def test_speed(self):
        for x in range(15):
            r = requests.get(f'{BASE_URL}/cats-d8c4vu/localhost:8080')


if __name__ == '__main__':
    unittest.main()
