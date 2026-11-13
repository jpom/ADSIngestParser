import collections.abc
import html
import logging
import os
import re

import nameparser

from adsingestp.ingest_exceptions import AuthorParserException

logger = logging.getLogger(__name__)

MONTH_TO_NUMBER = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

ROMAN_TO_NUMBER = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
    "xi": "11",
    "xii": "12",
    "xiii": "13",
    "xiv": "14",
    "xv": "15",
    "xvi": "16",
    "xvii": "17",
    "xviii": "18",
    "xix": "19",
    "xx": "20",
    "xxi": "21",
    "xxii": "22",
    "xxiii": "23",
    "xxiv": "24",
    "xxv": "25",
    "xxvi": "26",
    "xxvii": "27",
    "xxviii": "28",
    "xxix": "29",
    "xxx": "30",
}

# TODO have MT review this list
related_trans_dict = {
    "IsCitedBy": "related",
    "IsSupplementTo": "supplement",
    "IsContinuedBy": "series",
    "IsDescribedBy": "related",
    "HasMetadata": "related",
    "HasVersion": "related",
    "IsNewVersionOf": "updated",
    "IsPartOf": "related",
    "IsReferencedBy": "related",
    "IsDocumentedBy": "related",
    "IsCompiledBy": "related",
    "IsVariantFormOf": "related",
    "IsIdenticalTo": "related",
    "IsReviewedBy": "related",
    "IsDerivedFrom": "related",
    "Requires": "related",
    "IsObsoletedBy": "related",
    "Cites": "related",
    "IsSupplementedBy": "related",
    "Continues": "series",
    "Describes": "related",
    "IsMetadataFor": "related",
    "IsVersionOf": "related",
    "PreviousVersionOf": "related",
    "HasPart": "related",
    "References": "related",
    "Documents": "related",
    "Compiles": "related",
    "IsOriginalFormOf": "related",
    "*": "related",
    "Reviews": "related",
    "IsSourceOf": "related",
    "IsRequiredBy": "related",
    "Obsoletes": "related",
    "Erratum": "errata",
}

AIP_DISCARD_KEYWORDS = [
    "ARTICLES",
    "REGULAR ARTICLES",
    "SOFTWARE",
    "PERSPECTIVES",
    "EDITORIALS",
    "PAPERS",
    "AWARDS",
    "NOTES AND DISCUSSIONS",
    "PAPERS",
    "LETTERS TO THE EDITOR",
    "REVIEWS",
    "REVIEW ARTICLE",
    "NEW PRODUCTS",
    "AWARDS",
    "ERRATA",
    "FAST TRACK",
    "COMMUNICATIONS",
    "LETTERS",
]


class AuthorNames(object):
    """
    Author names parser
    """

    default_collaborations_params = {
        "keywords": ["group", "team", "collaboration", "consortium"],
        "first_author_delimiter": ":",
        "remove_the": True,
        "fix_arXiv_mixed_collaboration_string": False,
    }
    parse_titles = False

    def __init__(self):
        data_dirname = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "data_files", "author_names"
        )
        logger.info("Loading ADS author names from: %s", data_dirname)
        self.first_names = self._read_datfile(os.path.join(data_dirname, "first.dat"))
        self.last_names = self._read_datfile(os.path.join(data_dirname, "last.dat"))
        suffix_names = self._read_datfile(os.path.join(data_dirname, "suffixes.dat"))

        # Remove the preset suffixes and add back only our suffixes
        nameparser.config.CONSTANTS.suffix_acronyms.remove(
            *nameparser.config.CONSTANTS.suffix_acronyms
        )
        nameparser.config.CONSTANTS.suffix_not_acronyms.remove(
            *nameparser.config.CONSTANTS.suffix_not_acronyms
        )
        for s in suffix_names:
            nameparser.config.CONSTANTS.suffix_acronyms.add(s)
            nameparser.config.CONSTANTS.suffix_not_acronyms.add(s)

        if self.parse_titles:
            prefix_names = self._read_datfile(os.path.join(data_dirname, "prefixes.dat"))

            # Remove the preset titles and add back only our titles
            nameparser.config.CONSTANTS.titles.remove(*nameparser.config.CONSTANTS.titles)
            for s in prefix_names:
                nameparser.config.CONSTANTS.titles.add(s)

        # Compile regular expressions
        self.regex_initial = re.compile(r"\. *(?!,)")
        self.regex_etal = re.compile(r",? et ?al\.?")
        self.regex_and = re.compile(r" and ")
        self.regex_first_char = re.compile(r"^-|^'")
        self.regex_the = re.compile(r"^[Tt]he ")
        self.regex_multiple_sp = re.compile(r" +")

    def _read_datfile(self, filename):
        output_list = []
        with open(filename, "r") as fp:
            for line in fp.readlines():
                if line.strip() != "" and line[0] != "#":
                    output_list.append(line.strip())
        return output_list

    def _extract_collaboration(self, author_str, default_to_last_name, collaborations_params):
        """
        Verifies if the author name string contains a collaboration string
        The collaboration extraction can be controlled by the dictionary
        'collaborations_params'.
        """
        corrected_collaboration_list = []
        is_collaboration_str = False
        for keyword in collaborations_params["keywords"]:
            if keyword in author_str.lower():
                is_collaboration_str = True
                if collaborations_params["first_author_delimiter"]:
                    # Based on an arXiv author case: "<tag>Collaboration: Name, Author</tag>"
                    authors_list = author_str.split(
                        collaborations_params["first_author_delimiter"]
                    )
                else:
                    authors_list = list(author_str)
                for author in authors_list:
                    if keyword in author.lower():
                        # collaboration
                        corrected_collaboration_str_tmp = re.sub(
                            keyword, keyword.capitalize(), author
                        )
                        if collaborations_params["remove_the"]:
                            corrected_collaboration_str_tmp = self.regex_the.sub(
                                "", corrected_collaboration_str_tmp
                            )

                        if collaborations_params["fix_arXiv_mixed_collaboration_string"]:
                            # TODO: Think a better way to account for this
                            # specific cases if there's a ',' in the string, it probably includes the 1st author
                            string_list = corrected_collaboration_str_tmp.split(",")
                            if len(string_list) == 2:
                                # Based on an arXiv author case: "collaboration,
                                # Gaia"
                                string_list.reverse()
                                corrected_collaboration_str_tmp = " ".join(string_list)
                        corrected_collaboration_list.append(
                            {
                                "collab": corrected_collaboration_str_tmp.strip(),
                                "nameraw": author.strip(),
                            }
                        )
                    else:
                        # non-collaboration author
                        corrected_collaboration_list.append(
                            self._parse_author_name(author.strip(), default_to_last_name)
                        )
                break

        return is_collaboration_str, corrected_collaboration_list

    def _clean_author_name(self, author_str):
        """
        Remove useless characters in author name string
        """
        author_str = self.regex_initial.sub(". ", author_str)
        author_str = self.regex_etal.sub("", author_str)
        author_str = self.regex_and.sub(" ", author_str)
        author_str = author_str.replace(" .", ".").replace(" ,", ",")
        author_str = self.regex_multiple_sp.sub(" ", author_str)
        author_str = author_str.strip()
        return author_str

    def _parse_author_name(self, author_str, default_to_last_name):
        """
        Automatically detect first and last names in an author name string and parse into parts
        :param author_str: raw author name string
        :param default_to_last_name: boolean; if true, ambiguous middle names added to last name, if false, kept as middle name

        :return dict of parsed author name parts
        """
        author = nameparser.HumanName(author_str)
        if author.first == "Jr." and author.suffix != "":
            author.first = author.suffix
            author.suffix = "Jr."

        if author.middle:
            # Keep middle names as middle if detected as a first name,
            # or move to last name if detected as so
            # or move to the default
            keep_as_middle = []
            add_to_last = []
            last_name_found = False

            middle_name_list = author.middle.split()

            for middle_name in middle_name_list:
                middle_name_length = len(
                    html.unescape(middle_name).strip(".").strip("-")
                )  # Ignore '.' or '-' at the beginning/end of the string
                middle_name_upper = middle_name.upper()
                if (  # ignore "-" or "'" at the beginnning of the string
                    self.regex_first_char.sub("", middle_name_upper) in self.first_names
                    and self.regex_first_char.sub("", middle_name_upper) not in self.last_names
                ) or (
                    middle_name_length <= 2
                    and middle_name_upper not in self.last_names
                    and "'" not in middle_name
                ):
                    # Case: First name found
                    # Middle name is found in the first names ADS list and not in the last names ADS list
                    if last_name_found:
                        # Move all previously detected first names to last name since we are in a situation where
                        # we detected:
                        # middle name: L F
                        # hence we correct it to:
                        # middle name: F F
                        # where F is first/middle name and L is last name
                        keep_as_middle += add_to_last
                        add_to_last = []
                        last_name_found = False
                    keep_as_middle.append(middle_name)
                elif last_name_found or middle_name_upper in self.last_names:
                    # Case: Last name found
                    add_to_last.append(middle_name)
                    last_name_found = True
                else:
                    # Case: Unknown
                    # Middle name not found in the first or last names ADS list
                    if default_to_last_name:
                        add_to_last.append(middle_name)
                        last_name_found = True
                    else:
                        keep_as_middle.append(middle_name)

            author.middle = " ".join(keep_as_middle)
            # [MT 2020 Oct 07, can't reproduce where .reverse() is necessary?]
            # add_to_last.reverse()
            author.last = " ".join(add_to_last + [author.last])

        # Verify that no first names appear in the detected last name
        if author.last:
            if isinstance(author.last, str):
                last_name_list = author.last.split()
            elif isinstance(author.last, collections.abc.Sequence):
                last_name_list = author.last.copy()
            else:
                raise AuthorParserException(
                    "Author name %s of unhandled type: %s", author.last, type(author.last)
                )
            # At this point we already know it has at least 1 last name and
            # we will not question that one (in the last position)
            last_last_name = last_name_list.pop()
            verified_last_name_list = []
            last_name_found = False
            for last_name in last_name_list:
                last_name_upper = last_name.upper()
                if (
                    last_name_upper in self.first_names
                    and last_name_upper not in self.last_names
                    and not last_name_found
                ):
                    author.middle = author.middle + " " + last_name
                else:
                    verified_last_name_list.append(last_name)
                    last_name_found = True

            verified_last_name_list = verified_last_name_list + [last_last_name]
            author.last = " ".join(verified_last_name_list)

        parsed_author = {}
        parsed_author["given"] = self.regex_multiple_sp.sub(" ", html.unescape(author.first))
        parsed_author["middle"] = self.regex_multiple_sp.sub(" ", html.unescape(author.middle))
        parsed_author["surname"] = self.regex_multiple_sp.sub(" ", html.unescape(author.last))
        parsed_author["suffix"] = self.regex_multiple_sp.sub(" ", html.unescape(author.suffix))
        parsed_author["prefix"] = self.regex_multiple_sp.sub(" ", html.unescape(author.title))
        parsed_author["nameraw"] = self.regex_multiple_sp.sub(" ", html.unescape(author_str))

        return parsed_author

    def parse(
        self,
        author_str,
        default_to_last_name=True,
        collaborations_params=default_collaborations_params,
        parse_titles=False,
    ):
        """
        Receives an author string and returns a list of re-formatted parsed author dictionaries

        It also verifies if an author name string contains a collaboration
        string.  The collaboration extraction can be controlled by collaborations_params

        :param author_str: raw author string for a single author
        :param default_to_last_name: Boolean param to set whether unknown names parsed as middle named should be kept as
        middle names or moved to the last name. Default: True
        :param collaborations_params: dict that controls how collaborations are parsed; only need to pass in
        key/values that are different from the default params. Keys:
            - keywords [list of strings]: Keywords that appear in strings that
              should be identifier as collaboration strings. Default: "group", "team",
              "collaboration", "consortium"
            - remove_the [boolean]: Remove the article 'The' from collaboration
              strings (e.g., 'The collaboration'). Default: True
            - first_author_delimiter [string]: Some collaboration strings include
              the first author separated by a delimiter (e.g., The collaboration:
              First author), the delimiter can be specified in this variable,
              otherwise None or False values can be provided to avoid trying to
              extract first authors from collaboration strings. Default: ':'
            - fix_arXiv_mixed_collaboration_string [boolean]: Some arXiv entries
              mix the collaboration string with the collaboration string.
              (e.g. 'collaboration, Gaia'). Default: False
        :param parse_titles: Boolean param to set whether to parse titles in author names. By default, this is
            turned off because most modern records do not include author titles, and the set of titles
            overlaps with some first names; recommended for older record parsing only. Default: False
        :return: list of parsed author dictionaries
        """

        if parse_titles:
            self.parse_titles = True
        full_collaborations_params = self.default_collaborations_params.copy()
        full_collaborations_params.update(collaborations_params)
        corrected_authors_list = []
        author_str = self._clean_author_name(author_str)
        # Check for collaboration strings
        is_collaboration, collaboration_list = self._extract_collaboration(
            author_str, default_to_last_name, full_collaborations_params
        )
        if is_collaboration:
            # Collaboration strings can contain the first author, which we need to split
            for corrected_author in collaboration_list:
                corrected_authors_list.append(corrected_author)
        else:
            corrected_authors_list.append(
                self._parse_author_name(author_str, default_to_last_name)
            )

        # Last minute global corrections due to manually detected problems in
        # our processing corrected_authors_str =
        # corrected_authors_str.replace(' ,', ',').replace('  ', ' ').
        # replace('. -', '.-')
        # TODO do I need these? make a cleaning func if so
        # corrected_authors_str = corrected_authors_str.replace(', , ', ', ')
        # corrected_authors_str = corrected_authors_str.replace(' -', '-').replace(' ~', '~')

        return corrected_authors_list
