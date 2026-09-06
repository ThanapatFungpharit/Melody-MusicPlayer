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
            "key": "EmbedThumbnail",
        },
        {
            "key": "FFmpegMetadata",
        },
    ],
    "writethumbnail": True,
    "addmetadata": True,
    "quiet": False,
    "noprogress": False,
}

TIMEOUT = 10
