import base64
import netrc
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import ParseResult, unquote, urlparse
from xml.etree.ElementTree import Element, ParseError

from nix_update.errors import VersionError
from nix_update.utils import info

from .version import Version

# https://github.com/NixOS/nixpkgs/blob/13ae608185b2430ebffc8b181fa9a854cd241007/pkgs/build-support/fetchgithub/default.nix#L133-L143
GITHUB_PUBLIC = re.compile(
    r"^/(?P<owner>[^~/]+)/(?P<repo>[^/]+)(.git)?/archive/(?P<revWithTag>.+).tar.gz$",
)
GITHUB_PUBLIC_GENERAL = re.compile(r"^/(?P<owner>[^/~]+)/(?P<repo>[^/]+)(.git)?")
GITHUB_PRIVATE = re.compile(
    r"^(/api/v3)?/repos/(?P<owner>[^/~]+)/(?P<repo>[^/]+)/tarball/(?P<revWithTag>.+)$",
)


def version_from_entry(entry: Element) -> Version:
    if entry is None:
        msg = "No release found"
        raise VersionError(msg)
    link = entry.find("{http://www.w3.org/2005/Atom}link")
    assert link is not None
    href = link.attrib["href"]
    url = urlparse(href)
    # TODO: set pre-release flag
    return Version(unquote(url.path.split("/")[-1]))


def _dorequest(url: ParseResult, feed_url: str) -> str:
    request = urllib.request.Request(feed_url)

    try:
        netrccreds = netrc.netrc().authenticators(url.netloc)
        if netrccreds is not None:
            info("using netrc file")
            encoded = f"{netrccreds[0]}:{netrccreds[2]}".encode()
            encodedcreds = base64.b64encode(encoded).decode()
            request.add_header("Authorization", f"Basic {encodedcreds}")
    except FileNotFoundError:
        pass
    except netrc.NetrcParseError:
        info(
            "unable to parse netrc file, please verify content / owner-only permissions (chmod 600)",
        )

    return urllib.request.urlopen(request).read()


def fetch_github_versions(url: ParseResult) -> list[Version]:
    urlmatch = (
        GITHUB_PUBLIC.match(url.path)
        or GITHUB_PRIVATE.match(url.path)
        or (url.netloc == "github.com" and GITHUB_PUBLIC_GENERAL.match(url.path))
    )
    if not urlmatch:
        return []
    owner, repo = urlmatch.group("owner"), urlmatch.group("repo")
    server = url.netloc
    # unfortunately github requires this if condition
    if url.netloc == "api.github.com":
        server = "github.com"
    # TODO fallback to tags?
    feed_url = f"https://{server}/{owner}/{repo.removesuffix('.git')}/releases.atom"
    info(f"fetch {feed_url}")
    resp = _dorequest(url, feed_url)
    try:
        tree = ET.fromstring(resp)
    except ParseError:
        info("unable to parse github response, ignoring")
        return []
    releases = tree.findall(".//{http://www.w3.org/2005/Atom}entry")
    return [version_from_entry(x) for x in releases]


def fetch_github_snapshots(url: ParseResult, branch: str) -> list[Version]:
    urlmatch = (
        GITHUB_PUBLIC.match(url.path)
        or GITHUB_PRIVATE.match(url.path)
        or (url.netloc == "github.com" and GITHUB_PUBLIC_GENERAL.match(url.path))
    )
    if not urlmatch:
        return []
    server = url.netloc
    # unfortunately github requires this if condition
    if url.netloc == "api.github.com":
        server = "github.com"
    owner, repo = urlmatch.group("owner"), urlmatch.group("repo")
    feed_url = f"https://{server}/{owner}/{repo.removesuffix('.git')}/commits/{branch}.atom"
    info(f"fetch {feed_url}")
    resp = _dorequest(url, feed_url)
    try:
        tree = ET.fromstring(resp)
    except ParseError:
        info("unable to parse github response, ignoring")
        return []
    commits = tree.findall(".//{http://www.w3.org/2005/Atom}entry")

    versions = fetch_github_versions(url)
    latest_version = versions[0].number if versions else "0"

    for entry in commits:
        link = entry.find("{http://www.w3.org/2005/Atom}link")
        assert link is not None, "cannot parse ATOM feed: missing link"

        updated = entry.find("{http://www.w3.org/2005/Atom}updated")
        assert updated is not None, "cannot parse ATOM feed: missing updated element"
        assert updated.text is not None, (
            "cannot parse ATOM feed: updated element has no text"
        )

        url = urlparse(link.attrib["href"])
        commit = url.path.rsplit("/", maxsplit=1)[-1]
        date = updated.text.split("T", maxsplit=1)[0]
        return [Version(f"{latest_version}-unstable-{date}", rev=commit)]

    return []
