#!/usr/bin/env python3
"""Show what In My Home sees when it reads a product link, and why a price was or was not found.

    python3 tools/check_link.py "https://www.walmart.com/ip/Some-Product/12345678"
    python3 tools/check_link.py URL --save page.html     # also keep the page it received, to look at or share
    python3 tools/check_link.py URL --ua "Mozilla/5.0 ..."   # try another browser identity

Nothing here needs the app to be running. Standard library only.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import linkinfo  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--save", metavar="FILE", help="write the received page to FILE")
    ap.add_argument("--ua", help="User-Agent to send (default: the app's)")
    ap.add_argument("--allow-private", action="store_true", help="allow local/private addresses (for testing against your own pages)")
    a = ap.parse_args(argv)
    if a.ua:
        linkinfo.USER_AGENT = a.ua
    print("Link:        %s" % a.url)
    print("User-Agent:  %s" % linkinfo.USER_AGENT)
    try:
        status, final, ctype, doc = linkinfo.fetch_raw(a.url, allow_private=a.allow_private)
    except linkinfo.LinkError as e:
        print("\nCould not fetch: %s" % e.message)
        return 1
    print("HTTP status: %s" % status)
    print("Final link:  %s%s" % (final, "" if final == a.url else "   (redirected)"))
    print("Content:     %s, %d characters" % (ctype or "unknown type", len(doc)))
    if a.save:
        with open(a.save, "w", encoding="utf-8") as fh:
            fh.write(doc)
        print("Saved page:  %s" % a.save)
    markers = [m for m in linkinfo.ROBOT_MARKERS if m in doc[:200_000].lower()]
    print("Robot check: %s" % ("LIKELY, found: " + ", ".join(markers) if markers else "none found"))
    page = linkinfo._Page()
    try:
        page.feed(doc)
    except Exception:
        pass
    item_id = linkinfo.item_id_from_url(final)
    print("Product id:  %s" % (item_id or "none found in the link"))
    print("Page title:  %s" % (page.title or page.meta.get("og:title") or "(none)")[:120])
    print("Data on the page:")
    print("  JSON-LD blocks:           %d" % len(page.ld))
    print("  embedded JSON blocks:     %d%s" % (len(page.blobs), "  (e.g. __NEXT_DATA__)" if "__NEXT_DATA__" in doc else ""))
    print("  price meta tags:          %s" % (", ".join(k for k in page.meta if "price" in k) or "none"))
    print("  itemprop=price tags:      %d" % len(page.itemprop))
    if item_id:
        print("  product id inside the data: %s" % ("yes" if any(item_id in b.lower() for b in page.blobs) else "no"))
    res = linkinfo.extract_price(doc, final)
    print("\nResult:")
    if res["price"] is not None:
        print("  PRICE FOUND: %s %s  (source: %s)" % (res["price"], res["currency"] or "USD?", res["source"]))
        print("  Title: %s" % res["title"])
        return 0
    if markers:
        print("  No price. The site returned a robot check, not the product page.")
    elif status >= 400:
        print("  No price. The site answered HTTP %s." % status)
    else:
        print("  No price found in the data this page publishes.")
        snippet = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", doc, flags=re.S | re.I))
        print("  Start of the page text: %s" % re.sub(r"<[^>]+>", " ", snippet)[:240].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
