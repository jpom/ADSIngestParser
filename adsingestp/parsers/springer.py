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
        self.bookmeta = None
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
                p = self._remove_latex(p)
                abstract = p.text.strip()
                self.base_metadata["abstract"] = abstract

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
            editor_meta = self.bookmeta
            ed_output_dict = contrib_affil.parse(editor_meta)
            editors = ed_output_dict.get("editors") or []

            if editors:
                for ed in editors:
                    if ed.get("given-names"):
                        ed["given"] = " ".join(ed["given"].split())
                    if ed.get("surname"):
                        ed["surname"] = " ".join(ed["surname"].split())
                self.base_metadata["editors"] = editors

            author_meta = self.bookpartmeta
            au_output_dict = contrib_affil.parse(author_meta)
            authors = au_output_dict.get("authors") or []

            # If BookFrontMatter or BookBackMatter, treat editor(s) as author(s)
            if self.frontmatter or self.backmatter:
                author_meta = self.bookmeta
                au_output_dict = contrib_affil.parse(author_meta)
                authors = au_output_dict.get("authors") or []

        if authors:
            for auth in authors:
                if auth.get("given-names"):
                    auth["given"] = " ".join(auth["given"].split())
                if auth.get("surname"):
                    auth["surname"] = " ".join(auth["surname"].split())
            self.base_metadata["authors"] = authors

    def _parse_collection(self):
        # Pass <book-meta> <custom-meta-group> <custom-meta> <meta-name>book-subject-secondary
        # Removed in postprocessing; used to create %W
        # TO DO: Change as needed if Collection is ever added to the ingest data model
        self.base_metadata["comments"] = []
        subjects = []

        if self.bookmeta.find("custom-meta-group"):
            cm_group = self.bookmeta.find("custom-meta-group")

        for cm in cm_group.find_all("custom-meta"):
            meta_name_tag = cm.find("meta-name")
            meta_value_tag = cm.find("meta-value")

            # <custom-meta> should always contain both <meta-name> & <meta-value> but just in case
            if not meta_name_tag or not meta_value_tag:
                continue

            meta_name = meta_name_tag.get_text(strip=True)
            meta_value = meta_value_tag.get_text(strip=True)

            # XML contains book-subject-collection, book-subject-primary, & book-subject-secondary
            # Sadly, collection & primary terms are too broad to be useful
            # Have to use much longer term list for book-subject-secondary
            if meta_name == "book-subject-secondary":
                subjects.append(meta_value)

        if subjects:
            self.base_metadata["comments"].append({"text": "; ".join(subjects)})

    def _parse_ids(self):
        self.base_metadata["ids"] = {}

        # Get book DOI for manuscripts
        if self.contenttype == "manuscript":
            if self.bookmeta.find("book-id", {"book-id-type": "doi"}):
                self.base_metadata["ids"]["doi"] = self.bookmeta.find(
                    "book-id", {"book-id-type": "doi"}
                ).get_text(strip=True)

        # Get chapter DOI for edited vols
        # TO DO: Also get book DOI for edited vols?
        if self.contenttype == "edited":
            if self.bookpartmeta.find("book-part-id", {"book-part-id-type": "doi"}):
                self.base_metadata["ids"]["doi"] = self.bookpartmeta.find(
                    "book-part-id", {"book-part-id-type": "doi"}
                ).get_text(strip=True)

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
                kwd = self._remove_latex(kwd)
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

    def _parse_page(self):
        fpage = None
        e_id = None
        lpage = None
        pagerange = None

        # Get page numbers for chapters of edited vols only
        if self.contenttype == "edited":
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
        if self.bookmeta.find("permissions").find("copyright-year"):
            self.base_metadata["pubdate_print"] = (
                self.bookmeta.find("permissions").find("copyright-year").get_text()
            )

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
                s = self._remove_latex(r)
                t = str(s.extract()).replace("\n", " ").replace("\xa0", " ")
                ref_list_text.append(t)
            self.base_metadata["references"] = ref_list_text

    def _parse_title(self):
        # 4 possible titles:
        # Series title: <collection-meta collection-type="series"> <title-group> <title>
        # Subseries title: <collection-meta collection-type="subseries"> <title-group> <title>
        # Book title: <book-meta> <book-title-group> <book-title> & <subtitle>
        # Chapter title: <book-part> <book-part-meta> <title-group> <title> (no subtitle)

        # If book is part of a series, get series title
        # <collection-meta> has 2 collection-type="series" & "subseries"
        # Ignore subseries title, if one exists
        series_title = None

        if self.collectionmeta:
            title_group = self.collectionmeta.find("title-group")
            if title_group:
                sti = title_group.find("title")
                if sti:
                    sti = self._remove_latex(sti)
                    series_title = sti.text.strip()

            # Volume number in series is in <book-meta>
            if self.bookmeta.find("book-volume-number"):
                self.base_metadata["volume"] = self.bookmeta.find("book-volume-number").get_text(
                    strip=True
                )

        # Get book title & subtitle
        book_title = None
        book_subtitle = None

        btg = self.bookmeta.find("book-title-group")
        if btg:
            bt = btg.find("book-title")
            if bt:
                bt = self._remove_latex(bt)
                book_title = bt.text.strip()
            subti = btg.find("subtitle")
            if subti:
                subti = self._remove_latex(subti)
                book_subtitle = subti.text.strip()

        if self.frontmatter or self.backmatter:
            self.base_metadata["title"] = book_title
            self.base_metadata["subtitle"] = book_subtitle
        else:  # If Chapter, then title = chapter title
            tg = self.bookpartmeta.find("title-group")
            if tg:
                ct = tg.find("title")
                if ct:
                    ct = self._remove_latex(ct)
                    chapter_title = ct.text.strip()
            self.base_metadata["title"] = chapter_title
            self.base_metadata["subtitle"] = ""

        # publication = book for ALL content types (chapters & frontmatter)
        publication = None
        if book_subtitle:
            publication = book_title + ": " + book_subtitle
        else:
            publication = book_title
        if series_title:
            publication = publication + ". " + series_title
        self.base_metadata["publication"] = publication

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

        # ALL FILES
        # Book has <collection-meta> only if part of a series
        if self.toplevel.find("collection-meta", None):
            self.collectionmeta = self.toplevel.find("collection-meta")

        # All books have <book-meta>
        if self.toplevel.find("book-meta", None):
            self.bookmeta = self.toplevel.find("book-meta")

        # <contrib-group> provides names of book author(s) or editor(s)
        contrib_group = self.bookmeta.find("contrib-group")
        if contrib_group is not None:
            content_type = contrib_group.get("content-type", "").lower()

        # If book type is manuscript
        # <contrib-group content-type="book author"> or "book authors"
        # Only parse front & back matter, skip chapters
        if "author" in content_type:
            self.contenttype = "manuscript"

        # If book type is edited volume
        # <contrib-group content-type="book editor"> or "book editors"
        # Parse front matter & chapters
        elif "editor" in content_type:
            self.contenttype = "edited"
        else:
            raise Exception("XML file is of unknown content type")

        # FRONT MATTER
        # Only BookFrontMatter files contain <book> <front-matter>
        # Skip BookFrontMatter files
        if self.toplevel.find("front-matter", None):
            self.frontmatter = self.toplevel.find("front-matter")
            return
        if self.toplevel.find("book-part", {"book-part-type": "part"}):
            self.bookpart = self.toplevel.find("book-part", {"book-part-type": "part"})
            if self.bookpart.find("front-matter", None):
                return

        # CHAPTERS
        # Get <book-part book-part-type="chapter"> only
        # Ignore <book-part book-part-type="part"> <book-part-meta> as it only contains part title
        if self.toplevel.find("book-part", {"book-part-type": "chapter"}):
            self.bookpart = self.toplevel.find("book-part", {"book-part-type": "chapter"})
            # If a manuscript, skip chapters: we only want book-level records
            if self.contenttype == "manuscript":
                return

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
