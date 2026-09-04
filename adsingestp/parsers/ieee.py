# Parser for IEEE conference XML

import logging
import re

from adsingestp import utils
from adsingestp.ingest_exceptions import XmlLoadException
from adsingestp.parsers.base import BaseBeautifulSoupParser
from adsingestp.parsers.jats import JATSAffils

logger = logging.getLogger(__name__)

orcid_format = re.compile(r"(\d{4}-){3}\d{3}(\d|X)")


class IEEEParser(BaseBeautifulSoupParser):
    def __init__(self):
        super(BaseBeautifulSoupParser, self).__init__()
        self.base_metadata = {}
        self.confarticle = None  # Wrapper for whole XML file: <conf-article>
        self.conffront = None  # About conference & article: <conf-article> <conf-front>
        self.confprocmeta = (
            None  # About conference proceedings: <conf-article> <conf-front> <conf-proc-meta>
        )
        self.confmeta = (
            None  # About the physical conference event: <conf-article> <conf-front> <conf-meta>
        )
        self.article = None  # About the article: <conf-article> <conf-front> <conf-article-meta>
        self.body = None  # Fulltext: <conf-article> <body>
        self.back = None  # Acknowledgments & References: <conf-article> <back>

    def _parse_abstract(self):
        abstract = self.article.find("abstract")
        if abstract:
            ab = self._remove_latex(abstract)
            abstract = ab.text.strip()
            self.base_metadata["abstract"] = abstract

    def _parse_authors(self):
        # Parse authors from <contrib-group> section

        auth_affil = JATSAffils()
        aa_output_dict = auth_affil.parse(article_metadata=self.article)

        if aa_output_dict.get("authors"):
            for auth in aa_output_dict["authors"]:
                given = auth.get("given") or ""
                if given.strip():
                    auth["given"] = " ".join(given.split())

                surname = auth.get("surname") or ""
                if surname.strip():
                    auth["surname"] = " ".join(surname.split())

                middle = auth.get("middle") or ""
                if middle.strip():
                    auth["middle"] = " ".join(middle.split())
            self.base_metadata["authors"] = aa_output_dict["authors"]

    def _parse_funding(self):
        funding = []

        # <funding-group> <award-group> <funding-source> <institution-wrap> <institution content-type="institution">

        if not self.article.find("funding-group"):
            return
        else:
            fg = self.article.find("funding-group")
            funding_stmt = fg.find("funding-statement", "").get_text(strip=True)
            award_groups = fg.find_all("award-group")

            for ag in award_groups:
                funder = {}

                # Collect all institutions under this award-group
                # <award-group> contains a single ? funding institution & all awards from that inst
                institutions = ag.select("funding-source institution-wrap institution")
                if institutions:
                    agency_names = [
                        self._clean_output(inst.get_text(strip=True))
                        for inst in institutions
                        if inst.get_text(strip=True)
                    ]
                    if agency_names:
                        # Join multiple institutions for this award-group with "; "
                        funder.setdefault("agencyname", "; ".join(agency_names))

                # Collect all award-ids under this award-group
                # <funding-group> <award-group> <award-id>
                award_ids = ag.find_all("award-id")
                if award_ids:
                    awards = [
                        self._clean_output(aid.get_text(strip=True))
                        for aid in award_ids
                        if aid.get_text(strip=True)
                    ]
                    if awards:
                        # Join multiple award numbers with comma if present
                        funder.setdefault("awardnumber", ", ".join(awards))

                if funder:
                    funding.append(funder)

        if funding:
            self.base_metadata["funding"] = funding

    def _parse_ids(self):
        self.base_metadata["ids"] = {}

        isbns = []

        # Handle ISBNs for both print & electronic
        # <isbn publication-format="print" OR "electronic">
        isbn_all = self.confprocmeta.find_all("isbn")
        isbns = []
        for i in isbn_all:
            content_type = None
            if i.get("publication-format", ""):
                pub_format = i.get("publication-format")
            isbns.append({"type": pub_format, "isbn_str": self._detag(i, [])})
        self.base_metadata["isbn"] = isbns

        # Possible TO DO: Add ISSNs
        # Conferences don't have ISSNs?
        # IEEE XML contains ISSNs only in references

        if self.article.find("article-id", {"pub-id-type": "doi"}):
            self.base_metadata["ids"]["doi"] = self.article.find(
                "article-id", {"pub-id-type": "doi"}
            ).get_text(strip=True)

        # Possible TO DO: Add publication DOIs
        # IEEE XML old DTD has these, new version does not?

    def _parse_keywords(self):
        keywords = []

        # Handle both IEEE- and author-assigned keyword sets
        for keywordset in self.article.find_all("kwd-group"):
            keyword_type = keywordset.get("kwd-group-type", "")

            for kwd in keywordset.find_all("kwd"):
                kwd = self._remove_latex(kwd)
                keyword = kwd.get_text(strip=True)
                if keyword:
                    keywords.append(
                        {
                            "system": keyword_type,
                            "string": self._clean_output(keyword),
                        }
                    )

        if keywords:
            self.base_metadata["keywords"] = keywords

    def _parse_page(self):
        fpage = self.article.find("fpage")
        lpage = self.article.find("lpage")
        article_id = self.article.find("xplore-article-id")

        fpage_num = fpage.get_text(strip=True) if fpage else None

        # if fpage = 1, use document id instead, if it exists
        if fpage_num in ("1", "01"):
            if article_id:
                id_num = article_id.get_text(strip=True)
                # cut or pad article_id to 4 chars
                if len(id_num) > 4:
                    id_num = id_num[-4:]
                else:
                    id_num = id_num.rjust(4, ".")
                self.base_metadata["page_first"] = id_num
        elif fpage_num:
            self.base_metadata["page_first"] = fpage_num
        if lpage:
            self.base_metadata["page_last"] = lpage.get_text(strip=True)

    def _parse_permissions(self):
        # Check for open-access and permissions information
        if self.article.find("permissions"):
            permissions = self.article.find("permissions")

            if permissions.find("copyright-statement"):
                copyright_statement = permissions.find("copyright-statement", "").get_text(
                    strip=True
                )
            if permissions.find("copyright-year"):
                copyright_year = permissions.find("copyright-year", "").get_text(strip=True)
            if permissions.find("copyright-holder"):
                copyright_holder = permissions.find("copyright-holder", "").get_text(strip=True)
            if permissions.find("license"):
                license = permissions.find("license", "").get_text(strip=True)

            # Format copyright string
            copyright_text = (
                "©" + copyright_year + " " + copyright_holder
            )  # + ". " + copyright_statement
            self.base_metadata["copyright"] = copyright_text

            """
            # TO DO: Are any IEEE conference articles OA?
            # Check if open access is given as "T" (true)
            if permissions.find("articleopenaccess"):
                if permissions.find("articleopenaccess").get_text(strip=True) == "T":
                    self.base_metadata.setdefault("openAccess", {}).setdefault("open", True)
            """

    def _parse_pub(self):
        # Conference title
        if self.confprocmeta.find("conf-proc-title-group"):
            if self.confprocmeta.find("conf-proc-title-group").find("conf-full-title"):
                conf_title = (
                    self.confprocmeta.find("conf-proc-title-group")
                    .find("conf-full-title")
                    .get_text(strip=True)
                )

        # Volume
        if self.confprocmeta.find("volume"):
            self.base_metadata["volume"] = self.confprocmeta.find("volume").get_text(strip=True)

        # Conference location
        if self.confmeta.find("conf-loc"):
            full_loc = self.confmeta.find("conf-loc")
            city_tag = full_loc.find("city")
            city = city_tag.get_text(strip=True) if city_tag else ""
            state_tag = full_loc.find("state")
            state = state_tag.get_text(strip=True) if state_tag else ""
            country_tag = full_loc.find("country")
            country = country_tag.get_text(strip=True) if country_tag else ""

            loc_parts = [p for p in (city, state, country) if p]
            location = ", ".join(loc_parts)

            self.base_metadata["conf_location"] = location

        # Conference dates in <conf-meta> section
        conf_start = self.confmeta.find("conf-start")
        if conf_start:
            startdate_info = self._parse_date(conf_start)
            start_year = startdate_info.get("year", "")
            start_month = startdate_info.get("month", "")
            start_day = startdate_info.get("day", "")
            start_date = f"{start_day} {start_month} {start_year}"

        conf_end = self.confmeta.find("conf-end")
        if conf_end:
            enddate_info = self._parse_date(conf_end)
            end_year = enddate_info.get("year", "")
            end_month = enddate_info.get("month", "")
            end_day = enddate_info.get("day", "")
            end_date = f"{end_day} {end_month} {end_year}"

        # Assemble conference dates
        date_parts = [p for p in (start_date, end_date) if p]
        conf_dates = " - ".join(date_parts) if date_parts else ""

        self.base_metadata["conf_date"] = conf_dates

        # Assemble %J
        pub_parts = [p for p in (conf_title, conf_dates, location) if p]
        publication = ", ".join(pub_parts) if pub_parts else ""

        self.base_metadata["publication"] = publication  # conf_title

    def _parse_date(self, date_tag):
        # Helper function to _parse_pub & _parse_pubdate

        # Use iso-8601-date attribute if it exists
        iso_attr = date_tag.get("iso-8601-date")

        # Original text values (as in XML)
        year_tag = date_tag.find("year")
        year_raw = year_tag.get_text(strip=True) if year_tag else ""

        month_tag = date_tag.find("month")
        month_raw = month_tag.get_text(strip=True) if month_tag else ""

        day_tag = date_tag.find("day")
        day_raw = day_tag.get_text(strip=True) if day_tag else ""

        # Build normalized ISO date (YYYY-MM-DD)
        # Year
        year_norm = year_raw if year_raw.isdigit() else "0000"

        # Month
        if month_raw:
            if month_raw.isdigit():
                month_norm = month_raw.zfill(2)
            else:
                month_name = month_raw[:3].lower()
                month_norm = utils.MONTH_TO_NUMBER.get(month_name, "00")
        else:
            month_norm = "00"

        # Day
        day_norm = day_raw.zfill(2) if day_raw.isdigit() else "00"

        iso_norm = iso_attr or f"{year_norm}-{month_norm}-{day_norm}"

        return {
            "iso": iso_norm,
            "year": year_raw,
            "month": month_raw,
            "day": day_raw,
        }

    def _parse_pubdate(self):
        # Publication dates in <conf-article-meta> section
        for date in self.article.find_all("pub-date"):
            date_type = date.get("pub-type", "")

            pubdate_info = self._parse_date(date)
            iso_date = pubdate_info.get("iso", "")

            if date_type == "print":
                self.base_metadata["pubdate_print"] = iso_date
            elif date_type == "electronic":
                self.base_metadata["pubdate_electronic"] = iso_date

    def _parse_references(self):
        if not self.back:
            return

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
        # Article title
        tg = self.article.find("title-group")
        if tg:
            at = tg.find("article-title")
            if at:
                at = self._remove_latex(at)
                title = at.text.strip()
                self.base_metadata["title"] = title

    def parse(self, text):
        """
        Parse IEEE XML into standard JSON format
        :param text: string, contents of XML file
        :return: parsed file contents in JSON format
        """
        try:
            d = self.bsstrtodict(text, parser="lxml-xml")
        except Exception as err:
            raise XmlLoadException(err)

        if d.find("conf-article", None):
            self.confarticle = d.find("conf-article")

            if self.confarticle.find("conf-front", None):
                self.conffront = self.confarticle.find("conf-front")

                if self.confarticle.find("conf-proc-meta", None):
                    self.confprocmeta = self.confarticle.find("conf-proc-meta")
                if self.confarticle.find("conf-meta", None):
                    self.confmeta = self.confarticle.find("conf-meta")
                if self.confarticle.find("conf-article-meta", None):
                    self.article = self.confarticle.find("conf-article-meta")

            if self.confarticle.find("body", None):
                self.body = self.confarticle.find("body")

            if self.confarticle.find("back", None):
                self.back = self.confarticle.find("back")

        self._parse_abstract()
        self._parse_authors()
        self._parse_funding()
        self._parse_ids()
        self._parse_keywords()
        self._parse_page()
        self._parse_permissions()
        self._parse_pub()
        self._parse_pubdate()
        self._parse_references()
        self._parse_title()

        output = self.format(self.base_metadata, format="IEEE")

        return output
