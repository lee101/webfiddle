import re


import logging
import unittest

from mirror.transform_content import TransformContent


class TransformTest(unittest.TestCase):
    def _RunTransformTest(self, base_url, accessed_url, original, expected):
        tag_tests = [
            '<img src="%s"/>',
            "<img src='%s'/>",
            "<img src=%s/>",
            "<img src=\"%s'/>",
            "<img src='%s\"/>",
            "<img src  \t=  '%s'/>",
            "<img src  \t=  \t '%s'/>",
            "<img src = '%s'/>",
            '<a href="%s">',
            "<a href='%s'>",
            "<a href=%s>",
            "<a href=\"%s'>",
            "<a href='%s\">",
            "<a href \t = \t'%s'>",
            "<a href \t  = '%s'>",
            "<a href =  \t'%s'>",
            "<td background=%s>",
            "<td background='%s'>",
            '<td background="%s">',
            '<form action="%s">',
            "<form action='%s'>",
            "<form action=%s>",
            "<form action=\"%s'>",
            "<form action='%s\">",
            "<form action \t = \t'%s'>",
            "<form action \t  = '%s'>",
            "<form action =  \t'%s'>",
            "@import '%s';",
            "@import '%s'\nnext line here",
            "@import \t '%s';",
            "@import %s;",
            "@import %s",
            '@import "%s";',
            '@import "%s"\nnext line here',
            "@import url(%s)",
            "@import url('%s')",
            '@import url("%s")',
            "background: transparent url(%s) repeat-x left;",
            'background: transparent url("%s") repeat-x left;',
            "background: transparent url('%s') repeat-x left;",
            '<meta http-equiv="Refresh" content="0; URL=%s">',
        ]
        for tag in tag_tests:
            test = tag % original
            correct = tag % expected
            result = TransformContent(base_url, accessed_url, test)
            logging.info("Test with\n"
                         "Accessed: %s\n"
                         "Input   : %s\n"
                         "Received: %s\n"
                         "Expected: %s",
                         accessed_url, test, result, correct)
            if result != correct:
                logging.info("FAIL")
            self.assertEqual(correct, result)

    def testPreventDoublePrefix(self):
        """Test that URLs already containing the fiddle prefix are not re-prefixed"""
        self._RunTransformTest(
            "cats-bdml3m/slashdot.org",
            "http://slashdot.org",
            "/cats-bdml3m/slashdot.org/style.css",
            "/cats-bdml3m/slashdot.org/style.css")

    def testNestedPaths(self):
        """Test handling of nested paths in URLs"""
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com/path/to/page.html",
            "/path/to/resource.jpg",
            "/cats-bdml3m/example.com/path/to/resource.jpg")

    def testQueryParameters(self):
        """Test URLs with query parameters"""
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com",
            "/search?q=test&page=1",
            "/cats-bdml3m/example.com/search?q=test&page=1")

    def testFragmentIdentifiers(self):
        """Test URLs with fragment identifiers"""
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com",
            "/page#section1",
            "/cats-bdml3m/example.com/page#section1")

    def testMultipleSlashes(self):
        """Test handling of URLs with multiple consecutive slashes"""
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com",
            "//path//to//resource",
            "/cats-bdml3m/example.com/path/to/resource")

    def testEmptyPath(self):
        """Test handling of empty paths"""
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com",
            "/",
            "/cats-bdml3m/example.com/")

    def testDataUrl(self):
        """Test that data URLs are not transformed"""
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"
        self._RunTransformTest(
            "cats-bdml3m/example.com",
            "http://example.com",
            data_url,
            data_url)

    def testBaseTransform(self):
        self._RunTransformTest(
            "cats-bdml3m/slashdot.org",
            "http://slashdot.org",
            "//images.slashdot.org/iestyles.css?T_2_5_0_204",
            "/cats-bdml3m/images.slashdot.org/iestyles.css?T_2_5_0_204")

    def testAbsolute(self):
        self._RunTransformTest(
            "slashdot.org",
            "http://slashdot.org",
            "http://slashdot.org/slashdot_files/all-minified.js",
            "/slashdot.org/slashdot_files/all-minified.js")

    def testRelative(self):
        self._RunTransformTest(
            "cats-bdml3m/slashdot.org",
            "http://slashdot.org",
            "images/foo.html",
            "/cats-bdml3m/slashdot.org/images/foo.html")

    def testUpDirectory(self):
        self._RunTransformTest(
            "cats-bdml3m/a248.e.akamai.net",
            "http://a248.e.akamai.net/foobar/is/the/path.html",
            "../layout/mh_phone-home.png",
            "/cats-bdml3m/a248.e.akamai.net/foobar/is/layout/mh_phone-home.png")

    def testSameDirectoryRelative(self):
        self._RunTransformTest(
            "a248.e.akamai.net",
            "http://a248.e.akamai.net/foobar/is/the/path.html",
            "./layout/mh_phone-home.png",
            "/a248.e.akamai.net/foobar/is/the/./layout/mh_phone-home.png")

    def testSameDirectory(self):
        self._RunTransformTest(
            "a248.e.akamai.net",
            "http://a248.e.akamai.net/foobar/is/the/path.html",
            "mh_phone-home.png",
            "/a248.e.akamai.net/foobar/is/the/mh_phone-home.png")

    def testSameDirectoryNoParent(self):
        self._RunTransformTest(
            "a248.e.akamai.net",
            "http://a248.e.akamai.net/path.html",
            "mh_phone-home.png",
            "/a248.e.akamai.net/mh_phone-home.png")

    def testSameDirectoryWithParent(self):
        self._RunTransformTest(
            "a248.e.akamai.net",
            ("http://a248.e.akamai.net/7/248/2041/1447/store.apple.com"
             "/rs1/css/aos-screen.css"),
            "aos-layout.css",
            ("/a248.e.akamai.net/7/248/2041/1447/store.apple.com"
             "/rs1/css/aos-layout.css"))

    def testRootDirectory(self):
        self._RunTransformTest(
            "a248.e.akamai.net",
            "http://a248.e.akamai.net/foobar/is/the/path.html",
            "/",
            "/a248.e.akamai.net/")

    def testSecureContent(self):
        self._RunTransformTest(
            "slashdot.org",
            "https://slashdot.org",
            "https://images.slashdot.org/iestyles.css?T_2_5_0_204",
            "/images.slashdot.org/iestyles.css?T_2_5_0_204")

    def testPartiallySecureContent(self):
        self._RunTransformTest(
            "slashdot.org",
            "http://slashdot.org",
            "https://images.slashdot.org/iestyles.css?T_2_5_0_204",
            "/images.slashdot.org/iestyles.css?T_2_5_0_204")


