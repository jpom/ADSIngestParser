import logging
import re
from collections import OrderedDict
from copy import copy

import bs4
import validators
from ordered_set import OrderedSet

from adsingestp import utils
from adsingestp.ingest_exceptions import XmlLoadException
from adsingestp.parsers.base import BaseBeautifulSoupParser

logger = logging.getLogger(__name__)


class JATSAffils(object):
    regex_email = re.compile(r"^[a-zA-Z0-9+_.-]+@[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+")
    regex_auth_xid = re.compile(r"^A[0-9]+$")

    def __init__(self):
        self.contrib_dict = {}
        self.collab = {}
        self.xref_dict = OrderedDict()
        self.xref_xid_dict = OrderedDict()
        self.email_xref = OrderedDict()
        self.output = None

    def _decompose(self, soup=None, tag=None):
        """
        Remove all instances of a tag and its contents from the BeautifulSoup tree
        :param soup: BeautifulSoup object/tree
        :param tag: tag in the BS tree to remove
        :return: BeautifulSoup object/tree
        """
        for element in soup(tag):
            element.decompose()

        return soup

    def _get_inst_identifiers(self, aff):
        """
        Takes a single affiliation in soup form, removes any institution-ids from the text, places them in the aff_id array, and returns the soup sans ids and the aff_id array
        :param aff: BeautifulSoup object/tree
        :return: BeautifulSoup object/tree, aff_ids array
        """
        aff_ids = []
        aff_external_ids = aff.find_all("institution-id", [])
        for ident in aff_external_ids:
            idtype = ident.get("institution-id-type", "")
            idvalue = ident.get_text()
            aff_ids.append({idtype: idvalue})
            ident.decompose()
        if not aff_external_ids:
            aff_ids.append({})
        return aff, aff_ids

    def _remove_unbalanced_parentheses(self, affstr):
        # Stack to track balanced parentheses
        stack = []
        to_remove = set()

        for i, char in enumerate(affstr):
            # Track the index of opening parentheses
            if char == "(":
                stack.append(i)
            elif char == ")":
                if stack:
                    # Pop if there's a matching opening parenthesis
                    stack.pop()
                else:
                    # Mark unbalanced closing parenthesis
                    to_remove.add(i)

        # Mark remaining unbalanced opening parentheses
        to_remove.update(stack)

        # Create a new string without the unbalanced parentheses
        new_affstr = "".join([char for i, char in enumerate(affstr) if i not in to_remove])

        return new_affstr

    def _fix_affil(self, affstring):
        """
        Separate email addresses from affiliations in a given input affiliation string
        :param affstring: Raw affiliation string
        :return: newaffstr: affiliation string with email addresses removed
                 emails: list of email addresses
        """
        aff_list = affstring.split(";")
        new_aff = []
        emails = []
        for a in aff_list:
            a = a.strip()
            # check for empty strings with commas
            check_a = a.replace(",", "")
            if check_a:
                a = re.sub("\\(e-*mail:\\s*,+\\s*\\)", "", a)
                a = a.replace("\\n", ",")
                a = a.replace(" —", "—")
                a = a.replace(" , ", ", ")
                a = a.replace(", .", ".")
                a = re.sub(",+", ",", a)
                a = re.sub("\\s+", " ", a)
                a = re.sub("^(\\s*,+\\s*)+", "", a)
                a = re.sub("(\\s*,\\s+)+", ", ", a)
                a = re.sub("(,\\s*)+$", "", a)
                a = re.sub("\\s+$", "", a)
                if self.regex_email.match(a):
                    emails.append(a)
                else:
                    if a:
                        a = self._remove_unbalanced_parentheses(a)
                        new_aff.append(a)

        newaffstr = "; ".join(new_aff)
        return newaffstr, emails

    def _fix_email(self, email):
        """
        Separate and perform basic validation of email address string
        :param email: List of email address(es)
        :return: list of verified email addresses (those that match the regex)
        """
        email_new = OrderedSet()

        email_format = re.compile(r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)")
        email_parsed = False
        for em in email:
            if " " in em:
                for e in em.strip().split():
                    try:
                        if email_format.search(e):
                            email_new.add(email_format.search(e).group(0))
                            email_parsed = True
                    except Exception as err:
                        logger.warning("Bad format in _fix_email: %s" % err)
            else:
                try:
                    if type(em) == str:
                        if email_format.search(em):
                            email_new.add(email_format.search(em).group(0))
                            email_parsed = True
                    elif type(em) == list:
                        for e in em:
                            if email_format.search(e):
                                email_new.add(email_format.search(e).group(0))
                                email_parsed = True
                except Exception as err:
                    logger.warning("Bad format in _fix_email: %s" % err)

        if not email_parsed:
            logger.warning("Email not verified as valid. Input email list: %s", str(email))

        return list(email_new)

    def _fix_orcid(self, orcid):
        """
        Standarize ORCID formatting
        :param orcid: string or list of ORCIDs
        :return: uniqued list of ORCIDs, with URL-part removed if necessary
        """
        orcid_new = OrderedSet()
        if isinstance(orcid, str):
            orcid = [orcid]
        elif not isinstance(orcid, list):
            raise TypeError("ORCID must be str or list")

        orcid_format = re.compile(r"(\d{4}-){3}\d{3}(\d|X)")
        for orc in orcid:
            osplit = orc.strip().split()
            for o in osplit:
                # ORCID IDs sometimes have the URL prepended - remove it
                if orcid_format.search(o):
                    orcid_new.add(orcid_format.search(o).group(0))
        return list(orcid_new)

    def _reformat_affids(self):
        for contribs in self.contrib_dict.values():
            for auth in contribs:
                if auth.get("affid", None) == [[{}]]:
                    del auth["affid"]
                # Initialize affid if not present
                if not auth.get("affid"):
                    auth["affid"] = []
                # Process existing affids
                if auth["affid"]:
                    affid_tmp = []
                    for ids in auth.get("affid", None):
                        ids_tmp = []
                        for d in ids:
                            for k, v in d.items():
                                ids_tmp.append({"affIDType": k, "affID": v})
                        affid_tmp.append((ids_tmp))
                    if affid_tmp:
                        auth["affid"] = affid_tmp
                    else:
                        del auth["affid"]

    def _match_xref_clean(self):
        """
        Matches crossreferenced affiliations and emails; cleans emails and ORCIDs
        :return: none (updates class variable auth_list)
        """
        for contrib_type, contribs in self.contrib_dict.items():
            for auth in contribs:
                # contents of xaff field aren't always properly separated - fix that here
                xaff_list = []
                for item in auth.get("xaff", []):
                    xi = re.split("\\s*,\\s*|\\s+", item)
                    for x in xi:
                        xaff_list.append(x)

                    # if you found any emails in an affstring, add them
                    # to the email field
                    if item in self.email_xref:
                        auth["email"].append(self.email_xref[item])

                if auth.get("xref", None):
                    if not auth.get("affid"):
                        auth["affid"] = []

                xaff_xid_tmp = []
                for x in xaff_list:
                    try:
                        if self.xref_dict[x] not in auth["aff"]:
                            auth["aff"].append(self.xref_dict[x])
                    except KeyError as err:
                        logger.info("Key is missing from xaff. Missing key: %s", err)
                        pass
                    try:
                        if self.xref_xid_dict[x]:
                            xaff_xid_tmp.append(self.xref_xid_dict[x])
                        else:
                            xaff_xid_tmp.append([{}])
                    except KeyError as err:
                        logger.info("Key is missing from xaff. Missing key: %s" % err)
                if xaff_xid_tmp:
                    auth["affid"] = xaff_xid_tmp
                if not auth.get("affid", None) or not xaff_xid_tmp:
                    auth["affid"] = []

                # Check for 'ALLAUTH'/'ALLCONTRIB' affils (global affils without a key), and assign them to all authors/contributors
                if contrib_type == "authors" and "ALLAUTH" in self.xref_dict:
                    auth["aff"].append(self.xref_dict["ALLAUTH"])
                if contrib_type == "contributors" and "ALLCONTRIB" in self.xref_dict:
                    auth["aff"].append(self.xref_dict["ALLCONTRIB"])

                for item in auth.get("xemail", []):
                    try:
                        auth["email"].append(self.xref_dict[item])
                    except KeyError as err:
                        logger.info("Missing key in xemail! Error: %s", err)
                        pass

                if auth.get("email", []):
                    auth["email"] = self._fix_email(auth["email"])

                if auth.get("orcid", []):
                    try:
                        auth["orcid"] = self._fix_orcid(auth["orcid"])
                    except TypeError:
                        logger.warning(
                            "ORCID of wrong type (not str or list) passed, removing. ORCID: %s",
                            auth["orcid"],
                        )
                        auth["orcid"] = []

                # note that the ingest schema allows a single email address,
                # but we've extracted all here in case that changes to allow
                #  more than one
                if auth.get("email", []):
                    auth["email"] = auth["email"][0]
                else:
                    auth["email"] = ""

                # same for orcid
                if auth.get("orcid", []):
                    auth["orcid"] = auth["orcid"][0]
                else:
                    auth["orcid"] = ""

    def parse(self, article_metadata):
        """
        Parses author affiliation from BeautifulSoup object
        :param article_metadata: BeautifulSoup object containing author nodes
        :return: auth_list: list of dicts, one per author
        """
        article_metadata = self._decompose(soup=article_metadata, tag="label")

        art_contrib_groups = []
        if article_metadata.find("contrib-group"):
            art_contrib_groups = article_metadata.find_all("contrib-group")

        authors_out = []
        contribs_out = []

        # JATS puts author data in <contrib-group>, giving individual authors in each <contrib>
        for art_group in art_contrib_groups:
            art_contrib_group = art_group.extract()

            contribs_raw = art_contrib_group.find_all("contrib", recursive=False)

            default_key = "ALLAUTH"

            num_contribs = len(contribs_raw)

            # extract <contrib> from each <contrib-group>
            for idx, contrib in enumerate(contribs_raw):
                # note: IOP, APS get affil data within each contrib block,
                #       OUP, AIP, Springer, etc get them via xrefs.
                auth = {}
                # cycle through <contrib> to check if a <collab> is listed in the same level as an author an has multiple authors nested under it;
                # targeted for Springer

                if contrib.find("collab") or contrib.find("collab-name"):
                    # Springer collab info for nested authors is given as <institution>
                    if contrib.find("collab"):
                        if contrib.find("collab").find("institution"):
                            collab = contrib.find("collab").find("institution")
                        else:
                            collab = contrib.find("collab")
                    else:
                        collab = contrib.find("collab-name")

                    # This is checking if a collaboration is listed as an author
                    if collab:
                        if type(collab.contents[0].get_text()) == str:
                            collab_name = collab.contents[0].get_text().strip()
                        else:
                            collab_name = collab.get_text().strip()

                        if collab.find("address"):
                            collab_affil = collab.find("address").get_text()
                        else:
                            collab_affil = []

                        self.collab = {
                            "collab": collab_name,
                            "aff": collab_affil,
                            "affid": [],
                            "xaff": [],
                            "xemail": [],
                            "email": [],
                            "corresp": False,
                            "rid": None,
                            "surname": "",
                            "given": "",
                            "prefix": "",
                            "suffix": "",
                            "native_lang": "",
                            "orcid": "",
                        }

                    if self.collab:
                        # add collab in the correct author position
                        if self.collab not in authors_out:
                            authors_out.append(self.collab)

                    # find nested collab authors and unnest them
                    collab_contribs = collab.find_all("contrib")
                    nested_contribs = []
                    for ncontrib in collab_contribs:
                        if ncontrib:
                            nested_contribs.append(copy(ncontrib))
                            ncontrib.decompose()

                    if not nested_contribs:
                        nested_contribs = contrib.find_all("contrib")

                    nested_idx = idx + 1
                    for nested_contrib in nested_contribs:
                        if "rid" in nested_contrib.attrs:
                            rid_match = next(
                                (
                                    (rid_ndx, author)
                                    for rid_ndx, author in enumerate(authors_out)
                                    if author.get("rid") == nested_contrib["rid"]
                                ),
                                None,
                            )
                            if rid_match:
                                author_tmp = rid_match[1]
                                if contrib.find("collab").find("institution", None):
                                    author_tmp["collab"] = (
                                        contrib.find("collab").find("institution").get_text()
                                    )
                                    authors_out[rid_match[0]] = author_tmp
                        else:
                            # add new collab tag to each unnested author
                            if contrib.find("collab") and contrib.find("collab").find(
                                "institution"
                            ):
                                collab_text = (
                                    contrib.find("collab").find("institution").decode_contents()
                                )
                            elif collab_name:
                                collab_text = collab_name
                            else:
                                collab_text = None
                            if collab_text:
                                collabtag_string = "<collab>" + collab_text + "</collab>"
                                collabtag = bs4.BeautifulSoup(collabtag_string, "xml").collab

                            if not collabtag:
                                collabtag = "ALLAUTH"

                            if collabtag:
                                nested_contrib.insert(0, collabtag)
                                contribs_raw.insert(nested_idx, nested_contrib.extract())
                                nested_idx += 1

                # check if collabtag is present in the author author attributes
                collab = contrib.find("collab")

                if collab:
                    if type(collab.contents[0].get_text()) == str:
                        collab_name = collab.contents[0].get_text().strip()
                    else:
                        collab_name = collab.get_text().strip()

                    if collab.find("address"):
                        collab_affil = collab.find("address").get_text()
                    else:
                        collab_affil = ""

                    if not self.collab:
                        self.collab = {
                            "collab": collab_name,
                            "aff": collab_affil,
                            "affid": [],
                            "xaff": [],
                            "xemail": [],
                            "email": [],
                            "corresp": False,
                            "rid": None,
                            "surname": "",
                            "given": "",
                            "prefix": "",
                            "suffix": "",
                            "native_lang": "",
                            "orcid": "",
                        }

                l_correspondent = False
                if contrib.get("corresp", None) == "yes":
                    l_correspondent = True

                # get author's name
                if contrib.find("name") and contrib.find("name").find("surname"):
                    surname = contrib.find("name").find("surname").get_text()
                elif contrib.find("string-name") and contrib.find("string-name").find("surname"):
                    surname = contrib.find("string-name").find("surname").get_text()
                else:
                    surname = ""

                if contrib.find("name") and contrib.find("name").find("given-names"):
                    given = contrib.find("name").find("given-names").get_text()
                elif contrib.find("string-name") and contrib.find("string-name").find(
                    "given-names"
                ):
                    given = contrib.find("string-name").find("given-names").get_text()
                else:
                    given = ""

                if contrib.find("name") and contrib.find("name").find("suffix"):
                    suffix = contrib.find("name").find("suffix").get_text()
                elif contrib.find("string-name") and contrib.find("string-name").find("suffix"):
                    suffix = contrib.find("string-name").find("suffix").get_text()
                else:
                    suffix = ""

                if contrib.find("name") and contrib.find("name").find("prefix"):
                    prefix = contrib.find("name").find("prefix").get_text()
                elif contrib.find("string-name") and contrib.find("string-name").find("prefix"):
                    prefix = contrib.find("string-name").find("prefix").get_text()
                else:
                    prefix = ""

                # get native language author name
                if contrib.find("name-alternatives"):
                    if contrib.find("name-alternatives").find("string-name"):
                        if (
                            contrib.find("name-alternatives")
                            .find("string-name")
                            .get("name-style", "")
                            != "western"
                        ):
                            native_lang = (
                                contrib.find("name-alternatives")
                                .find("string-name")
                                .get_text()
                                .strip()
                            )
                        else:
                            native_lang = ""
                    else:
                        native_lang = contrib.find("name-alternatives").get_text().strip()
                else:
                    native_lang = ""

                # NOTE: institution-id is actually useful, but at
                # at the moment, strip it
                # contrib = self._decompose(soup=contrib, tag="institution-id")

                # get named affiliations within the contrib block
                affs = contrib.find_all("aff")
                aff_text = []
                email_list = []
                aff_extids = []
                for i in affs:
                    if not i.get("specific-use", None):
                        # special case: some pubs label affils with <sup>label</sup>, strip them
                        i = self._decompose(soup=i, tag="sup")
                        i, aff_extids_tmp = self._get_inst_identifiers(i)
                        affstr = i.get_text(separator=", ").strip()
                        (affstr, email_list) = self._fix_affil(affstr)
                        aff_text.append(affstr)
                        aff_extids.extend(aff_extids_tmp)
                        i.decompose()
                    else:
                        i.decompose()

                # special case (e.g. AIP) - one author per contrib group, aff stored at contrib group level
                if num_contribs == 1 and art_contrib_group.find("aff"):
                    aff_list = art_contrib_group.find_all("aff")
                    if aff_list:
                        for aff in aff_list:
                            aff, aff_extids_tmp = self._get_inst_identifiers(aff)
                            aff_fix = aff.get_text(separator=", ").strip()
                            (affstr, email_fix) = self._fix_affil(aff_fix)
                            email_list.extend(email_fix)
                            aff_text.append(affstr)
                            aff_extids.extend(aff_extids_tmp)
                            aff.decompose()

                # get xrefs...
                xrefs = contrib.find_all("xref")
                xref_aff = []
                xref_email = []
                for x in xrefs:
                    if x.get("ref-type", "") == "aff":
                        xref_aff.append(x["rid"])
                    elif x.get("ref-type", "") == "corresp":
                        xref_email.append(x["rid"])
                    x.decompose()

                # get email(s)...
                # we already have raw emails stripped out of affil strings above, add to this from contrib block
                email_contrib = contrib.find_all("email")
                for e in email_contrib:
                    email_list.append(e.get_text(separator=" ").strip())
                    e.decompose()

                # get orcid
                contrib_id = contrib.find_all("contrib-id")
                orcid = []
                for c in contrib_id:
                    if (c.get("contrib-id-type", "") == "orcid") or ("orcid" in c.get_text()):
                        orcid.append(c.get_text(separator=" ").strip())
                    c.decompose()

                # double-check for orcid in other places...
                extlinks = contrib.find_all("ext-link")
                for e in extlinks:
                    # orcid
                    if e.get("ext-link-type", "") == "orcid":
                        orcid.append(e.get_text(separator=" ").strip())
                    e.decompose()

                # note that the ingest schema allows a single orcid, but we've extracted all
                # here in case that changes to allow more than one
                if orcid:
                    orcid_out = self._fix_orcid(orcid)
                    if orcid_out:
                        orcid_out = orcid_out[0]
                    else:
                        orcid_out = ""
                else:
                    orcid_out = ""

                # create the author dict
                auth["corresp"] = l_correspondent
                auth["surname"] = surname
                auth["given"] = given
                auth["suffix"] = suffix
                auth["prefix"] = prefix
                auth["native_lang"] = native_lang
                auth["aff"] = aff_text
                auth["affid"] = aff_extids
                auth["xaff"] = xref_aff
                auth["xemail"] = xref_email
                auth["orcid"] = orcid_out
                auth["email"] = email_list
                auth["rid"] = contrib.get("id", None)

                # this is a list of author dicts
                if auth:
                    if collab:
                        auth["collab"] = collab_name

                    # Check if author is a duplicate of a collaboration
                    if auth.get("surname", "") == "" and auth.get("collab", ""):
                        # delete email and correspondence info for collabs
                        auth["email"] = []
                        auth["xemail"] = []
                        auth["corresp"] = False
                        # if the collab is already in author list, skip
                        if auth in authors_out:
                            continue

                    if contrib.get("contrib-type", "author") == "author":
                        authors_out.append(auth)
                        default_key = "ALLAUTH"
                    else:
                        if contrib.find("role"):
                            role = contrib.find("role").get_text()
                        else:
                            role = contrib.get("contrib-type", "contributor")
                        auth["role"] = role
                        contribs_out.append(auth)
                        default_key = "ALLCONTRIB"
                contrib.decompose()

            if self.collab:
                if self.collab not in authors_out:
                    authors_out.append(self.collab)

            # special case: affs defined in contrib-group, but not in individual contrib
            if art_contrib_group:
                contrib_aff = art_contrib_group.find_all("aff")
                contrib_aff_new = []
                for a in contrib_aff:
                    if not a.get("specific-use", None):
                        contrib_aff_new.append(a)
                contrib_aff = contrib_aff_new
                for aff in contrib_aff:
                    # check and see if the publisher defined an email tag inside an affil (like IOP does)
                    nested_email_list = aff.find_all("ext-link")
                    key = aff.get("id", default_key)
                    for e in nested_email_list:
                        if e.get("ext-link-type", None) == "email":
                            if e.get("id", None):
                                ekey = e["id"]
                            else:
                                ekey = key
                            value = e.text
                            # build the cross-reference dictionary to be used later
                            self.email_xref[ekey] = value
                            e.decompose()

                    # special case: get rid of <sup>...
                    aff = self._decompose(soup=aff, tag="sup")
                    aff, aff_extids_tmp = self._get_inst_identifiers(aff)

                    # getting rid of ext-link eliminates *all* emails,
                    # so this is not the place to fix the iop thing
                    # a = self._decompose(soup=a, tag='ext-link')

                    affstr = aff.get_text(separator=", ").strip()
                    (affstr, email_list) = self._fix_affil(affstr)
                    if not self.email_xref.get(key, None):
                        if email_list:
                            self.email_xref[key] = email_list
                        else:
                            self.email_xref[key] = ""
                    self.xref_dict[key] = affstr
                    self.xref_xid_dict[key] = aff_extids_tmp

        # special case: publisher defined aff/email xrefs, but the xids aren't
        # assigned to authors; xid is typically of the form "A\d+"
        # publisher example: Geol. Soc. London (gsl)
        count_auth = len(authors_out)
        count_xref = len(self.xref_dict.keys())
        if count_auth == count_xref:
            for auth, xref in zip(authors_out, self.xref_dict.keys()):
                if self.regex_auth_xid.match(xref):
                    if not auth.get("aff", []) and not auth.get("xaff", []):
                        auth["xaff"] = [xref]

        self.contrib_dict = {"authors": authors_out, "contributors": contribs_out}

        # now get the xref keys outside of contrib-group:
        # aff xrefs...
        aff_glob = article_metadata.find_all("aff")
        aff_glob_new = []
        for a in aff_glob:
            if not a.get("specific-use", None):
                aff_glob_new.append(a)
        aff_glob = aff_glob_new
        for aff in aff_glob:
            try:
                key = aff["id"]
            except KeyError:
                logger.info("No aff id key in: %s", aff)
                continue
            # special case: get rid of <sup>...
            aff = self._decompose(soup=aff, tag="sup")

            aff, aff_extids_tmp = self._get_inst_identifiers(aff)
            affstr = aff.get_text(separator=", ").strip()
            (aff_list, email_list) = self._fix_affil(affstr)
            self.xref_dict[key] = aff_list
            if self.xref_xid_dict.get(key, None):
                self.xref_xid_dict[key].extend(aff_extids_tmp)
            else:
                self.xref_xid_dict[key] = aff_extids_tmp
            aff.decompose()

        # author-notes xrefs...
        authnote_glob = article_metadata.find_all("author-notes")
        for aff in authnote_glob:
            # emails...
            cor = aff.find_all("corresp")
            for c in cor:
                try:
                    key = c["id"]
                except KeyError:
                    logger.info("No authnote id key in: %s", aff)
                    continue
                c = self._decompose(soup=c, tag="sup")
                val = c.get_text(separator=" ").strip()
                self.xref_dict[key] = val
                c.decompose()

        # finishing up
        self._match_xref_clean()
        self._reformat_affids()

        return self.contrib_dict


class JATSParser(BaseBeautifulSoupParser):
    def __init__(self):
        super(BaseBeautifulSoupParser, self).__init__()
        self.base_metadata = {}
        self.back_meta = None
        self.article_meta = None
        self.journal_meta = None
        self.isErratum = False

    def _get_date(self, d):
        """
        Extract and standarize date from input BeautifulSoup date object
        :param d: BeautifulSoup date object
        :return: formatted date string (yyyy-mm-dd)
        """
        if d.find("year"):
            pubdate = self._detag(d.year, [])
        else:
            pubdate = "0000"

        if d.find("month"):
            month_raw = self._detag(d.month, [])
            if month_raw.isdigit():
                month = month_raw
            else:
                month_name = month_raw[0:3].lower()
                month = utils.MONTH_TO_NUMBER.get(month_name, 0)

            if int(month) < 10 and len(str(month)) < 2:
                month = "0" + str(int(month))
            else:
                month = str(month)
            pubdate = pubdate + "-" + month
        else:
            pubdate = pubdate + "-" + "00"

        if d.find("day"):
            day_raw = self._detag(d.day, [])
            if day_raw.isdigit():
                day = day_raw
            else:
                day = "00"

            if int(day) < 10 and len(str(day)) < 2:
                day = "0" + str(int(day))
            else:
                day = str(day)
            pubdate = pubdate + "-" + day
        else:
            pubdate = pubdate + "-" + "00"

        return pubdate

    def _parse_title_abstract(self):
        title_fn_dict = {}
        title_fn_list = []
        subtitle_fn_list = []
        self.titledoi = None
        title_group = self.article_meta.find("title-group")
        art_title = None
        sub_title = None
        if title_group:
            if title_group.find("article-title"):
                title = title_group.find("article-title")
                if not title or not title.get_text():
                    title = title_group.find("alt-title")
                for dx in title.find_all("ext-link"):
                    if dx.find("xlink:href"):
                        self.titledoi = dx.find("xlink:href")
                # all title footnotes:
                for df in title_group.find_all("fn"):
                    key = df.get("id", None)
                    df = self._remove_latex(df)
                    note = self._detag(df, self.HTML_TAGSET["abstract"]).strip()
                    if key and note:
                        title_fn_dict[key] = note
                    df.decompose()
                # title xrefs
                for dx in title.find_all("xref"):
                    key = dx.get("rid", None)
                    if title_fn_dict.get(key, None):
                        title_fn_list.append(title_fn_dict.get(key, None))
                    dx.decompose()
                # strip latex out of title
                title = self._remove_latex(title)
                art_title = self._detag(title, self.HTML_TAGSET["title"]).strip()

                title_notes = []
                if title_fn_list:
                    title_notes.extend(title_fn_list)

                if title_group.find("subtitle"):
                    subtitle = title_group.find("subtitle")
                    if subtitle.get("content-type", "") != "running-title":
                        # subtitle xrefs
                        for dx in subtitle.find_all("xref"):
                            key = dx.get("rid", None)
                            if title_fn_dict.get(key, None):
                                subtitle_fn_list.append(title_fn_dict.get(key, None))
                            dx.decompose()
                        subtitle = self._remove_latex(subtitle)
                        sub_title = self._detag(subtitle, self.HTML_TAGSET["title"]).strip()
                subtitle_notes = []
                if subtitle_fn_list:
                    subtitle_notes.extend(subtitle_fn_list)
            if art_title:
                self.base_metadata["title"] = art_title
                if title_notes:
                    self.base_metadata["title_notes"] = title_notes
                if sub_title:
                    self.base_metadata["subtitle"] = sub_title
                if subtitle_notes:
                    self.base_metadata["subtitle_notes"] = subtitle_notes

        if self.article_meta.find("abstract"):
            if self.article_meta.find("abstract").find("p"):
                abstract_all = self.article_meta.find("abstract").find_all("p")
                abstract_paragraph_list = list()
                for paragraph in abstract_all:
                    paragraph = self._remove_latex(paragraph)
                    para = self._detag(paragraph, self.HTML_TAGSET["abstract"])
                    abstract_paragraph_list.append(para)
                self.base_metadata["abstract"] = "\n".join(abstract_paragraph_list)
                if title_fn_list:
                    self.base_metadata["abstract"] += "  " + " ".join(title_fn_list)
            else:
                abs_raw = self.article_meta.find("abstract")
                abs_raw = self._remove_latex(abs_raw)
                abs_txt = self._detag(abs_raw, self.HTML_TAGSET["abstract"])
                self.base_metadata["abstract"] = abs_txt

    def _parse_author(self):
        auth_affil = JATSAffils()
        aa_output_dict = auth_affil.parse(article_metadata=self.article_meta)
        if aa_output_dict.get("authors"):
            for auth in aa_output_dict["authors"]:
                if auth.get("given"):
                    auth["given"] = " ".join(auth["given"].split())
                if auth.get("surname"):
                    auth["surname"] = " ".join(auth["surname"].split())
                if auth.get("middle"):
                    auth["middle"] = " ".join(auth["middle"].split())

            self.base_metadata["authors"] = aa_output_dict["authors"]

        if aa_output_dict.get("contributors"):
            self.base_metadata["contributors"] = aa_output_dict["contributors"]

    def _parse_copyright(self):
        if self.article_meta.find("copyright-statement"):
            copyright = self._detag(self.article_meta.find("copyright-statement"), [])
        else:
            copyright = "&copy;"
            if self.article_meta.find("copyright-year"):
                copyright = (
                    copyright + " " + self._detag(self.article_meta.find("copyright-year"), [])
                )
            if self.article_meta.find("copyright-holder"):
                copyright = (
                    copyright + " " + self._detag(self.article_meta.find("copyright-holder"), [])
                )
            if copyright == "&copy;":
                # not copyright info found
                copyright = None

        if copyright:
            self.base_metadata["copyright"] = copyright

    def _parse_edhistory(self):
        # received and revised dates can be arrays, but accepted can just be a single value
        received = []
        revised = []

        if self.article_meta.find("history"):
            dates = self.article_meta.find("history").find_all("date")
            for d in dates:
                date_type = d.get("date-type", "")
                eddate = self._get_date(d)
                if date_type == "received":
                    received.append(eddate)
                elif date_type == "rev-recd":
                    revised.append(eddate)
                elif date_type == "accepted":
                    self.base_metadata["edhist_acc"] = eddate
                # special case: if "version-of-record" add to pubDate.otherDate
                elif date_type == "version-of-record":
                    pd = {"type": date_type, "date": eddate}
                    # self.base_metadata["pubdate_other"]
                    if self.base_metadata.get("pubdate_other", []):
                        self.base_metadata["pubdate_other"].append(pd)
                    else:
                        self.base_metadata["pubdate_other"] = [pd]
                else:
                    logger.info("Editorial history date type (%s) not recognized.", date_type)

            self.base_metadata["edhist_rec"] = received
            self.base_metadata["edhist_rev"] = revised

    def _parse_keywords(self):
        keys_uat = []
        keys_misc = []
        keys_aas = []
        keys_out = []
        keyword_groups = self.article_meta.find_all("kwd-group")

        for kg in keyword_groups:
            if kg.get("kwd-group-type", "") == "author":
                keywords_uat_test = kg.find_all("compound-kwd")
                for kw in keywords_uat_test:
                    keys_uat_test = kw.find_all("compound-kwd-part")
                    for kk in keys_uat_test:
                        # Check for UAT first:
                        if kk["content-type"] == "uat-code":
                            kk = self._remove_latex(kk)
                            keyid = self._detag(kk, self.HTML_TAGSET["keywords"])
                        if kk["content-type"] == "term":
                            kk = self._remove_latex(kk)
                            keystring = self._detag(kk, self.HTML_TAGSET["keywords"])

                    if keyid or keystring:
                        keys_uat.append({"string": keystring, "system": "UAT", "id": keyid})

                    if not keys_uat:
                        keys_misc_test = kg.find_all("kwd")
                        for kk in keys_misc_test:
                            kk = self._remove_latex(kk)
                            keys_misc.append(self._detag(kk, self.HTML_TAGSET["keywords"]))

            # Then check for AAS:
            if kg.get("kwd-group-type", "") == "AAS":
                keys_aas_test = kg.find_all("kwd")
                for kk in keys_aas_test:
                    kk = self._remove_latex(kk)
                    keys_aas.append(self._detag(kk, self.HTML_TAGSET["keywords"]))

            # If all else fails, just search for 'kwd'
            if (not keys_uat) and (not keys_aas):
                keys_misc_test = kg.find_all("kwd")
                for kk in keys_misc_test:
                    kk = self._remove_latex(kk)
                    keys_misc.append(self._detag(kk, self.HTML_TAGSET["keywords"]))

        if keys_uat:
            for k in keys_uat:
                keys_out.append(k)

        if keys_aas:
            for k in keys_aas:
                keys_out.append({"system": "AAS", "string": k})

        if keys_misc:
            for k in keys_misc:
                keys_out.append({"system": "misc", "string": k})

        if keys_out:
            self.base_metadata["keywords"] = keys_out

        if "keywords" not in self.base_metadata:
            if self.article_meta.find("article-categories"):
                subj_groups = self.article_meta.find("article-categories").find_all("subj-group")
            else:
                subj_groups = []
            for sg in subj_groups:
                subjects = []
                if sg.get("subj-group-type", "") in ["toc-minor", "toc-heading"]:
                    subjects = sg.find_all("subject")

                for k in subjects:
                    k = self._remove_latex(k)
                    key = self._detag(k, self.HTML_TAGSET["keywords"])
                    # AIP special handling
                    if key not in utils.AIP_DISCARD_KEYWORDS:
                        keys_out.append(
                            {
                                "system": "subject",
                                "string": key,
                            }
                        )

                for k in sg.find_all("subject"):
                    if k.string == "Errata" or k.string == "Corrigendum":
                        self.isErratum = True

            self.base_metadata["keywords"] = keys_out

    def _parse_conference(self):
        event_meta = self.article_meta.find("conference")

        if event_meta.find("conf-name"):
            conf_name = self._remove_latex(event_meta.find("conf-name", ""))
            self.base_metadata["conf_name"] = self._detag(conf_name, [])

        if event_meta.find("conf-loc"):
            conf_loc = self._remove_latex(event_meta.find("conf-loc", ""))
            self.base_metadata["conf_location"] = self._detag(conf_loc, [])

        if event_meta.find("conf-date"):
            conf_date = self._remove_latex(event_meta.find("conf-date", ""))
            self.base_metadata["conf_date"] = self._detag(conf_date, [])

    def _parse_pub(self):
        journal = None
        if self.journal_meta.find("journal-title-group") and self.journal_meta.find(
            "journal-title-group"
        ).find("journal-title"):
            journal = self.journal_meta.find("journal-title-group").find("journal-title")
        elif self.journal_meta.find("journal-title-group") and self.journal_meta.find(
            "journal-title-group"
        ).find("abbrev-journal-title"):
            if self.journal_meta.find("journal-title-group").find(
                "abbrev-journal-title", {"abbrev-type": "pubmed"}
            ):
                journal = self.journal_meta.find("journal-title-group").find(
                    "abbrev-journal-title", {"abbrev-type": "pubmed"}
                )
            else:
                journal = self.journal_meta.find("journal-title-group").find(
                    "abbrev-journal-title"
                )
        elif self.journal_meta.find("journal-title"):
            journal = self.journal_meta.find("journal-title")

        if journal:
            journal = self._remove_latex(journal)
            self.base_metadata["publication"] = self._detag(journal, [])

        if self.journal_meta.find("publisher") and self.journal_meta.find("publisher").find(
            "publisher-name"
        ):
            publisher_name = self._remove_latex(
                self.journal_meta.find("publisher").find("publisher-name")
            )
            self.base_metadata["publisher"] = self._detag(publisher_name, [])

        issn_all = self.journal_meta.find_all("issn")
        issns = []
        for i in issn_all:
            issns.append((i.get("pub-type", ""), self._detag(i, [])))
        self.base_metadata["issn"] = issns

        isbn_all = self.article_meta.find_all("isbn")
        isbns = []
        for i in isbn_all:
            content_type = None
            if i.get("content-type", ""):
                content_type = i.get("content-type")
            elif i.get("publication-format", ""):
                content_type = i.get("publication-format")
            isbns.append({"type": content_type, "isbn_str": self._detag(i, [])})

        self.base_metadata["isbn"] = isbns

    def _parse_related(self):
        # Related article data, especially corrections and errata

        related = self.article_meta.find_all("related-article")
        relateddoi = ""
        for r in related:
            # TODO are there other types of related articles to track? need an example
            if r.get("related-article-type", "") == "corrected-article":
                self.isErratum = True
                relateddoi = r.get("xlink:href", "")

        if self.isErratum:
            doiurl_pat = r"(.*?)(doi.org\/)"
            if self.titledoi:
                self.base_metadata["relatedto"] = [
                    {
                        "relationship": "errata",
                        "id": re.sub(doiurl_pat, "", self.titledoi),
                    }
                ]
            elif relateddoi:
                self.base_metadata["relatedto"] = [
                    {
                        "relationship": "errata",
                        "id": re.sub(doiurl_pat, "", relateddoi),
                    }
                ]
            else:
                logger.warning("No DOI for erratum: %s", related)

    def _parse_ids(self):
        self.base_metadata["ids"] = {}

        ids = self.article_meta.find_all("article-id")

        self.base_metadata["ids"]["pub-id"] = []
        for d in ids:
            id_type = d.get("pub-id-type", "")
            # DOI
            if id_type == "doi":
                self.base_metadata["ids"]["doi"] = self._detag(d, [])

            # publisher ID
            if id_type == "publisher-id":
                self.base_metadata["ids"]["pub-id"].append(
                    {"attribute": "publisher-id", "Identifier": self._detag(d, [])}
                )
            elif id_type == "manuscript":
                self.base_metadata["ids"]["pub-id"].append(
                    {"attribute": "manuscript", "Identifier": self._detag(d, [])}
                )
            elif id_type == "url":
                self.base_metadata["ids"]["pub-id"].append(
                    {"attribute": "url", "Identifier": self._detag(d, [])}
                )
            elif id_type == "other":
                self.base_metadata["ids"]["pub-id"].append(
                    {"attribute": "other", "Identifier": self._detag(d, [])}
                )

        # Arxiv Preprint
        arxiv = self.article_meta.find_all("custom-meta")
        # ax_pref = "https://arxiv.org/abs/"
        self.base_metadata["ids"]["preprint"] = {}
        for ax in arxiv:
            x_name = self._detag(ax.find("meta-name"), [])
            x_value = self._detag(ax.find("meta-value"), [])
            if x_name and x_name == "arxivppt":
                self.base_metadata["ids"]["preprint"] = {"source": "arxiv", "id": x_value}

    def _parse_pubdate(self):
        pub_dates = self.article_meta.find_all("pub-date")

        for d in pub_dates:
            d = d.extract()
            pub_format = d.get("publication-format", "")
            pub_type = d.get("pub-type", "")
            date_type = d.get("date-type", "")
            accepted_date_types = [
                "pub",
                "",
                "first_release",
                "epub-ppub",
                "ppub-epub",
                "version-of-record",
            ]
            pubdate = self._get_date(d)
            if (
                pub_format == "print"
                or pub_type == "ppub"
                or pub_type == "cover"
                or (pub_type == "" and pub_format == "")
            ) and (date_type == "pub" or date_type == "" or date_type == "version-of-record"):
                self.base_metadata["pubdate_print"] = pubdate

            if (
                pub_format == "electronic"
                or pub_type == "epub"
                or pub_type == "epub-ppub"
                or pub_type == "ppub-epub"
                or (pub_type == "" and pub_format == "")
            ) and (date_type in accepted_date_types):
                self.base_metadata["pubdate_electronic"] = pubdate

            elif (date_type != "pub") and (date_type != ""):
                # you need to check the next level to see if there's an
                # embedded version of record date-type
                if self.base_metadata.get("pubdate_other", []):
                    self.base_metadata["pubdate_other"].append(
                        {"type": date_type, "date": pubdate}
                    )
                else:
                    self.base_metadata["pubdate_other"] = [{"type": date_type, "date": pubdate}]
            if pub_type == "open-access":
                self.base_metadata.setdefault("openAccess", {}).setdefault("open", True)

    def _parse_permissions(self):
        # Check for open-access / "Permissions" field
        if self.article_meta.find("permissions"):
            permissions = self.article_meta.find("permissions").find_all("license")
            for p in permissions:
                if (
                    p.get("license-type", None) == "open"
                    or p.get("license-type", None) == "open-access"
                ):
                    self.base_metadata.setdefault("openAccess", {}).setdefault("open", True)
                if p.find("license-p"):
                    license_text = p.find("license-p")
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

    def _parse_page(self):
        fpage = self.article_meta.find("fpage")
        if not fpage:
            fpage = self.article_meta.find("pageStart")
        if fpage:
            self.base_metadata["page_first"] = self._detag(fpage, [])

        e_id = self.article_meta.find("elocation-id")
        if e_id:
            self.base_metadata["electronic_id"] = self._detag(e_id, [])

        lpage = self.article_meta.find("lpage")
        if not lpage:
            lpage = self.article_meta.find("pageEnd")
        if lpage == fpage:
            lpage = None
        if lpage:
            self.base_metadata["page_last"] = self._detag(lpage, [])

        if fpage and lpage:
            self.base_metadata["page_range"] = (
                self._detag(fpage, []) + "-" + (self._detag(lpage, []))
            )

        # Number of Pages:
        if self.article_meta.find("counts") and self.article_meta.find("counts").find(
            "page-count"
        ):
            pagecount = self.article_meta.find("counts").find("page-count").get("count", "")
            if str(pagecount).isdigit() and pagecount != "0":
                self.base_metadata["numpages"] = pagecount

    def _parse_references(self):
        if self.back_meta is not None:
            ref_list_text = []
            if self.back_meta.find("ref-list"):
                ref_results = self.back_meta.find("ref-list").find_all("ref")
            else:
                ref_results = []
            for r in ref_results:
                # output raw XML for reference service to parse later
                s = str(r.extract()).replace("\n", " ").replace("\xa0", " ")
                ref_list_text.append(s)
            self.base_metadata["references"] = ref_list_text

    def _parse_esources(self):
        links = []
        rawlinks = self.article_meta.find_all("self-uri")

        for link in rawlinks:
            if link.get("content-type", "") == "full_html":
                if validators.url(link.get("xlink:href", "")):
                    links.append(("pub_html", link.get("xlink:href", "")))

            if link.get("content-type", "") == "pdf":
                if validators.url(link.get("xlink:href", "")):
                    links.append(("pub_pdf", link.get("xlink:href", "")))

        # add a check to see if pub_html exists in links. if not, search for abstract link
        if "pub_html" not in dict(links).keys():
            for link in rawlinks:
                if link.get("content-type", "") == "abstract":
                    if validators.url(link.get("xlink:href", "")):
                        links.append(("pub_html", link.get("xlink:href", "")))

        self.base_metadata["esources"] = links

    def _parse_funding(self):
        funding = []
        funding_groups = self.article_meta.find_all("funding-group")
        for fg in funding_groups:
            award_groups = fg.find_all("award-group")
            for ag in award_groups:
                # with institution-wrap
                institution_tag = ag.find("institution")
                if institution_tag:
                    institution = institution_tag.get_text()
                else:
                    institution = None
                institution_id = ag.find("institution-id")
                if institution_id:
                    idschema = institution_id.get("institution-id-type", None)
                    idvalue = institution_id.get_text()
                else:
                    idschema = None
                    idvalue = None
                award_id_tag = ag.find("award-id")
                if award_id_tag:
                    award_id = award_id_tag.get_text()
                else:
                    award_id = None

                # without institution-wrap
                if not institution:
                    funding_source = ag.find("funding-source")
                    if funding_source:
                        named_content = funding_source.find("named-content")
                        if named_content:
                            idvalue = named_content.get_text()
                            if "doi" in idvalue:
                                idschema = "doi"
                            named_content.decompose()
                        institution = funding_source.get_text()
                        funding_source.decompose()

                funder = {}
                if institution:
                    funder.setdefault("agencyname", institution)
                if idschema or idvalue:
                    if idschema:
                        funder.setdefault("agencyid", {}).setdefault("idschema", idschema)
                    if idvalue:
                        funder.setdefault("agencyid", {}).setdefault("idvalue", idvalue)
                if award_id:
                    funder.setdefault("awardnumber", award_id)
                if funder:
                    funding.append(funder)
                ag.decompose()
            fg.decompose()
        self.base_metadata["funding"] = funding

    def parse(self, text, bsparser="lxml-xml"):
        """
        Parse JATS XML into standard JSON format
        :param text: string, contents of XML file
        :return: parsed file contents in JSON format
        """
        try:
            d = self.bsstrtodict(text, parser=bsparser)
        except Exception as err:
            raise XmlLoadException(err)

        document = getattr(d, "article", None) or getattr(d, "conf-article", None)
        if document is None:
            raise XmlLoadException("No <article> or <conf-article> element found")

        front_meta = getattr(document, "front", None) or getattr(document, "conf-front", None)
        if front_meta is None:
            raise XmlLoadException("No <front> or <conf-front> element found")

        self.back_meta = document.back

        # If a journal
        if front_meta.find("journal-meta"):
            self.journal_meta = front_meta.find("journal-meta")
        if front_meta.find("article-meta"):
            self.article_meta = front_meta.find("article-meta")

        # If a conference
        # IEEE JATS for conferences contains 2 container elements about the conference:
        # <conf-proc-meta> about the proceedings
        # <conf-meta> about the conference itself
        if front_meta.find("conf-proc-meta"):
            self.journal_meta = front_meta.find("conf-proc-meta")
        if front_meta.find("conf-meta"):
            confm = front_meta.find("conf-meta")
            for child in list(confm.children):
                self.journal_meta.append(child)
            # self.journal_meta = front_meta.find("conf-meta")
        if front_meta.find("conf-article-meta"):
            self.article_meta = front_meta.find("conf-article-meta")

        # parse individual pieces
        self._parse_title_abstract()
        self._parse_author()
        self._parse_copyright()
        self._parse_keywords()

        # Volume:
        volume = self.article_meta.volume
        if volume:
            self.base_metadata["volume"] = self._detag(volume, [])

        # Issue:
        issue = self.article_meta.issue
        if issue:
            self.base_metadata["issue"] = self._detag(issue, [])

        if self.article_meta.find("conference"):
            self._parse_conference()

        self._parse_pub()
        self._parse_related()
        self._parse_ids()
        self._parse_pubdate()
        self._parse_edhistory()
        self._parse_permissions()
        self._parse_page()
        self._parse_esources()
        self._parse_funding()

        self._parse_references()

        self.base_metadata = self._entity_convert(self.base_metadata)

        output = self.format(self.base_metadata, format="JATS")

        return output

    def add_fulltext(self):
        pass

    def citation_context(
        self,
        text,
        bsparser="lxml-xml",
        input_bibcode=None,
        num_char=500,
        resolve_refs=False,
        text_output=True,
    ):
        """
        For a given fulltext XML, find the paragraph(s) each reference is cited in. Returns a dictionary of the
        references (key) and an array of the paragraph(s) they're cited in (value). If resolve_refs is set to True, the
        keys are bibcodes, otherwise they're the internal ID of the reference

        :param text: text of fulltext XML to parse
        :param bsparser: parser to use with BeautifulSoup
        :param input_bibcode: string, bibcode of input XML, if known # TODO do I need this? do I need to resolve and return the paper's own bibcode?
        :param num_char: integer, check that citation paragraph is at least this long; if it's shorter, return the
            paragraphs before and after the citing paragraph as well
        :param resolve_refs: boolean, set to True to convert reference IDs to bibcodes # TODO this isn't fully implemented yet
        :param text_output: boolean, set to True to output citation context as a string, or False to output citation context as a raw XML string
        :return: dictionary: {"resolved": {bibcode1: [cite_context1, cite_context2, ...], ...},
                              "unresolved": {reference1: [cite_context1, cite_context2, ...], ...}}
                 where a reference appears in "resolved" if a bibcode has been found for it, and "unresolved" if not
        """
        try:
            d = self.bsstrtodict(text, parser=bsparser)
        except Exception as err:
            raise XmlLoadException(err)
        document = d.article

        self.back_meta = document.back
        body = document.body
        xrefs = body.find_all("xref", attrs={"ref-type": "bibr"})
        raw_cites = {}  # {rid_1: ["context 1", "context 2", ...]}
        for x in xrefs:
            id = x["rid"]
            immediate_para = x.find_parent("p")  # try to find the containing paragraph
            if immediate_para:
                context = immediate_para
                if text_output:
                    context = context.get_text()
                    if len(context) < num_char:
                        prev_para = immediate_para.find_previous_sibling("p")
                        if prev_para:
                            context = prev_para.get_text() + context
                        next_para = immediate_para.find_next_sibling("p")
                        if next_para:
                            context = context + next_para.get_text()
                else:
                    context = str(context)
            else:
                # reference not contained in a paragraph, so just get whatever context we have
                if text_output:
                    context = x.find_parent().get_text()
                else:
                    context = str(x.find_parent())
            if not context:
                context = "WARNING NO CONTEXT FOUND"
            if id in raw_cites.keys():
                raw_cites[id].append(context)
            else:
                raw_cites[id] = [context]

        if not resolve_refs:
            out_cites = {"unresolved": raw_cites, "resolved": {}}
            return out_cites

        resolved_cites = {}
        if self.back_meta is not None:
            if self.back_meta.find("ref-list"):
                ref_results = self.back_meta.find("ref-list").find_all("ref")
            else:
                ref_results = []
            for r in ref_results:
                if r["id"]:
                    ref_id = r["id"]
                    bibc = None
                    for e in r.find_all("ext-link"):
                        if e.has_attr("ext-link-type") and e["ext-link-type"] == "bibcode":
                            bibc = e.get_text()
                            # if we have the bibcode and it matches something in our unresolved dict, add to output
                            tmp = raw_cites.pop(ref_id, [])
                            if tmp:
                                resolved_cites[bibc] = tmp
                    if not bibc:
                        # load the parsed references file (this should happen just once per input file, so somewhere up above)
                        # parsed references file: /proj/ads_references/resolved/<bibstem>/<volume?>/<bibcode>.iopft.xml.result
                        # columns of this file: score \s parsed reference bibcode \s raw XML
                        # check w/ Golnaz for a reader for this file
                        # look at this file: https://github.com/golnazads/ADSReferencePipeline/blob/master/adsrefpipe/utils.py

                        # if we don't have the bibcode, parse ref and add to a structure to query the API
                        # authors = r.find_all("surname")

                        # match the raw ref XML from the input file to the parsed reference bibcode
                        # bibc = "BIB" + ref_id

                        # options for resolving references
                        # 1. parse references here, pass parsed references to /xml endpoint to get bibcode
                        #     pros: cleanest, easiest for other people to run the code later (no special /proj access needed)
                        #     cons: have to parse the reference a bit to pass it to the /xml endpoint (code duplication, re-inventing the wheel, etc.),
                        #           will likely have to make multiple requests per input file (can only do 16 refs per request) so this will be slower
                        #           (plus API request overhead) and could be a hit on our API depending on how many files are being processed
                        # 2. use these files: /proj/ads_references/resolved/<bibstem>/<volume?>/<bibcode>.iopft.xml.result to match raw XML w/ parsed bibcode
                        #     pros: only need to access one file per XML file, easy to code, no need to do anything special for different formats
                        #     cons: have to construct the file path (e.g. know the bibstem, volume, bibcode of the input file), have to establish
                        #           connection to /proj (though pipelines can be set up to do this automatically, harder for individual users to do on
                        #           localhost)
                        # 3. reference pipeline database? is that a thing? there's a model for it but not sure if that's running anywhere
                        #     pros: potentially easier to connect to than /proj (maybe), don't need to know input file's bibcode
                        #     cons: not sure this is deployed anywhere useful right now, or populated
                        pass

        out_cites = {"resolved": resolved_cites, "unresolved": raw_cites}

        return out_cites
