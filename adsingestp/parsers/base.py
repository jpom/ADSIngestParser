import html
import json
import re
import warnings
from datetime import datetime

import bs4
from bs4 import MarkupResemblesLocatorWarning

from adsingestp.ingest_exceptions import WrongFormatException
from adsingestp.utils import ConvertEntities


class IngestBase(object):
    TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

    # TODO will need to add boolean keys here if we want to keep falsy values - check these
    required_keys = [
        "createdTime",
        "parsedTime",
        "loadType",
        "loadFormat",
        "loadLocation",
        "recordOrigin",
    ]

    def __init__(self, xml_ref=True):
        warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning, module="bs4")
        self.xml_ref = xml_ref

    def _clean_empty(self, input_to_clean, keys_to_keep=required_keys):
        """

        :param input_to_clean: dictionary that contains empty key/value pairs to remove
        :param keys_to_keep: list of keys to keep, even if they"re empty
        :return: copy of input dict with all keys that contain empty values removed
        """

        if isinstance(input_to_clean, dict):
            return {
                k: v
                for k, v in ((k, self._clean_empty(v)) for k, v in input_to_clean.items())
                if v or (k in keys_to_keep)
            }

        if isinstance(input_to_clean, list):
            return [v for v in map(self._clean_empty, input_to_clean) if v]

        return input_to_clean

    def _clean_output(self, input):
        """
        Remove extra spaces and line breaks
        :param input: text to clean
        :return: cleaned text
        """
        input = input.replace("\n", " ")
        output = re.sub(r"\s+", r" ", input)
        output = output.strip()

        return output

    def _entity_convert(self, input):
        for k, v in input.items():
            if k == "references" and self.xml_ref:
                pass
            else:
                if isinstance(v, str):
                    v = html.unescape(v)
                elif isinstance(v, list):
                    newv = []
                    for i in v:
                        if isinstance(i, str):
                            i = html.unescape(i)
                        newv.append(i)
                    v = newv
                else:
                    pass
            input[k] = v

        return input

    def get_chunks(self, input_xml, start_pattern, end_pattern, head_foot=False):
        """
        Super simple method (though not inefficient) to cut input
        into chunk-sized documents, preserving header/footer if needed

        :param input_xml: Text of XML document to be chunked
        :param start_pattern: string, regex pattern to match at beginning of a chunk
        :param end_pattern: string, regex pattern to match at end of a chunk
        :param head_foot: boolean, option to return the header/footer with each chunk
        :return: iterator of chunks
        """

        start = re.compile(start_pattern, re.IGNORECASE)
        end = re.compile(end_pattern, re.IGNORECASE)

        first = start.search(input_xml)
        if first is None:
            return input_xml  # not found, return the whole thing

        istart = first.start()
        iend = None
        for first in end.finditer(input_xml, istart + 1):
            iend = first.end() + 1
        if iend is None:
            return input_xml  # not found, return the whole thing

        if head_foot:
            header = input_xml[0:istart]
            footer = input_xml[iend:]
        else:
            header = ""
            footer = ""

        for snext in start.finditer(input_xml, istart + 1):
            next_start = snext.start()
            yield header + input_xml[istart:next_start] + footer
            istart = snext.start()

        yield header + input_xml[istart:iend] + footer

    def format(self, input_dict, format):
        """
        Converts parsed metadata dictionary into formal data model. Parsed metadata dictionary should be
        of the following format:

        input_dict: {'abstract': string,
                     'authors': [{'given': string,
                                  'middle': string,
                                  'surname': string,
                                  'prefix': string,
                                  'suffix': string,
                                  'pubraw': string,
                                  'collab': string,
                                  'aff': [string],
                                  'affid': [[affid_string]],
                                  'corresp': bool,
                                  'email': string,
                                  'orcid': string,
                                  'xemail': [string]}
                                 ],
                     'contributors': [{'given': string,
                                       'middle': string,
                                       'surname': string,
                                       'prefix': string,
                                       'suffix': string,
                                       'pubraw': string,
                                       'collab': string,
                                       'aff': [string],
                                       'affid': [[affid_string]],
                                       'corresp': bool,
                                       'email': string,
                                       'orcid': string,
                                       'xemail': [string]}
                                      ],
                     'comments': [{'origin': string,
                                   'text': string}],
                     'conf_name': string,
                     'conf_location': string,
                     'conf_date': string,
                     'copyright': string,
                     'doctype': string,
                     'edhist_acc': string
                     'edhist_rec': [string],
                     'edhist_rev': [string],
                     'esources': [(sourcestring, locationstring)],
                     'electronic_id': string,
                     'ids': {'doi': string,
                             'preprint': {'source': string,
                                          'id': string},
                             'pub-id': [{'Identifier': string,
                                         'attribute': string}]},
                     'isbn': [{'type': string,
                               'isbn_str': string}],
                     'issn': [(pubtype_string, issn_string)],
                     'issue': string,
                     'keywords': [{'string': string,
                                   'system': string}],
                     'lang_native': string,
                     'numpages': string,
                     'openAccess': {'open': bool},
                     'page_first': string,
                     'page_last': string,
                     'page_range': string,
                     'pubdate_electronic': string,
                     'pubdate_print': string,
                     'pubdate_other': [{'type': string,
                                        'date': string}]
                     'publication': string,
                     'publisher': string,
                     'references': [string],
                     'relatedto': [{'relationship': string,
                                    'id': string}],
                     'series_title': string,
                     'series_id': string,
                     'series_id_description': string,
                     'sub_lang_native': string,
                     'subtitle': string,
                     'subtitle_native': string,
                     'subtitle_notes': [string],
                     'title': string,
                     'title_native': string,
                     'title_notes': [string],
                     'volume': string
                    }


        :param input_dict: parsed metadata dictionary to format into formal data model
        :param format: JATS, OtherXML, HTML, Text
        :return: serialized JSON that follows our internal data model
        """

        if format not in [
            "JATS",
            "OtherXML",
            "HTML",
            "Text",
            "Copernicus",
            "Elsevier",
            "IEEE",
            "Wiley",
            "Springer",
            "XOAI",
        ]:
            raise WrongFormatException

        output = {}

        output["recordData"] = {
            "createdTime": "",
            "parsedTime": datetime.utcnow().strftime(self.TIMESTAMP_FMT),
            "loadType": "fromURL" if format == "HTML" else "fromFile",
            "loadFormat": format,
            "loadLocation": "",
            "recordOrigin": "",
        }

        output["relatedTo"] = [
            {"relationship": i.get("relationship", ""), "relatedDocID": i.get("id", "")}
            for i in input_dict.get("relatedto", [])
        ]

        output["editorialHistory"] = {
            "receivedDates": input_dict.get("edhist_rec", ""),
            "revisedDates": input_dict.get("edhist_rev", ""),
            "acceptedDate": input_dict.get("edhist_acc", ""),
        }

        output["pubDate"] = {
            "electrDate": input_dict.get("pubdate_electronic", ""),
            "printDate": input_dict.get("pubdate_print", ""),
            "otherDate": [
                {"otherDateType": i.get("type", ""), "otherDateValue": i.get("date", "")}
                for i in input_dict.get("pubdate_other", [])
            ],
        }

        # new decision tree for pubyear
        versionOfRecordDate = ""
        if input_dict.get("pubdate_other", []):
            for o in input_dict["pubdate_other"]:
                if o.get("type", "") == "version-of-record":
                    versionOfRecordDate = o.get("date")[0:4]
        output["publication"] = {
            # "docType": "XXX",
            "pubName": input_dict.get("publication", ""),
            "confName": input_dict.get("conf_name", ""),
            "confLocation": input_dict.get("conf_location", ""),
            "confDates": input_dict.get("conf_date", ""),
            # "confEditors": ["XXX"],
            # "confPID": "XXX",
            "publisher": input_dict.get("publisher", ""),
            "issueNum": input_dict.get("issue", ""),
            "volumeNum": input_dict.get("volume", ""),
            "pubYear": versionOfRecordDate
            if versionOfRecordDate
            else (
                input_dict["pubdate_print"][0:4]
                if "pubdate_print" in input_dict
                else (
                    input_dict["pubdate_electronic"][0:4]
                    if "pubdate_electronic" in input_dict
                    else (
                        input_dict.get("pubdate_other", {})[0].get("date", "")[0:4]
                        if "pubdate_other" in input_dict
                        else ""
                    )
                )
            ),
            "bookSeries": {
                "seriesName": input_dict.get("series_title", ""),
                "seriesID": input_dict.get("series_id", ""),
                "seriesDescription": input_dict.get("series_id_description", ""),
            },
            "ISSN": [
                {"pubtype": pubtype, "issnString": issn}
                for (pubtype, issn) in input_dict.get("issn", "")
            ],
            # "isRefereed": True or False
        }

        output["persistentIDs"] = [
            {
                # 'Crossref': 'XXX',
                "ISBN": [
                    {"pubtype": i.get("type", ""), "isbnString": i.get("isbn_str", "")}
                    for i in input_dict.get("isbn", [])
                ],
                "DOI": input_dict.get("ids", {}).get("doi", ""),
                "preprint": {
                    "source": input_dict.get("ids", {}).get("preprint", {}).get("source", ""),
                    "identifier": input_dict.get("ids", {}).get("preprint", {}).get("id", ""),
                },
            }
        ]

        output["publisherIDs"] = [
            {"attribute": i.get("attribute", ""), "Identifier": i.get("Identifier", "")}
            for i in input_dict.get("ids", {}).get("pub-id", "")
        ]

        output["pagination"] = {
            "firstPage": input_dict.get("page_first", ""),
            "lastPage": input_dict.get("page_last", ""),
            "pageCount": input_dict.get("numpages", ""),
            "pageRange": input_dict.get("page_range", ""),
            "electronicID": input_dict.get("electronic_id", ""),
        }

        output["authors"] = [
            {
                "name": {
                    "surname": i.get("surname", ""),
                    "given_name": i.get("given", ""),
                    "middle_name": i.get("middle", ""),
                    "prefix": i.get("prefix", ""),
                    "suffix": i.get("suffix", ""),
                    "pubraw": i.get("nameraw", ""),
                    "native_lang": i.get("native_lang", ""),
                    "collab": i.get("collab", ""),
                },
                "affiliation": [
                    {"affPubRaw": j, "affPubID": i.get("affid")[idx] if i.get("affid") else []}
                    for idx, j in enumerate(i.get("aff", []))
                ],
                "attrib": {
                    "collab": True if i.get("collab", "") else False,
                    "corresp": True if i.get("corresp", "") else False,
                    # "deceased": True or False, # TODO need an example
                    # "coauthor": True or False, # TODO need an example
                    "email": i.get("email", ""),
                    # "funding": "XXX", # TODO need an example
                    "orcid": i.get("orcid", ""),
                },
            }
            for i in input_dict.get("authors", [])
        ]

        output["otherContributor"] = [
            {
                "role": i.get("role", ""),
                "contrib": {
                    "name": {
                        "surname": i.get("surname", ""),
                        "given_name": i.get("given", ""),
                        "middle_name": i.get("middle", ""),
                        "prefix": i.get("prefix", ""),
                        "suffix": i.get("suffix", ""),
                        "pubraw": i.get("nameraw", ""),
                        # "native_lang": "XXX",
                        "collab": i.get("collab", ""),
                    },
                    "affiliation": [
                        {"affPubRaw": j, "affPubID": i.get("affid")[idx] if i.get("affid") else []}
                        for idx, j in enumerate(i.get("aff", []))
                    ],
                    "attrib": {
                        "collab": True if i.get("collab", "") else False,
                        # "deceased": True or False,
                        # "coauthor": True or False,
                        "email": i.get("email", ""),
                        # "funding": "XXX",
                        "orcid": i.get("orcid", ""),
                    },
                },
            }
            for i in input_dict.get("contributors", [])
        ]

        output["title"] = {
            "textEnglish": input_dict.get("title", ""),
            "textNative": input_dict.get("title_native", ""),
            "langNative": input_dict.get("lang_native", ""),
            "textNotes": input_dict.get("title_notes", []),
        }

        output["subtitle"] = {
            "textEnglish": input_dict.get("subtitle", ""),
            "textNative": input_dict.get("subtitle_native", ""),
            "langNative": input_dict.get("sub_lang_native", ""),
            "textNotes": input_dict.get("subtitle_notes", []),
        }

        output["abstract"] = {
            "textEnglish": input_dict.get(
                "abstract", ""
            ),  # TODO need to tweak for case of foreign language abstract
            # "textNative": "XXX", # TODO
            # "langNative": "XXX" # TODO
        }

        output["comments"] = [
            {"commentOrigin": i.get("origin", ""), "commentText": i.get("text", "")}
            for i in input_dict.get("comments", [])
        ]

        # output["fulltext"] = {
        #     "language": "XXX",
        #     "body": "XXX"
        # } # TODO this is from fulltext

        # output["acknowledgements"] = "XXX" # TODO this is from fulltext

        if input_dict.get("references", None):
            if type(input_dict.get("references")) == list:
                input_refs = input_dict.get("references")
            elif type(input_dict.get("references")) == str:
                input_refs = list(input_dict.get("references"))
            else:
                # TODO add error handling here
                input_refs = ""
            output["references"] = input_refs

        # output["backmatter"] = [
        #     {
        #         "backType": "XXX",
        #         "language": "XXX",
        #         "body": "XXX"
        #     }
        # ] # TODO need an example

        # output["astronomicalObjects"] = [
        #     "XXX"
        # ] # TODO need an example

        output["esources"] = [
            {"source": source, "location": location}
            for (source, location) in input_dict.get("esources", "")
        ]

        # output["dataLinks"] = [
        #     {
        #         "title": "XXX",
        #         "identifier": "XXX",
        #         "location": "XXX",
        #         "dataType": "XXX",
        #         "comment": "XXX"
        #     }
        # ] # TODO need an example

        output["doctype"] = input_dict.get("doctype", "")

        output["keywords"] = [
            {
                "keyString": i.get("string", ""),
                "keySystem": i.get("system", ""),
                "keyID": i.get("id", "") if i.get("id", "") and i.get("system", "") else "",
            }
            for i in input_dict.get("keywords", [])
        ]

        output["copyright"] = {
            "status": True if "copyright" in input_dict else False,  # TODO ask MT about this
            "statement": input_dict.get("copyright", ""),
        }

        output["openAccess"] = {
            "open": input_dict.get("openAccess", {}).get("open", False),
            "license": input_dict.get("openAccess", {}).get("license", ""),
            "licenseURL": input_dict.get("openAccess", {}).get("licenseURL", "")
            # "preprint": "XXX",
            # "startDate": "XXX",
            # "endDate": "XXX",
            # "embargoLength": "XXX"
        }  # TODO need an example

        # output["pubnote"] = "XXX" # TODO need an example
        #
        output["funding"] = input_dict.get("funding", [])
        #
        # output["version"] = "XXX" # TODO need an example

        # do a very quick custom entity conversion here:
        if type(output) == dict:
            json_output = ConvertEntities()._convert_entities_to_ascii(json.dumps(output))
            output = json.loads(json_output)

        output_clean = self._clean_empty(output)

        return output_clean


class BaseBeautifulSoupParser(IngestBase):
    """
    An XML parser which uses BeautifulSoup to create a dictionary
    out of the input XML stream.
    """

    fix_ampersand_1 = re.compile(r"(__amp__)(.*?)(;)")
    fix_ampersand_2 = re.compile(r"(&amp;)(.*?)(;)")
    re_ampersands = [fix_ampersand_1, fix_ampersand_2]

    HTML_TAGS_MATH = [
        "inline-formula",
        "tex-math",
        "mml:math",
        "mml:semantics",
        "mml:mrow",
        "mml:munder",
        "mml:mo",
        "mml:mi",
        "mml:msub",
        "mml:mover",
        "mml:mn",
        "mml:annotation",
        "mml:msubsup",
        "mml:msupsub",
        "mml:msup",
        "mml:mfrac",
        "mml:munderovers",
        "mml:msqrt",
        "mml:mmultiscripts",
        "mml:prescripts",
        "mml:mtext",
        "mml:mfenced",
        "mml:mstyle",
        "mml:mspace",
    ]

    HTML_TAGS_HTML = ["sub", "sup", "a", "astrobj", "i", "b"]

    HTML_TAGSET = {
        "title": HTML_TAGS_MATH + HTML_TAGS_HTML + ["a"],
        "abstract": HTML_TAGS_MATH + HTML_TAGS_HTML + ["a", "pre", "br"],
        "comments": HTML_TAGS_MATH + HTML_TAGS_HTML + ["a", "pre", "br", "p"],
        "affiliations": ["email", "orcid"],
        "keywords": HTML_TAGS_HTML,
        "license": HTML_TAGS_MATH + HTML_TAGS_HTML + ["a", "pre", "br"],
    }

    HTML_TAGS_DANGER = ["php", "script", "css"]

    def __init__(self):
        super(IngestBase, self).__init__()

    def bsstrtodict(self, input_xml, parser="lxml-xml"):
        """
        Returns a BeautifulSoup tree given an XML text
        :param input_xml: XML text blob
        :param parser: e.g. 'html.parser', 'html5lib', 'lxml-xml' (default)
        :return: BeautifulSoup object/tree
        """

        return bs4.BeautifulSoup(input_xml, parser)

    def _remove_latex(self, r):
        """
        Removes LaTeX markup inside <tex-math> tags from input BeautifulSoup object
        :param r: BeautifulSoup object
        :return: newr: string with LaTeX removed
        """
        math_elements = r.find_all("tex-math")
        for e in math_elements:
            text = e.get_text()
            begin = text.find("\\begin{document}")
            end = text.find("\\end{document}")
            begin_len = len("\\begin{document}")
            if begin == -1 or end == -1:
                continue
            newtext = text[begin + begin_len : end]
            e.string = newtext
        return r

    def _detag(self, r, tags_keep):
        """
        Removes tags from input BeautifulSoup object
        :param r: BeautifulSoup object (not string)
        :param tags_keep: this function will remove all tags except those passed here
        :return: newr: striing with cleaned text
        """
        # note that parser=lxml is recommended here - if the more stringent lxml-xml is used,
        # the output is slightly different and the code will need to be modified
        newr = self.bsstrtodict(str(r), "lxml")

        # list of all tags in the object
        if newr.find_all():
            tag_list = list(set([x.name for x in newr.find_all()]))
        else:
            tag_list = []

        # list of all processing instructions in the object like <?index>
        if newr.find_all(string=True):
            instruction_list = newr.find_all(string=True)
        else:
            instruction_list = []
        for ins in instruction_list:
            if ins.startswith("index"):
                # Remove preceding white-space
                if ins.previous_sibling and isinstance(ins.previous_sibling, str):
                    ins.previous_sibling.replace_with(ins.previous_sibling.rstrip())
                ins.extract()

        # Modify the semantics tag before unwrapping newr
        if "annotation" in tag_list and "semantics" in tag_list:
            semantics_elements = newr.find_all("semantics", None)
            for se in semantics_elements:
                annotation_elements = se.find_all("annotation", None)
                if annotation_elements:
                    se.clear()
                    for ae in annotation_elements:
                        # Replace the contents of <semantics> with the contents of the child <annotation> tag
                        se.append(ae.text.strip())

            # reset tag list
            tag_list = list(set([x.name for x in newr.find_all()]))

        for t in tag_list:
            elements = newr.find_all(t)
            for e in elements:
                if e.attrs:
                    e.attrs.clear()
                if t in self.HTML_TAGS_DANGER:
                    e.decompose()
                elif (t == "alternatives") or (t == "inline-formula"):
                    alt_math_element = e.find_all("mml:math", [])
                    alt_tex_element = e.find_all("tex-math", [])
                    if alt_math_element and alt_tex_element:
                        for ee in alt_tex_element:
                            ee.decompose()
                    if t not in tags_keep:
                        e.unwrap()
                elif t in tags_keep:
                    continue
                else:
                    if t.lower() == "sc":
                        if e.string:
                            e.string = e.string.upper()
                    e.unwrap()

        # Note: newr is converted from a bs4 object to a string here.
        # Everything after this point is string manipulation.
        newr = str(newr)

        for reamp in self.re_ampersands:
            amp_fix = reamp.findall(newr)
            for s in amp_fix:
                s_old = "".join(s)
                s_new = "&" + s[1] + ";"
                newr = newr.replace(s_old, s_new)

        newr = re.sub("\\s+|\n+|\r+", " ", newr)
        newr = newr.replace("&nbsp;", " ")
        newr = newr.strip()

        return newr
