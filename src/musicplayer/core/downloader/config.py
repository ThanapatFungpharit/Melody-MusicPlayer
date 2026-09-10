OPTS = {
    "format": "bestaudio/best",
    "outtmpl": "%(title)s.%(ext)s",
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        },
        {
            "key": "FFmpegMetadata",
        },
        {
            "key": "EmbedThumbnail",
            # Keep the sidecar in staging after yt-dlp burns it into the media.
            # The downloader verifies the embedded tag, then derives the UI
            # cache from that tag rather than treating the cache as canonical.
            "already_have_thumbnail": True,
        },
    ],
    "writethumbnail": True,
    "addmetadata": True,
    "quiet": False,
    "noprogress": False,
}

TIMEOUT = 10
