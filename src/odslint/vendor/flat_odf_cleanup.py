#!/usr/bin/python3
# -*- tab-width: 4; indent-tabs-mode: nil; py-indent-offset: 4 -*-
#
# This file is part of the LibreOffice project.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#

# Source: https://github.com/LibreOffice/core/blob/11f10c48688436129337ffc7a082a56023c58071/bin/flat-odf-cleanup.py#L1

import sys
# sadly need lxml because the python one doesn't preserve namespace prefixes
# and type-detection looks for the string "office:document"
from lxml import etree as ET
#import xml.etree.ElementTree as ET

import os.path
import re

VERBOSE = False

# Matches a start tag with two or more attributes, e.g.
# <style:style style:name="ce1" style:family="table-cell">. Safe to do with
# a regex here (rather than a proper XML tokenizer) because lxml always
# double-quotes attribute values and always escapes any literal '<', '>' or
# '"' inside them - so a start tag never contains an unescaped '<', '>' or
# '"' except as its own delimiters, and no ODF flat-XML file uses CDATA.
_TAG_WITH_ATTRS_RE = re.compile(
    rb'^([ \t]*)<([A-Za-z_][-\w.]*(?::[A-Za-z_][-\w.]*)?)'
    rb'((?:\s+[A-Za-z_][-\w.]*(?::[A-Za-z_][-\w.]*)?="[^"]*")+)(\s*/?)>',
    re.MULTILINE,
)
_ATTR_RE = re.compile(rb'([A-Za-z_][-\w.]*(?::[A-Za-z_][-\w.]*)?)="([^"]*)"')

def split_attributes_onto_lines(data):
    # Reformat any start tag with 2+ attributes to one attribute per line,
    # so that changing a single attribute shows up as a single-line diff
    # instead of rewriting one huge line. Purely cosmetic: verified by
    # re-parsing the result and comparing against the unformatted
    # serialization (see __main__).
    def repl(match):
        indent, tagname, attrs_blob, closing = match.groups()
        attrs = _ATTR_RE.findall(attrs_blob)
        if len(attrs) < 2:
            return match.group(0)
        selfclosing = b'/' in closing
        lines = [indent + b'<' + tagname]
        for i, (name, value) in enumerate(attrs):
            line = indent + b' ' + name + b'="' + value + b'"'
            if i == len(attrs) - 1:
                line += b'/>' if selfclosing else b'>'
            lines.append(line)
        return b'\n'.join(lines)
    return _TAG_WITH_ATTRS_RE.sub(repl, data)

def log(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)

def get_used_p_styles(root):
    elementnames = [
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}alphabetical-index-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}bibliography-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}illustration-index-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-source-style",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}object-index-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}table-index-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}table-of-content-entry-template",
        ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-index-entry-template",
    ]

    # document content
    ps = sum([root.findall(e) for e in elementnames], [])
    usedpstyles = set()
    usedcondstyles = set()
    for p in ps:
        usedpstyles.add(p.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name"))
        if p.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}cond-style-name"):
            usedcondstyles.add(p.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}cond-style-name"))
        if p.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}class-names"):
            for style in p.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}class-names").split(" "):
                usedpstyles.add(style)
    for shape in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}text-style-name]"):
        usedpstyles.add(shape.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}text-style-name"))
    for tabletemplate in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:table:1.0}paragraph-style-name]"):
        usedpstyles.add(tabletemplate.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}paragraph-style-name"))
    for page in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}register-truth-ref-style-name]"):
        usedpstyles.add(page.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}register-truth-ref-style-name"))
    for form in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:form:1.0}text-style-name]"):
        usedpstyles.add(form.get("{urn:oasis:names:tc:opendocument:xmlns:form:1.0}text-style-name"))
    # conditional styles
    for condstyle in usedcondstyles:
        for map_ in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='paragraph'][@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name='" + condstyle + "']/{urn:oasis:names:tc:opendocument:xmlns:style:1.0}map"):
            usedpstyles.add(map_.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}apply-style-name"))
    # other styles
    for notesconfig in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}default-style-name]"):
        usedpstyles.add(notesconfig.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}default-style-name"))
    return usedpstyles

def add_parent_styles(usedstyles, styles):
    size = -1
    while size != len(usedstyles):
        size = len(usedstyles)
        for style in styles:
            if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") in usedstyles:
                if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}parent-style-name"):
                    usedstyles.add(style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}parent-style-name"))
                # only for paragraph styles and master-pages
                if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}next-style-name"):
                    usedstyles.add(style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}next-style-name"))

def remove_unused_styles(root, usedstyles, styles, name):
    for style in styles:
        log(style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
        if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") not in usedstyles:
            log("removing unused " + name + " " + style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
            # it is really dumb that there is no parent pointer in dom
            try:
                root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}automatic-styles").remove(style)
            except ValueError:
                root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}styles").remove(style)

def remove_unused_drawings(root, useddrawings, drawings, name):
    for drawing in drawings:
        log(drawing.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name"))
        if drawing.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name") not in useddrawings:
            log("removing unused " + name + " " + drawing.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name"))
            root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}styles").remove(drawing)

def collect_all_attribute(usedstyles, attribute):
    for element in root.findall(".//*[@" + attribute + "]"):
        usedstyles.add(element.get(attribute))

def collect_all_attribute_list(usedstyles, attribute):
    for element in root.findall(".//*[@" + attribute + "]"):
        for style in element.get(attribute).split(" "):
            usedstyles.add(style)

def renumber_automatic_table_styles(root):
    # LibreOffice assigns internal, sequential names to the automatic
    # (per-document) table styles it generates - ce1, ce2, ... for cell
    # styles, and co/ro/ta for column/row/table styles. The exact numbers
    # are just an internal counter: re-saving the same document can shift
    # every cell style from ce1..ce12 to ce24..ce35 with no semantic change,
    # producing a huge, meaningless diff. Rename them canonically to a dense
    # 1-based sequence per family, in document order, so the names are
    # deterministic regardless of what counter LibreOffice happened to be at.
    #
    # References are rewritten too. Only two attributes ever point at these
    # families - table:style-name (on table/column/row/cell) and
    # table:default-cell-style-name (on table/column/row) - so we rewrite
    # exactly those, which cannot touch an unrelated attribute that merely
    # happens to hold a matching string. Named styles in office:styles are
    # left alone; a reference to one simply isn't in the rename map.
    office = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
    style = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
    table = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    autostyles = root.find(".//" + office + "automatic-styles")
    if autostyles is None:
        return
    family_prefix = {
        "table": "ta",
        "table-column": "co",
        "table-row": "ro",
        "table-cell": "ce",
    }
    counters = {prefix: 0 for prefix in family_prefix.values()}
    rename = {}
    # build the old -> new map from the original names first, in document
    # order, before mutating anything
    for st in autostyles.findall(style + "style"):
        family = st.get(style + "family")
        prefix = family_prefix.get(family)
        if prefix is None:
            continue
        oldname = st.get(style + "name")
        if oldname is None:
            continue
        counters[prefix] += 1
        newname = prefix + str(counters[prefix])
        if oldname != newname:
            rename[oldname] = newname
    if not rename:
        return
    # a canonical name must not collide with any style name that is NOT part
    # of the set being renamed (e.g. a named style, or an automatic style of
    # some other family); if it would, bail out rather than risk aliasing
    allnames = set()
    for st in root.iter(style + "style"):
        n = st.get(style + "name")
        if n is not None:
            allnames.add(n)
    survivors = allnames - set(rename.keys())
    if survivors & set(rename.values()):
        log("skipping table-style renumber to avoid name collision")
        return
    # apply to the definitions
    for st in autostyles.findall(style + "style"):
        n = st.get(style + "name")
        if n in rename:
            st.set(style + "name", rename[n])
    # apply to the references
    refattrs = (table + "style-name", table + "default-cell-style-name")
    for element in root.iter():
        for attr in refattrs:
            v = element.get(attr)
            if v is not None and v in rename:
                element.set(attr, rename[v])

_ZERO_LENGTH_RE = re.compile(r'^-?(?:\d+(?:\.\d+)?|\.\d+)')

def remove_zero_loext_tab_stop_distance(root):
    # LibreOffice adds loext:tab-stop-distance="0cm" (a LO extension
    # attribute, no effect that the standard style:tab-stop-distance /
    # style:tab-stops don't already express) to paragraph-properties on
    # save, even when the source had none. A zero value is pure churn: strip
    # it. A non-zero value could be a deliberate setting, so leave those.
    loext = "{urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0}"
    attr = loext + "tab-stop-distance"
    for element in root.findall(".//*[@" + attr + "]"):
        v = element.get(attr)
        m = _ZERO_LENGTH_RE.match(v)
        if m and float(m.group(0)) == 0:
            log("removing zero-length loext:tab-stop-distance")
            del element.attrib[attr]

def remove_unused_namespaces(root):
    nsmap = root.nsmap
    textuallyused = set()
    for element in root.iter():
        for value in element.attrib.values():
            for prefix in nsmap:
                if prefix and (prefix + ":") in value:
                    textuallyused.add(prefix)
        if element.text:
            for prefix in nsmap:
                if prefix and (prefix + ":") in element.text:
                    textuallyused.add(prefix)
    log("keeping textually-referenced namespace prefixes " + str(textuallyused))
    # top_nsmap re-declares every currently-declared prefix on the root
    # first; cleanup then drops the (now redundant) re-declarations that
    # LibreOffice sprinkles on individual elements deeper in the tree
    # (e.g. every style:style in automatic-styles repeats a dozen xmlns
    # declarations that are already in scope from the root), on top of
    # dropping prefixes that are entirely unused.
    ET.cleanup_namespaces(root, top_nsmap=nsmap, keep_ns_prefixes=textuallyused)

def remove_unused(root):
    # 1) find all elements that may reference page styles - this gets rid of some paragraphs
    usedpstyles = get_used_p_styles(root)
    log(usedpstyles)
    usedtstyles = set()
    tables = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table")
    log(tables)
    for table in tables:
        usedtstyles.add(table.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}style-name"))
    pstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='paragraph']")
    tstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='table']")
    usedmasterpages = {"Standard"} # assume this is the default on page 1
    # only automatic styles may have page breaks in LO, so no need to chase parents or nexts
    for pstyle in pstyles:
        log(pstyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
        if pstyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") in usedpstyles:
            usedmasterpages.add(pstyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}master-page-name"))
    for tstyle in tstyles:
        if tstyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") in usedtstyles:
            usedmasterpages.add(tstyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}master-page-name"))
    for node in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}master-page-name]"):
        usedmasterpages.add(node.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}master-page-name"))
    for node in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}master-page-name]"):
        usedmasterpages.add(node.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}master-page-name"))
    log(usedmasterpages)
    # iterate parent/next until no more masterpage is added
    size = -1
    while size != len(usedmasterpages):
        size = len(usedmasterpages)
        for mp in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}master-page"):
            if mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") in usedmasterpages:
                if mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}parent-style-name"):
                    usedmasterpages.add(mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}parent-style-name"))
                if mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}next-style-name"):
                    usedmasterpages.add(mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}next-style-name"))
    # remove unused masterpages
    for mp in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}master-page"):
        if mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") not in usedmasterpages:
            log("removing unused master page " + mp.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
            # there is no way to get the parent element???
            root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}master-styles").remove(mp)

    # 2) remove unused paragraph styles
    usedpstyles = get_used_p_styles(root)

    add_parent_styles(usedpstyles, pstyles)
    remove_unused_styles(root, usedpstyles, pstyles, "paragraph style")

    # 3) unused list styles - keep referenced from still used paragraph styles
    usedliststyles = set()
    for style in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}list-style-name]"):
        usedliststyles.add(style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}list-style-name"))
    for list_ in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name]"):
        usedliststyles.add(list_.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name"))
    for listitem in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list-item[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-override]"):
        usedliststyles.add(listitem.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-override"))
    for numpara in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}numbered-paragraph[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name]"):
        usedliststyles.add(list_.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name"))
    # ignore ones that are children of style:graphic-properties, those must be handled as the containing style
    # there is no inheritance for these
    liststyles = root.findall("./*/{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list-style")
    remove_unused_styles(root, usedliststyles, liststyles, "list style")

    # 4) unused text styles
    usedtextstyles = set()
    usedsectionstyles = set()
    usedrubystyles = set()

    sections = {
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}alphabetical-index",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}bibliography",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}illustration-index",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-title",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}object-index",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}section",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}table-of-content",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}table-index",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-index",
    }
    texts = {
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}a",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-bibliography",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-chapter",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-link-end",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-link-start",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-page-number",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-span",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-tab-stop",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-entry-text",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}index-title-template",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}linenumbering-configuration",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list-level-style-number",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}list-level-style-bullet",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}outline-level-style",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}ruby-text",
        "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}span",
    }
    for element in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name]"):
        style = element.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}style-name")
        if element.tag == "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}ruby":
            usedrubystyles.add(style)
        elif element.tag in sections:
            usedsectionstyles.add(style)
        elif element.tag in texts:
            usedtextstyles.add(style)

    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style-name")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}leader-text-style")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}text-line-through-text-style")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}visited-style-name")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}main-entry-style-name")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}citation-style-name")
    collect_all_attribute(usedtextstyles, "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}citation-body-style-name")
    for span in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}span[@{urn:oasis:names:tc:opendocument:xmlns:text:1.0}class-names]"):
        for style in span.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}class-names").split(" "):
            usedtextstyles.add(style)
    textstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='text']")
    add_parent_styles(usedtextstyles, textstyles)
    remove_unused_styles(root, usedtextstyles, textstyles, "text style")

    # 5) unused ruby styles - can't have parents?
    rubystyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='ruby']")
    remove_unused_styles(root, usedrubystyles, rubystyles, "ruby style")

    # 6) unused section styles - can't have parents?
    sectionstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='section']")
    remove_unused_styles(root, usedsectionstyles, sectionstyles, "section style")

    # 7) presentation styles
    usedpresentationstyles = set()

    collect_all_attribute(usedpresentationstyles, "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}style-name")
    collect_all_attribute_list(usedpresentationstyles, "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}class-names")

    presentationstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='presentation']")
    add_parent_styles(usedpresentationstyles, presentationstyles)
    remove_unused_styles(root, usedpresentationstyles, presentationstyles, "presentation style")

    # 8) graphic styles
    pages = {
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}page",
        "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}notes",
        "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}handout-master",
        "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}master-page",
    }
    usedgraphicstyles = set()
    useddrawingpagestyles = set()
    for element in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}style-name]"):
        style = element.get("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}style-name")
        if element.tag in pages:
            useddrawingpagestyles.add(style)
        else:
            usedgraphicstyles.add(style)
    collect_all_attribute_list(usedgraphicstyles, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}class-names")

    graphicstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='graphic']")
    add_parent_styles(usedgraphicstyles, graphicstyles)
    remove_unused_styles(root, usedgraphicstyles, graphicstyles, "graphic style")

    # 9) drawing-page styles
    drawingpagestyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='drawing-page']")
    add_parent_styles(useddrawingpagestyles, drawingpagestyles)
    remove_unused_styles(root, useddrawingpagestyles, drawingpagestyles, "drawing-page style")

    # 10) page layouts
    usedpagelayouts = set()
    collect_all_attribute(usedpagelayouts, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}page-layout-name")
    pagelayouts = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}page-layout")
    remove_unused_styles(root, usedpagelayouts, pagelayouts, "page layout")

    # 11) presentation page layouts
    usedpresentationpagelayouts = set()
    collect_all_attribute(usedpresentationpagelayouts, "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}presentation-page-layout-name")
    presentationpagelayouts = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}presentation-page-layout")
    remove_unused_styles(root, usedpresentationpagelayouts, presentationpagelayouts, "presentation page layout")

    # 12) table (column/row/cell) styles
    usedtablestyles = set()
    usedtablecolumnstyles = set()
    usedtablerowstyles = set()
    usedtablecellstyles = set()

    tables = {
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table:background",
    }
    tablecells = {
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}covered-table-cell",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}body",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}even-columns",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}even-rows",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}first-column",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}first-row",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}last-column",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}last-row",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}odd-columns",
        "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}odd-rows",
    }
    for element in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:table:1.0}style-name]"):
        style = element.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}style-name")
        if element.tag == "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-column":
            usedtablecolumnstyles.add(style)
        elif element.tag == "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row":
            usedtablerowstyles.add(style)
        elif element.tag in tables:
            usedtablestyles.add(style)
        elif element.tag in tablecells:
            usedtablecellstyles.add(style)

    for element in root.findall(".//*[@{urn:oasis:names:tc:opendocument:xmlns:database:1.0}style-name]"):
        style = element.get("{urn:oasis:names:tc:opendocument:xmlns:database:1.0}style-name")
        if element.tag == "{urn:oasis:names:tc:opendocument:xmlns:database:1.0}column":
            usedtablecolumnstyles.add(style)
        else: # db:query db:table-representation
            usedtablestyles.add(style)

    collect_all_attribute(usedtablerowstyles, "{urn:oasis:names:tc:opendocument:xmlns:database:1.0}default-row-style-name")
    collect_all_attribute(usedtablecellstyles, "{urn:oasis:names:tc:opendocument:xmlns:database:1.0}default-cell-style-name")
    collect_all_attribute(usedtablecellstyles, "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}default-cell-style-name")

    tablecolumstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='table-column']")
    tablerowstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='table-row']")
    tablecellstyles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style[@{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family='table-cell']")
    add_parent_styles(usedtablestyles, tstyles)
    add_parent_styles(usedtablecolumnstyles, tablecolumstyles)
    add_parent_styles(usedtablerowstyles, tablerowstyles)
    add_parent_styles(usedtablecellstyles, tablecellstyles)
    remove_unused_styles(root, usedtstyles, tstyles, "table style")
    remove_unused_styles(root, usedtablecolumnstyles, tablecolumstyles, "table column style")
    remove_unused_styles(root, usedtablerowstyles, tablerowstyles, "table row style")
    remove_unused_styles(root, usedtablecellstyles, tablecellstyles, "table cell style")

    # 13) gradients
    usedgradients = set()
    collect_all_attribute(usedgradients, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}fill-gradient-name")
    collect_all_attribute(usedgradients, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}opacity-name")
    gradients = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}gradient")
    remove_unused_drawings(root, usedgradients, gradients, "gradient")

    # 14) hatchs
    usedhatchs = set()
    collect_all_attribute(usedhatchs, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}fill-hatch-name")
    hatchs = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}hatch")
    remove_unused_drawings(root, usedhatchs, hatchs, "hatch")

    # 15) bitmaps
    usedbitmaps = set()
    collect_all_attribute(usedbitmaps, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}fill-image-name")
    bitmaps = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}bitmap")
    remove_unused_drawings(root, usedbitmaps, bitmaps, "bitmap")

    # 16) markers
    usedmarkers = set()
    collect_all_attribute(usedmarkers, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}marker-start")
    collect_all_attribute(usedmarkers, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}marker-end")
    markers = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}marker")
    remove_unused_drawings(root, usedmarkers, markers, "marker")

    # 17) stroke-dash
    usedstrokedashs = set()
    collect_all_attribute(usedstrokedashs, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}stroke-dash")
    collect_all_attribute_list(usedstrokedashs, "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}stroke-dash-names")
    strokedashs = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}stroke-dash")
    remove_unused_drawings(root, usedstrokedashs, strokedashs, "stroke-dash")

    # 12b) unused data (number format) styles - e.g. N0, N2, N114 - that
    # LibreOffice always writes a default set of, whether or not any cell
    # actually uses them. A data style may itself reference another one
    # conditionally via style:map (e.g. a currency style maps negative
    # values to a red variant), so that referenced style counts as used
    # too, chased to a fixed point same as add_parent_styles does for
    # paragraph/page styles.
    datastyletags = {
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}number-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}currency-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}percentage-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}date-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}time-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}boolean-style",
        "{urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0}text-style",
    }
    useddatastyles = set()
    collect_all_attribute(useddatastyles, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}data-style-name")
    collect_all_attribute(useddatastyles, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}percentage-data-style-name")
    datastyles = [element for element in root.iter() if element.tag in datastyletags]
    size = -1
    while size != len(useddatastyles):
        size = len(useddatastyles)
        for datastyle in datastyles:
            if datastyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") in useddatastyles:
                for map_ in datastyle.findall("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}map"):
                    useddatastyles.add(map_.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}apply-style-name"))
    for datastyle in datastyles:
        name = datastyle.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name")
        if name not in useddatastyles:
            log("removing unused data style " + str(name))
            datastyle.getparent().remove(datastyle)

    # 13) unused font-face-decls
    usedfonts = set()
    collect_all_attribute(usedfonts, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}font-name")
    collect_all_attribute(usedfonts, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}font-name-asian")
    collect_all_attribute(usedfonts, "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}font-name-complex")
    fonts = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}font-face")
    for font in fonts:
        if font.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name") not in usedfonts:
            log("removing unused font-face " + font.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
            root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}font-face-decls").remove(font)

    # 14) remove rsid attributes
    styles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}style")
    for style in styles:
        tp = style.find(".//{urn:oasis:names:tc:opendocument:xmlns:style:1.0}text-properties")
        if tp is not None:
            if "{http://openoffice.org/2009/office}rsid" in tp.attrib:
                log("removing rsid from " + style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
                del tp.attrib["{http://openoffice.org/2009/office}rsid"]
            if "{http://openoffice.org/2009/office}paragraph-rsid" in tp.attrib:
                log("removing paragraph-rsid from " + style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}name"))
                del tp.attrib["{http://openoffice.org/2009/office}paragraph-rsid"]

    # 15) unused user field decls
    useduserfields = set()
    for field in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-field-get"):
        useduserfields.add(field.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}name"))
    for field in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-field-input"):
        useduserfields.add(field.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}name"))
    for field in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-field-decl"):
        if field.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}name") not in useduserfields:
            log("removing unused user-field-decl " + field.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}name"))
            root.find(".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}user-field-decls").remove(field)

    # remove office:settings
    settings = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}settings")
    if settings is not None:
        root.remove(settings)

    # scripts are almost never needed
    scripts = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}scripts")
    if scripts is not None:
        root.remove(scripts)

    # remove theme
    theme = root.find(".//{urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0}theme")
    if theme is not None:
        theme.getparent().remove(theme)

    # 16) strip volatile office:meta children that churn on every save but
    # are not shown anywhere in the document itself (dates, edit counters,
    # the generator version string, computed statistics). office:meta may
    # also occur inside embedded objects (e.g. an OLE chart), so search the
    # whole tree, not just the top-level document.
    volatilemetatags = {
        "{http://purl.org/dc/elements/1.1/}date",
        "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}editing-duration",
        "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}editing-cycles",
        "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}generator",
        "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}document-statistic",
    }
    for meta in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}meta"):
        for child in list(meta):
            if child.tag in volatilemetatags:
                log("removing volatile meta element " + child.tag)
                meta.remove(child)
        # LibreOffice writes an office:meta on every save; once its volatile
        # children are stripped it is often left completely empty, which is
        # itself churn (the source had no office:meta at all). Drop an empty
        # one; keep any that still carries real metadata (title, subject,
        # keywords, description, ...).
        if len(meta) == 0 and (meta.text is None or not meta.text.strip()):
            log("removing empty office:meta")
            meta.getparent().remove(meta)

    # 17) remove cached raster replacement images for OLE-ish objects
    # (charts, embedded spreadsheets, applets, plugins, floating frames).
    # LibreOffice writes both the native representation (e.g. draw:object
    # with a full embedded chart document) and a rendered-to-bitmap
    # fallback draw:image for consumers that can't render the native
    # format. LibreOffice itself always re-renders from the native
    # representation, so the fallback bitmap is pure churn (it's a big
    # base64 blob that changes on every save). Verified: removing it
    # produces a pixel-identical PDF export. A draw:frame that contains
    # only a draw:image (an actual inserted picture, no native object) is
    # left untouched.
    foreignobjecttags = {
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}object",
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}object-ole",
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}applet",
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}floating-frame",
        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}plugin",
    }
    for frame in root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}frame"):
        if any(child.tag in foreignobjecttags for child in frame):
            for image in frame.findall("{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}image"):
                log("removing OLE replacement image")
                frame.remove(image)

    # 18) remove calcext:value-type. This LibreOffice extension attribute
    # caches the cell's "logical" value subtype (e.g. currency vs date,
    # which office:value-type alone can't distinguish - both are just
    # "float"). It's derived purely from the cell's applied number-format
    # style, so LibreOffice recomputes it on load; it plays no part in
    # rendering. Verified via round-trip: stripping it and having
    # LibreOffice re-export to flat XML reproduces the exact same
    # calcext:value-type values, and PDF export is pixel-identical.
    calcextvaluetype = "{urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0}value-type"
    for element in root.findall(".//*[@" + calcextvaluetype + "]"):
        del element.attrib[calcextvaluetype]

    # 19) strip zero-length loext:tab-stop-distance churn
    remove_zero_loext_tab_stop_distance(root)

    # 20) give the automatic table styles canonical, deterministic names so
    # LibreOffice's internal renumbering doesn't produce noise diffs
    renumber_automatic_table_styles(root)

    # 21) drop namespace declarations that are not actually needed. LibreOffice
    # declares ~35 namespaces on the root element, most of which no element or
    # attribute in a given document ever uses. lxml's own
    # etree.cleanup_namespaces() would handle this, but it only looks at
    # *structural* namespace use (element/attribute names) - ODF formulas
    # (table:formula, and similar formula-bearing attributes) reference
    # namespace prefixes like "of:" as plain text inside an attribute string
    # (e.g. table:formula="of:=SUM(...)"), which is invisible to that
    # analysis. Blindly removing "unused" declarations strips xmlns:of and
    # LibreOffice then fails to resolve the formula (Err:510). So: keep any
    # prefix that shows up as "prefix:" anywhere in element/attribute text
    # content too, and only let lxml clean up the remainder.
    remove_unused_namespaces(root)

    # TODO: perhaps replace text with xxx (optionally)?

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print("Usage: flat-odf-cleanup.py [--verbose] file.fods")
        exit(1)

    if "--verbose" in args:
        VERBOSE = True
        args.remove("--verbose")

    for f in args:
        if os.path.isfile(f):
            if VERBOSE:
                print(f"processing {f}")

            with open(f, 'rb') as fh:
                original = fh.read()

            dom = ET.parse(f)
            root = dom.getroot()

            remove_unused(root)

            serialized = ET.tostring(root, encoding='UTF-8', xml_declaration=True)
            formatted = split_attributes_onto_lines(serialized)

            # the attribute-splitting is only ever a cosmetic change: prove
            # it by re-parsing the formatted output and checking it
            # round-trips to the exact same tree as the unformatted
            # serialization (lxml itself normalizes intra-tag whitespace
            # away, so comparing their single-line serializations is an
            # exact structural comparison).
            reparsed = ET.tostring(ET.fromstring(formatted), encoding='utf-8')
            if reparsed != ET.tostring(ET.fromstring(serialized), encoding='utf-8'):
                raise AssertionError(f"attribute reformatting changed document structure in {f}")

            # only write if something actually changed
            if formatted != original:
                if VERBOSE:
                    print(f"rewriting {f}")
                with open(f, 'wb') as fh:
                    fh.write(formatted)
            else:
                if VERBOSE:
                    print(f"no changes in {f}")
    """
    TODO
    chart:style-name
    -> chart
    style:data-style-name
    -> data style
    style:percentage-data-style-name
    -> data style
    """

# vim: set shiftwidth=4 softtabstop=4 expandtab:
