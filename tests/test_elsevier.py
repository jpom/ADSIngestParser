import datetime
import json
import os
import unittest

from adsingestschema import ads_schema_validator

from adsingestp.parsers import elsevier

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class TestElsevier(unittest.TestCase):
    def setUp(self):
        stubdata_dir = os.path.join(os.path.dirname(__file__), "stubdata/")
        self.inputdir = os.path.join(stubdata_dir, "input")
        self.outputdir = os.path.join(stubdata_dir, "output")
        self.maxDiff = None

    def test_elsevier(self):
        filenames = [
            "els_apss_586_152807",
            "els_icar_382_115019",
            "els_missing_keywords_tag",
            "els_mononym",
            "els_simple_article_1",
            "els_simple_article_2",
            "els_simple_article_3",
            "els_simple_article_4",
            "els_simple_article_5",
            "els_book_chapter",
            "els_book_review",
            "els_converted_article",
            "els_detag_example_1",
            "els_detag_example_2",
            "els_list",
            "els_phlb_compound_affil",
            "els_odd_cover_date",
            "els_roman_num_1",
            "els_roman_num_2",
            "els_abstract_author_1",
            "els_other_ref",
            "els_tex_title_1",
            "elsevier_untagged_orcid",
        ]
        for f in filenames:
            test_infile = os.path.join(self.inputdir, f + ".xml")
            test_outfile = os.path.join(self.outputdir, f + ".json")
            parser = elsevier.ElsevierParser()

            with open(test_infile, "rb") as fp:
                input_data = fp.read()

            parsed = parser.parse(input_data)

            with open(test_outfile, "rb") as fp:
                output_text = fp.read()
                output_data = json.loads(output_text)

            # make sure this is valid schema
            try:
                ads_schema_validator().validate(parsed)
            except Exception:
                self.fail("Schema validation failed")
                pass

            # this field won't match the test data, so check and then discard
            time_difference = (
                datetime.datetime.strptime(parsed["recordData"]["parsedTime"], TIMESTAMP_FMT)
                - datetime.datetime.utcnow()
            )
            self.assertTrue(abs(time_difference) < datetime.timedelta(seconds=10))
            parsed["recordData"]["parsedTime"] = ""

            self.assertEqual(parsed, output_data)
