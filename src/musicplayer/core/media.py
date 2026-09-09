"""Provider identities independent of titles, paths, UI, and storage records."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class MediaIdentity:
    provider: str
    media_id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.media_id}"

    @classmethod
    def from_url(cls, source: str) -> MediaIdentity | None:
        parsed = urlsplit(source.strip())
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        parts = parsed.path.strip("/").split("/")
        media_id = ""
        if host == "youtu.be":
            media_id = parts[0]
        elif host in {
            "youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtube-nocookie.com",
        }:
            media_id = parse_qs(parsed.query).get("v", [""])[0]
            if (
                not media_id
                and len(parts) == 2
                and parts[0] in {"shorts", "embed", "live"}
            ):
                media_id = parts[1]
        if media_id and parsed.scheme.casefold() in {"http", "https"}:
            return cls("youtube", media_id)
        return None


def source_key(source: str) -> str:
    identity = MediaIdentity.from_url(source)
    if identity is not None:
        return identity.key
    parsed = urlsplit(source.strip())
    query = parse_qs(parsed.query)
    filtered = [
        (name, value)
        for name in sorted(query)
        if not name.casefold().startswith("utm_")
        for value in query[name]
    ]
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold().removeprefix("www."),
            parsed.path.rstrip("/"),
            urlencode(filtered),
            "",
        )
    )
