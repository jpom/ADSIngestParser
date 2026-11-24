# Springer parser for metadata-only (not full-text) book XML files
# /proj/ads_abstracts/sources/SPRINGER/files/*
# /proj/ads_abstracts/sources/SPRINGER/files.done/*

# See BITS 2.2 tag library:
# https://jats.nlm.nih.gov/extensions/bits/tag-library/2.2/

import logging
import re

from adsingestp.ingest_exceptions import XmlLoadException
from adsingestp.parsers.base import BaseBeautifulSoupParser
from adsingestp.parsers.jats import JATSAffils

logger = logging.getLogger(__name__)

orcid_format = re.compile(r"(\d{4}-){3}\d{3}(\d|X)")


class SpringerParser(BaseBeautifulSoupParser):
    def __init__(self):
        super(BaseBeautifulSoupParser, self).__init__()
        self.base_metadata = {}
        self.toplevel = None
        self.collectionmeta = None
        # self.book = None  # delete in favor of toplevel
        self.bookmeta = None
        # self.toplevel = None  # delete in favor of toplevel
        self.bookpart = None
        self.bookpartmeta = None
        self.contenttype = None
        self.frontmatter = None  # <book> <front-matter>
        self.backmatter = None  # <book> <book-back>
        self.back = None  # <book> <book-back> <book-part> <back>

    def _parse_abstract(self):
        # Springer uses the id attribute for <abstract>, e.g.,
        # <abstract xml:lang="en" id="Abs1" specific-use="web-only">
        # <abstract id="Abs1_5" xml:lang="en">
        # So far I haven't seen >1 abstract per XML file but find_all just in case

        # TO DO: Store multiple abstracts. Currently this overwrites.

        if self.bookpartmeta is None:
            return

        if self.bookpartmeta.find("abstract"):
            for p in self.bookpartmeta.find("abstract").find_all("p"):
                abstract = p.text.strip()
                self.base_metadata["abstract"] = abstract
        else:
            logger.warning("No abstract found")

    def _parse_authors(self):
        # If book is part of a series
        # Ignore Series Editors & affiliations
        # <collection-meta> <contrib-group>

        contrib_affil = JATSAffils()

        # If manuscript, author(s) are for the book
        if self.contenttype == "manuscript":
            author_meta = self.bookmeta
            au_output_dict = contrib_affil.parse(author_meta)
            authors = au_output_dict.get("authors") or []

        # If edited vol, author(s) are for the chapters
        #   <book-part-meta> <contrib-group>
        # and editor(s) are for the book
        #   <book-meta> <contrib-group>
        elif self.contenttype == "edited":
            # JATSAffils.parse() extracts/decomposes <contrib-group>, so parse bookmeta once
            #editor_meta = self.bookmeta
            #ed_output_dict = contrib_affil.parse(editor_meta)
            ed_output_dict = JATSAffils().parse(self.bookmeta)
            editors = [
                c for c in (ed_output_dict.get("contributors") or [])
                if c.get("role", "").lower() == "editor"
            ]

            if editors:
                for ed in editors:
                    if ed.get("given-names"):
                        ed["given"] = " ".join(ed["given"].split())
                    if ed.get("surname"):
                        ed["surname"] = " ".join(ed["surname"].split())
                self.base_metadata["editors"] = editors
            else:
                logger.warning("No editors found")

            # BookFrontMatter or BookBackMatter: treat editor(s) as author(s)
            if self.frontmatter or self.backmatter:
                authors = editors
            else:
                au_output_dict = JATSAffils().parse(self.bookpartmeta)
                authors = au_output_dict.get("authors") or []

            """
            author_meta = self.bookpartmeta
            au_output_dict = contrib_affil.parse(author_meta)
            authors = au_output_dict.get("authors") or []

            # If BookFrontMatter or BookBackMatter, treat editor(s) as author(s)
            if self.frontmatter or self.backmatter:
                author_meta = self.bookmeta
                au_output_dict = contrib_affil.parse(author_meta)
                authors = au_output_dict.get("authors") or []
            """

        if authors:
            for auth in authors:
                if auth.get("given-names"):
                    auth["given"] = " ".join(auth["given"].split())
                if auth.get("surname"):
                    auth["surname"] = " ".join(auth["surname"].split())
            self.base_metadata["authors"] = authors
        else:
            logger.warning("No authors found")

    def _parse_collection(self):
        # Pass <book-meta> <custom-meta-group> <custom-meta> <meta-name>book-subject-primary
        # and <meta-name>book-subject-secondary in %X
        # Removed in postprocessing; used to create %W
        # TO DO: Change as needed if Collection is ever added to the ingest data model
        self.base_metadata["comments"] = []
        su_secondary = []
        subjects = []

        if self.bookmeta.find("custom-meta-group"):
            cm_group = self.bookmeta.find("custom-meta-group")
        else:
            logger.warning("No custom-meta-group found")

        for cm in cm_group.find_all("custom-meta"):
            meta_name_tag = cm.find("meta-name")
            meta_value_tag = cm.find("meta-value")

            # <custom-meta> should always contain both <meta-name> & <meta-value> but just in case
            if not meta_name_tag or not meta_value_tag:
                logger.warning("No meta_name_tag or meta_value_tag found")
                continue

            meta_name = meta_name_tag.get_text(strip=True)
            meta_value = meta_value_tag.get_text(strip=True)

            # Keeping these separate in case we want to use primary but not secondary
            # There is 1 primary, may be >1 secondary
            if meta_name == "book-subject-primary":
                subjects.append(meta_value)
            elif meta_name == "book-subject-secondary":
                su_secondary.append(meta_value)
                subjects.append(meta_value)

        if subjects:
            self.base_metadata["comments"].append({"text": "; ".join(subjects)})

    def _parse_ids(self):
        self.base_metadata["ids"] = {}
        doi = None

        # Get book DOI for manuscripts, frontmatter, or backmatter
        # Get chapter DOI for chapters of edited vols
        if self.contenttype == "manuscript" or self.frontmatter or self.backmatter:
            # No chapter DOI for these, so use the book DOI
            tag = self.bookmeta.find("book-id", {"book-id-type": "doi"})
        else:
            # Chapter DOI for edited vols
            tag = (
                self.bookpartmeta.find("book-part-id", {"book-part-id-type": "doi"})
                if self.bookpartmeta
                else None
            )
        if tag:
            doi = tag.get_text(strip=True)
        if doi:
            self.base_metadata["ids"]["doi"] = doi
        else:
            logger.warning("No doi found")

        # Handle both print & electronic ISBNs
        # <isbn content-type="[ppub or epub]">
        isbn_all = self.bookmeta.find_all("isbn")
        isbns = []
        for i in isbn_all:
            content_type = None
            if i.get("content-type", ""):
                content_type = i.get("content-type")
            isbns.append({"type": content_type, "isbn_str": self._detag(i, [])})
        self.base_metadata["isbn"] = isbns

        # Handle both print & electronic ISSNs
        # <issn publication-format="[print or electronic]">
        # Only series have ISSNs
        if self.collectionmeta:
            issn_all = self.collectionmeta.find_all("issn")
            issns = []
            for i in issn_all:
                if i.get("publication-format", ""):
                    content_type = i.get("publication-format")
                issns.append({content_type, self._detag(i, [])})
            self.base_metadata["issn"] = issns

    def _parse_keywords(self):
        # Not all Springer XML contains keywords
        # Only use keywords in <book-part-meta>
        # BITS allows keywords to be contained in <book-meta> or <collection-meta>
        # but Springer books doesn't seem to do that?
        # BITS also allows <kwd-group> to contain <compound-kwd> & <nested-kwd>
        # but Springer books doesn't seem to do that either?

        keywords = []
        kwd_groups = []

        # BookFrontMatter or BookBackMatter do not contain keywords
        if self.frontmatter or self.backmatter:
            return

        kwd_groups = self.bookpartmeta.find_all("kwd-group")

        # Handle multiple <kwd-group>s
        # BITS allows multiple keyword groups
        # but Springer books doesn't seem to do that either?
        for kwd_group in kwd_groups:
            kwd_type = kwd_group.get("kwd-group-type", "")

            for kwd in kwd_group.find_all("kwd"):
                keyword = kwd.get_text(strip=True)
                if keyword:
                    keywords.append(
                        {
                            "system": kwd_type,
                            "string": self._clean_output(keyword),
                        }
                    )
        if keywords:
            self.base_metadata["keywords"] = keywords
        else:
            logger.warning("No keywords found")

    def _parse_page(self):
        fpage = None
        e_id = None
        lpage = None
        pagerange = None

        # Get page numbers for chapters only
        # Don't want page numbers for backmatter for book-level record
        if self.frontmatter or self.backmatter:
            return

        # Get page numbers for chapters of edited vols only
        #if self.contenttype == "edited":
        fpage = self.bookpartmeta.find("fpage")
        e_id = self.bookpartmeta.find("elocation-id")
        lpage = self.bookpartmeta.find("lpage")
        pagerange = self.bookpartmeta.find("page-range")

        # Don't want page numbers for book-level record
        """
        elif self.contenttype == "manuscript":
            fpage = self.bookpartmeta.find("fpage")
            e_id = self.bookpartmeta.find("elocation-id")
            lpage = self.bookpartmeta.find("lpage")
            pagerange = self.bookpartmeta.find("page-range")
        """

        if fpage:
            self.base_metadata["page_first"] = self._detag(fpage, [])

        if e_id:
            self.base_metadata["electronic_id"] = self._detag(e_id, [])

        if lpage == fpage:
            lpage = None
        if lpage:
            self.base_metadata["page_last"] = self._detag(lpage, [])

        if pagerange:
            self.base_metadata["numpages"] = self._detag(pagerange, [])
        elif fpage and lpage:  # Construct page range
            self.base_metadata["page_range"] = (
                self._detag(fpage, []) + "-" + (self._detag(lpage, []))
            )
        else:
            self.base_metadata["page_range"] = fpage

        # <book-page-count> is only for whole book

    def _parse_permissions(self):
        # <permissions> appears in both <book-meta> and <book-part-meta>
        # Use only <book-meta>
        if self.bookmeta.find("permissions"):
            permissions = self.bookmeta.find("permissions")

            # In case we have to construct a copyright statement
            # copyright_year = permissions.find("copyright-year")
            # copyright_holder = permissions.find("copyright-holder")

            # <copyright-statement content-type="compact"> if exists
            # else <copyright-statement>
            compact_cs = permissions.find("copyright-statement", attrs={"content-type": "compact"})
            if compact_cs is not None:
                copyright_statement = compact_cs.get_text(strip=True)
            else:
                cs = permissions.find("copyright-statement")
                copyright_statement = cs.get_text(strip=True) if cs else None

            self.base_metadata["copyright"] = copyright_statement

            # the <license> license-type attribute is only used ="open-access"
            licenses = permissions.find_all("license")
            for lic in licenses:
                if lic.get("license-type", None) == "open-access":
                    self.base_metadata.setdefault("openAccess", {}).setdefault("open", True)
                if lic.find("license-p"):
                    license_text = lic.find("license-p")
                    if license_text:
                        self.base_metadata.setdefault("openAccess", {}).setdefault(
                            "license",
                            self._detag(
                                license_text.get_text(), self.HTML_TAGSET["license"]
                            ).strip(),
                        )
                        license_uri = license_text.find("ext-link")
                        if license_uri:
                            if license_uri.get("xlink:href", None):
                                license_uri_value = license_uri.get("xlink:href", None)
                                self.base_metadata.setdefault("openAccess", {}).setdefault(
                                    "licenseURL", self._detag(license_uri_value, [])
                                )

    def _parse_pubdate(self):
        year = self.bookmeta.find("permissions").find("copyright-year").get_text()
        if year:
            self.base_metadata["pubdate_print"] = year
        else:
            logger.warning("No pubdate found")

    # Manuscripts: refs are in backmatter
    # Edited volumes: refs are in chapters
    # <back> <ref-list> <ref>
    def _parse_references(self):
        if self.back is not None:
            ref_list_text = []
            if self.back.find("ref-list"):
                ref_results = self.back.find("ref-list").find_all("ref")
            else:
                ref_results = []
            for r in ref_results:
                # output raw XML for reference service to parse later
                s = str(r.extract()).replace("\n", " ").replace("\xa0", " ")
                ref_list_text.append(s)
            self.base_metadata["references"] = ref_list_text

    def _parse_title(self):
        # 4 possible titles:
        # Series title: <collection-meta collection-type="series"> <title-group> <title>
        # Subseries title: <collection-meta collection-type="subseries"> <title-group> <title>
        # Book title: <book-meta> <book-title-group> <book-title> & <subtitle>
        # Chapter title: <book-part> <book-part-meta> <title-group> <title> (no subtitle)

        # If book is part of a series, get series title
        # Ignore subseries title, if one exists
        if self.collectionmeta:
            for cm in self.collectionmeta.find_all("collection-meta"):
                ctype = cm.get("collection-type")
                if ctype not in ("series", "subseries"):
                    continue

                ti = cm.find("title-group").find("title").get_text(strip=True)

                if ctype == "series":
                    series_title = ti
                    self.base_metadata["series_title"] = series_title
                # elif ctype == "subseries":
                #    subseries_title = ti
                #    self.base_metadata["series_title"] = series_title + ": " + subseries_title

            # Volume number in series is in <book-meta>
            volnum = self.bookmeta.find("book-volume-number").get_text(strip=True)
            if volnum:
                self.base_metadata["volume"] = volnum

        # Get book title & subtitle
        book_title = None
        book_subtitle = None

        btg = self.bookmeta.find("book-title-group")
        book_title = btg.find("book-title").get_text()
        if btg.find("subtitle"):
            book_subtitle = btg.find("subtitle").get_text()

        self.base_metadata["title"] = book_title
        self.base_metadata["subtitle"] = book_subtitle

        # If Chapter, then title = chapter title
        if not (self.frontmatter or self.backmatter):
            if self.bookpartmeta.find("title-group").find("title"):
                chapter_title = self.bookpartmeta.find("title-group").find("title").get_text()
            self.base_metadata["title"] = chapter_title
            self.base_metadata["subtitle"] = ""

        # publication = book for ALL content types (chapters & frontmatter)
        if book_subtitle:
            self.base_metadata["publication"] = f"{book_title}: {book_subtitle}"
        else:
            self.base_metadata["publication"] = book_title

    def parse(self, text):
        if hasattr(self, "filename"):
            print(self.filename)

        try:
            d = self.bsstrtodict(text, parser="lxml-xml")
        except Exception as err:
            raise XmlLoadException(err)

        # TOP LEVEL ELEMENTS
        # If BookFrontMatter or BookBackMatter, top-level element is <book>
        if d.find("book", None):
            self.toplevel = d.find("book")

        # If chapter, top-level element is <book-part-wrapper>
        if d.find("book-part-wrapper", None):
            self.toplevel = d.find("book-part-wrapper")

        # <book-meta> is 2nd level for all files
        if self.toplevel.find("book-meta", None):
            self.bookmeta = self.toplevel.find("book-meta")

        # FRONT MATTER
        # Only BookFrontMatter files contain <book> <front-matter>
        if self.toplevel.find("front-matter", None):
            self.frontmatter = self.toplevel.find("front-matter")

            if self.frontmatter.find("book-part-meta", None):
                self.bookpartmeta = self.frontmatter.find("book-part-meta")

        # CHAPTERS
        # Book has <collection-meta> only if part of a series
        if self.toplevel.find("collection-meta", None):
            self.collectionmeta = self.toplevel.find("collection-meta")
        # <collection-meta> has 2 collection-type="series" & "subseries"

        # All books have <book-meta>
        if self.toplevel.find("book-meta", None):
            self.bookmeta = self.toplevel.find("book-meta")

        # Get <book-part book-part-type="chapter"> only
        # Ignore <book-part book-part-type="part"> <book-part-meta> as it only contains part title
        if self.toplevel.find("book-part", {"book-part-type": "chapter"}):
            self.bookpart = self.toplevel.find("book-part", {"book-part-type": "chapter"})
            if self.bookpart.find("book-part-meta", None):
                self.bookpartmeta = self.bookpart.find("book-part-meta")

            # For manuscripts, refs live in <book-part-wrapper> <book-part> <back>
            if self.bookpart.find("back"):
                self.back = self.bookpart.find("back")

        # BACK MATTER
        # Only BookBackMatter files contain <book> <book-back>
        if self.toplevel.find("book-back", None):
            self.backmatter = self.toplevel.find("book-back")

            if self.backmatter.find("book-part-meta", None):
                self.bookpartmeta = self.backmatter.find("book-part-meta")

            # For edited vols, refs live in <book-back> <book-part> <back>
            if self.backmatter.find("book-part"):
                self.bookbackpart = self.backmatter.find("book-part")
                if self.bookbackpart.find("back"):
                    self.back = self.bookbackpart.find("back")

        # ALL FILES
        # <contrib-group> provides names of book author(s) or editor(s)
        contrib_group = self.bookmeta.find("contrib-group")
        if contrib_group is not None:
            content_type = contrib_group.get("content-type", "").lower()

        # If book type is manuscript
        # <contrib-group content-type="book author"> or "book authors"
        # Only parse front & back matter, do not parse chapters
        if "author" in content_type:
            self.contenttype = "manuscript"
        # If book type is edited volume
        # <contrib-group content-type="book editor"> or "book editors"
        # Parse front matter & chapters
        elif "editor" in content_type:
            self.contenttype = "edited"
        else:
            raise Exception("XML file is of unknown content type")

        self._parse_abstract()
        self._parse_authors()
        self._parse_collection()
        self._parse_ids()
        self._parse_keywords()
        self._parse_page()
        self._parse_permissions()
        self._parse_pubdate()
        self._parse_references()
        self._parse_title()

        output = self.format(self.base_metadata, format="Springer")

        return output
