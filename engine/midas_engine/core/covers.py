"""Shared mutagen cover-art helpers.

Factored out of downloader._tag_audio so the Studio reuses the exact same
embedding logic. Every embed strips any pre-existing artwork first, so a
cover replacement can never append a second picture.
"""
import base64
from pathlib import Path
from typing import Optional, Tuple

AUDIO_SUFFIXES = (".mp3", ".m4a", ".flac", ".opus", ".ogg")


def mime_of(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def embed_cover_audio(path: Path, cover: bytes) -> bool:
    """Embed `cover` as the front cover of an audio file, replacing any
    existing art (.mp3/.m4a/.flac/.opus/.ogg). Returns True on success."""
    mime = mime_of(cover)
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3,
                                      desc="Cover", data=cover)])
            # ID3v2.3 (not mutagen's v2.4 default): the version Windows
            # Media Player, Explorer thumbnails and most hardware players
            # actually read (BUG 4).
            tags.save(path, v2_version=3)
        elif suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            fmt = (MP4Cover.FORMAT_PNG if mime == "image/png"
                   else MP4Cover.FORMAT_JPEG)
            mp4 = MP4(path)
            mp4["covr"] = [MP4Cover(cover, fmt)]
            mp4.save()
        elif suffix == ".flac":
            from mutagen.flac import FLAC, Picture
            flac = FLAC(path)
            flac.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = cover
            flac.add_picture(pic)
            flac.save()
        elif suffix in (".opus", ".ogg"):
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            try:
                ogg = OggOpus(path)
            except Exception:
                ogg = OggVorbis(path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.data = cover
            ogg["metadata_block_picture"] = [
                base64.b64encode(pic.write()).decode("ascii")]
            ogg.save()
        else:
            return False
        return True
    except Exception:
        return False


def extract_cover_audio(path: Path) -> Optional[Tuple[bytes, str]]:
    """Return (bytes, mime) of the embedded front cover of an audio file,
    or None when the file has no embedded art."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3
            pics = ID3(path).getall("APIC")
            if pics:
                return bytes(pics[0].data), pics[0].mime or "image/jpeg"
        elif suffix in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            covers = (MP4(path).tags or {}).get("covr") or []
            if covers:
                cover = covers[0]
                mime = ("image/png"
                        if cover.imageformat == MP4Cover.FORMAT_PNG
                        else "image/jpeg")
                return bytes(cover), mime
        elif suffix == ".flac":
            from mutagen.flac import FLAC
            pics = FLAC(path).pictures
            if pics:
                return bytes(pics[0].data), pics[0].mime or "image/jpeg"
        elif suffix in (".opus", ".ogg"):
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            from mutagen.oggvorbis import OggVorbis
            try:
                ogg = OggOpus(path)
            except Exception:
                ogg = OggVorbis(path)
            blobs = ogg.get("metadata_block_picture") or []
            if blobs:
                pic = Picture(base64.b64decode(blobs[0]))
                return bytes(pic.data), pic.mime or "image/jpeg"
    except Exception:
        return None
    return None
