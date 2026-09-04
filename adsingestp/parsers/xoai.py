# Parser for XOAI
# For theses & dissertations, mostly from DSpace repositories (e.g. Texas ScholarWorks)
#
# XOAI records nest every metadata field the same way, using generic
# <element name="..."> and <field name="..."> tags rather than
# format-specific tags (e.g. Dublin Core's <dc:creator>). Because of that,
# most of the work here is just carefully scoping BeautifulSoup searches to
# the right parent, since a naive `find_all` for a given name attribute can
# easily grab the wrong node's descendants.

import logging
import re

from adsingestp import utils
from adsingestp.ingest_exceptions import (
    MissingAuthorsException,
    MissingTitleException,
    NoSchemaException,
    XmlLoadException,
)
from adsingestp.parsers.base import BaseBeautifulSoupParser

logger = logging.getLogger(__name__)

orcid_format = re.compile(r"(\d{4}-){3}\d{3}(\d|X)")


class XOAIParser(BaseBeautifulSoupParser):
    """
    Parser for XOAI-format XML (as exposed via OAI-PMH by DSpace repositories),
    used mainly to ingest theses and dissertations.
    """

    author_collaborations_params = {
        "keywords": ["group", "team", "collaboration"],
        "remove_the": False,
    }

    # Qualifiers seen under <element name="identifier"> other than the bare
    # (language-only) value
    IDENTIFIER_QUALIFIERS = {"doi", "uri", "oclc", "proqst", "issn", "isbn"}

    # Common language/qualifier codes used as the "bare" wrapper for
    # <element name="subject"> and <element name="identifier"> when no
    # classification scheme is specified
    LANGUAGE_CODES = {"en", "en-us", "eng", "none", "es", "spa"}

    def __init__(self):
        super(BaseBeautifulSoupParser, self).__init__()
        self.base_metadata = {}
        self.header = None
        self.root = None  # the inner <metadata xmlns="http://www.lyncode.com/xoai" ...>
        self.dc = None  # <element name="dc"> - holds most bibliographic fields
        self.thesis = None  # <element name="thesis"> - degree info
        self.others = None  # <element name="others"> - handle, access status, etc.
        self.bundles = None  # <element name="bundles"> - bitstream/file info

    # ------------------------------------------------------------------
    # Generic helpers for navigating the XOAI element/field structure
    # ------------------------------------------------------------------
    def _get_element(self, parent, name):
        """Return the direct child <element name="name"> of parent, or None."""
        if parent is None:
            return None
        return parent.find("element", attrs={"name": name}, recursive=False)

    def _get_field_values(self, parent, name, field_name="value"):
        """
        Return the text of every <field name="field_name"> found anywhere
        below the <element name="name"> child of parent. This transparently
        handles the language/qualifier wrapper elements XOAI inserts
        (e.g. <element name="X"><element name="en"><field name="value">...).
        """
        el = self._get_element(parent, name)
        if el is None:
            return []
        return [
            text
            for text in (f.get_text().strip() for f in el.find_all("field", attrs={"name": field_name}))
            if text
        ]

    def _get_field_value(self, parent, name, field_name="value"):
        values = self._get_field_values(parent, name, field_name=field_name)
        return values[0] if values else None

    # ------------------------------------------------------------------
    # Individual field parsers
    # ------------------------------------------------------------------
    def _parse_title(self):
        title = self._get_field_value(self.dc, "title")
        if not title:
            raise MissingTitleException("No title found")
        self.base_metadata["title"] = self._clean_output(title)

    def _parse_abstract(self):
        abstracts = []

        description_el = self._get_element(self.dc, "description")
        abstract_el = self._get_element(description_el, "abstract")

        if abstract_el is not None:
            # language / qualifier elements (e.g., <element name="en">, <element name="none">)
            for lang_el in abstract_el.find_all("element", recursive=False):
                # one or more <field name="value"> per language
                for value in lang_el.find_all("field", attrs={"name": "value"}):
                    raw = value.get_text()  # no strip, to preserve paragraph breaks
                    if raw is None:
                        continue

                    # Normalize CRLF to LF but preserve blank lines between paragraphs
                    txt = raw.replace("\r\n", "\n").replace("\r", "\n").strip()

                    if txt:
                        abstracts.append(txt)

        if not abstracts:
            return

        # First abstract is the main one
        self.base_metadata["abstract"] = abstracts[0]

        # TO DO: EDIT
        # Any additional abstracts (e.g. other languages) are kept as comments,
        # merged in with anything already collected there
        if len(abstracts) > 1:
            comments = self.base_metadata.setdefault("comments", [])
            comments.extend({"text": extra} for extra in abstracts[1:])

    def _parse_author(self):
        authors_out = []
        name_parser = utils.AuthorNames()

        if self.dc is not None:
            creator_elements = self.dc.find_all("element", attrs={"name": "creator"}, recursive=False)
        else:
            creator_elements = []

        for creator_el in creator_elements:
            # each creator can (rarely) have more than one language/qualifier wrapper
            for lang_el in creator_el.find_all("element", recursive=False):
                name_field = lang_el.find("field", attrs={"name": "value"}, recursive=False)
                if not name_field:
                    continue
                name_text = name_field.get_text().strip()
                if not name_text:
                    continue

                parsed_name_list = name_parser.parse(
                    name_text, collaborations_params=self.author_collaborations_params
                )

                # DSpace stores an internal authority UUID here, not an ORCID -
                # but on some instances a real ORCID does end up in this field,
                # so only use it if it actually looks like one
                orcid = None
                authority_field = lang_el.find("field", attrs={"name": "authority"}, recursive=False)
                if authority_field:
                    candidate = authority_field.get_text().strip()
                    if orcid_format.match(candidate):
                        orcid = candidate

                for author_tmp in parsed_name_list:
                    if orcid:
                        author_tmp["orcid"] = orcid
                    authors_out.append(author_tmp)

        if not authors_out:
            raise MissingAuthorsException("No creators/authors found")

        self.base_metadata["authors"] = authors_out

    def _parse_contributors(self):
        """Advisors and committee members, stored as 'contributors' with a role."""
        contributor_parent = self._get_element(self.dc, "contributor")
        if contributor_parent is None:
            return

        role_map = {
            "advisor": "advisor",
            "committeeMember": "committee member",
        }

        name_parser = utils.AuthorNames()
        contributors_out = []

        for role_key, role_label in role_map.items():
            role_el = self._get_element(contributor_parent, role_key)
            if role_el is None:
                continue

            # unlike creator, multiple names for the same role are often lumped
            # together as sibling <field name="value"> under one language element
            for name_text in self._get_field_values(contributor_parent, role_key):
                parsed_name_list = name_parser.parse(
                    name_text, collaborations_params=self.author_collaborations_params
                )
                for c in parsed_name_list:
                    c["role"] = role_label
                    contributors_out.append(c)

        if contributors_out:
            self.base_metadata["contributors"] = contributors_out

    def _parse_date(self):
        date_el = self._get_element(self.dc, "date")
        issued = self._get_field_value(date_el, "issued") if date_el is not None else None
        if issued:
            self.base_metadata["pubdate_electronic"] = issued

    def _parse_ids(self):
        ids_out = []

        # OAI identifier from the record header
        if self.header is not None:
            for hid in self.header.find_all("identifier"):
                text = hid.get_text().strip()
                if text:
                    ids_out.append({"attribute": "oai", "Identifier": text})

        identifier_parent = self._get_element(self.dc, "identifier")
        if identifier_parent is not None:
            for child in identifier_parent.find_all("element", recursive=False):
                qualifier = child.get("name")
                values = [
                    text
                    for text in (
                        f.get_text().strip() for f in child.find_all("field", attrs={"name": "value"})
                    )
                    if text
                ]
                if not values:
                    continue

                if qualifier in self.IDENTIFIER_QUALIFIERS:
                    attribute = qualifier
                else:
                    # unqualified identifier (child name here is just a language
                    # code); this is usually a library catalog/bib number
                    attribute = "catalog"

                for v in values:
                    ids_out.append({"attribute": attribute, "Identifier": v})
                    if attribute == "uri" and "hdl.handle.net" in v:
                        handle = v.rsplit("hdl.handle.net/", 1)[-1]
                        ids_out.append({"attribute": "handle", "Identifier": handle})

        if ids_out:
            self.base_metadata["ids"] = {"pub-id": ids_out}

    def _parse_keywords(self):
        subject_parent = self._get_element(self.dc, "subject")
        if subject_parent is None:
            return

        keywords_out = []

        for child in subject_parent.find_all("element", recursive=False):
            qualifier = child.get("name")
            values = [
                text
                for text in (
                    f.get_text().strip() for f in child.find_all("field", attrs={"name": "value"})
                )
                if text
            ]
            if not values:
                continue

            # if the wrapper name is a language code, there's no classification
            # scheme; otherwise treat the name as the scheme (e.g. "lcsh")
            system = "keyword" if qualifier in self.LANGUAGE_CODES else qualifier

            for v in values:
                keywords_out.append({"string": v, "system": system})

        if keywords_out:
            self.base_metadata["keywords"] = keywords_out

    def _parse_rights(self):
        rights = self._get_field_value(self.dc, "rights")
        if rights:
            self.base_metadata["copyright"] = self._clean_output(rights)

    def _parse_permissions(self):
        if self.others is None:
            return

        access_el = self._get_element(self.others, "access-status")
        if access_el is None:
            return

        value_field = access_el.find("field", attrs={"name": "value"})
        if value_field:
            status = value_field.get_text().strip().lower()
            self.base_metadata["openAccess"] = {"open": status == "open.access"}

    def _parse_thesis_info(self):
        # doctype is always "thesis" for this parser; keep as its own line
        # in case future callers want to override it
        self.base_metadata["doctype"] = "thesis"

        if self.thesis is None:
            return

        degree_el = self._get_element(self.thesis, "degree")
        if degree_el is None:
            return

        grantor = self._get_field_value(degree_el, "grantor")
        if grantor:
            self.base_metadata["publisher"] = grantor

        degree_name = self._get_field_value(degree_el, "name")
        level = self._get_field_value(degree_el, "level")
        department = self._get_field_value(degree_el, "department")
        discipline = self._get_field_value(degree_el, "discipline")

        note_parts = []
        if degree_name:
            note_parts.append(degree_name)
        elif level:
            note_parts.append(level)

        if department:
            note_parts.append("Department of {}".format(department))
        elif discipline:
            note_parts.append(discipline)

        if note_parts:
            note = ", ".join(note_parts)
            note = "Thesis ({}), {}.".format(note, grantor) if grantor else "Thesis ({}).".format(note)
            comments = self.base_metadata.setdefault("comments", [])
            comments.append({"text": note})

    # DELETE
    def _parse_esources(self):
        if self.bundles is None:
            return

        original_bundle = None
        for bundle_el in self.bundles.find_all("element", attrs={"name": "bundle"}, recursive=False):
            name_field = bundle_el.find("field", attrs={"name": "name"}, recursive=False)
            if name_field and name_field.get_text().strip() == "ORIGINAL":
                original_bundle = bundle_el
                break

        if original_bundle is None:
            return

        bitstreams_el = self._get_element(original_bundle, "bitstreams")
        if bitstreams_el is None:
            return

        pdf_url = None
        for bitstream_el in bitstreams_el.find_all("element", attrs={"name": "bitstream"}, recursive=False):
            fmt_field = bitstream_el.find("field", attrs={"name": "format"}, recursive=False)
            url_field = bitstream_el.find("field", attrs={"name": "url"}, recursive=False)
            primary_field = bitstream_el.find("field", attrs={"name": "primary"}, recursive=False)

            if not url_field:
                continue

            is_pdf = fmt_field is not None and "pdf" in fmt_field.get_text().lower()
            is_primary = primary_field is not None and primary_field.get_text().strip().lower() == "true"

            if is_pdf and (is_primary or pdf_url is None):
                pdf_url = url_field.get_text().strip()
                if is_primary:
                    break

        if pdf_url:
            self.base_metadata["esources"] = [("pub_pdf", pdf_url)]


    def parse(self, text):
        """
        Parse XOAI XML into standard JSON format
        :param text: string, contents of XML file
        :return: parsed file contents in JSON format
        """
        try:
            d = self.bsstrtodict(text, parser="lxml-xml")
        except Exception as err:
            raise XmlLoadException(err)

        record = d.find("record")
        if record is None:
            raise NoSchemaException("No <record> element found in XOAI XML.")

        self.header = record.find("header")

        outer_metadata = record.find("metadata")
        if outer_metadata is None:
            raise NoSchemaException("No <metadata> element found in XOAI XML.")

        # XOAI wraps its content in a nested <metadata xmlns="http://www.lyncode.com/xoai" ...>
        self.root = outer_metadata.find("metadata") or outer_metadata

        self.dc = self._get_element(self.root, "dc")
        self.thesis = self._get_element(self.root, "thesis")
        self.others = self._get_element(self.root, "others")
        #self.bundles = self._get_element(self.root, "bundles")

        if self.dc is None:
            raise NoSchemaException("No XOAI 'dc' metadata block found.")

        self._parse_abstract()
        self._parse_author()
        self._parse_contributors()
        self._parse_date()
        self._parse_keywords()
        self._parse_ids()
        self._parse_permissions()
        #self._parse_rights()
        self._parse_thesis_info()
        self._parse_title()
        #self._parse_esources()

        self.base_metadata = self._entity_convert(self.base_metadata)

        output = self.format(self.base_metadata, format="OtherXML")
        return output
