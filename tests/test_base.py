import os
import unittest

import pytest

from adsingestp.parsers import base


@pytest.mark.filterwarnings("ignore::bs4.MarkupResemblesLocatorWarning")
class TestBase(unittest.TestCase):
    def setUp(self):
        stubdata_dir = os.path.join(os.path.dirname(__file__), "stubdata/")
        self.inputdir = os.path.join(stubdata_dir, "input")
        self.outputdir = os.path.join(stubdata_dir, "output")
        self.maxDiff = None

    def test_basebs4(self):
        data = '<bib xml:id="b54"><citation type="journal" xml:id="cit54"><author><familyName>Kormendy</familyName> <givenNames>J.</givenNames></author>, <author><familyName>Richstone</familyName> <givenNames>D.</givenNames></author>, <pubYear year="1995">1995</pubYear>, <journalTitle>ARA__amp__amp;A</journalTitle>, <vol>33</vol>, <pageFirst>581</pageFirst></citation></bib>'

        parser = base.BaseBeautifulSoupParser()
        record = parser._detag(data, parser.HTML_TAGS_HTML)
        record_corrected = "Kormendy J., Richstone D., 1995, ARA&amp;A, 33, 581"
        self.assertEqual(record, record_corrected)
