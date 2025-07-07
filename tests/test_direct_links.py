import unittest
import ast
import re

class TestDirectDomains(unittest.TestCase):
    def test_domains_list(self):
        with open('mirror/mirror.py') as f:
            content = f.read()
        match = re.search(r'DIRECT_DOMAINS\s*=\s*(\[.*?\])', content, re.S)
        self.assertIsNotNone(match, 'DIRECT_DOMAINS not found')
        domains = ast.literal_eval(match.group(1))
        expected = [
            'ebank.nz',
            'netwrck.com',
            'text-generator.io',
            'bitbank.nz',
            'readingtime.app.nz',
            'rewordgame.com',
            'bigmultiplayerchess.com',
            'webfiddle.net',
            'how.nz',
            'helix.app.nz'
        ]
        for domain in expected:
            self.assertIn(domain, domains)

if __name__ == '__main__':
    unittest.main()
