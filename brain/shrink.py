"""Keep HTML evidence small while preserving useful selector attributes."""

from collections import Counter

from lxml import etree, html

_DROP = {"script", "style", "svg", "noscript", "template"}
_ATTRIBUTES = {
    "id", "class", "href", "title", "rel", "role", "lang", "itemprop", "datetime",
    "aria-label", "data-testid", "data-id", "data-oid", "data-tender-id", "download",
    "data-tendersid", "data-sort-value",
}
_REPEATED = {"article", "div", "li", "tr"}


def shrink(markup: str, max_chars: int = 60_000) -> str:
    try:
        root = html.fromstring(markup)
    except (ValueError, etree.ParserError):
        return ""
    if root.tag in _DROP:
        return ""

    for node in list(root.iter()):
        if not isinstance(node.tag, str) or node.tag in _DROP:
            if node.getparent() is not None:
                node.drop_tree()
            continue
        for key in list(node.attrib):
            if key not in _ATTRIBUTES:
                del node.attrib[key]
        rows: Counter[tuple[str, str]] = Counter()
        for child in list(node):
            tag = child.tag
            css_class = child.get("class", "")
            if tag in _REPEATED and (tag != "div" or css_class):
                group = (tag, css_class)
                rows[group] += 1
                if rows[group] > 5:
                    child.drop_tree()
    compact = " ".join(html.tostring(root, encoding="unicode", method="html").split())
    if len(compact) > max_chars:
        primary = root.xpath("(descendant-or-self::main | descendant-or-self::*[@role='main'])[1]")
        if primary:
            # Keep the content and head pagination links visible ahead of long site menus.
            head_links = root.xpath("//head/link[@href and @rel]")
            relevant = head_links + primary
            compact = " ".join("".join(html.tostring(node, encoding="unicode") for node in relevant).split())
    return compact[:max_chars]
