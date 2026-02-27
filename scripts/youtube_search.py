#!/usr/bin/env python3
"""
youtube_search.py — Search YouTube, download subtitles and audio via yt-dlp.

Usage:
    youtube_search.py search "cozy documentary"
    youtube_search.py subs VIDEO_ID --output path.vtt
    youtube_search.py audio VIDEO_ID --output /path/to/song.mp3 --max-duration 480
"""

import sys
import os
import json
import argparse

try:
    import yt_dlp
except ImportError:
    print(json.dumps({"error": "yt-dlp not installed. Run: pip install yt-dlp"}),
          file=sys.stderr)
    sys.exit(1)


def format_duration(seconds):
    """Convert seconds to human-readable duration."""
    if not seconds:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_views(count):
    """Convert view count to readable string."""
    if not count:
        return "unknown"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def search_youtube(query, max_results=5):
    """Search YouTube and return structured results."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": "ytsearch",
    }

    search_query = f"ytsearch{max_results}:{query}"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_query, download=False)

    results = []
    for entry in info.get("entries", []):
        if not entry:
            continue
        results.append({
            "id": entry.get("id", ""),
            "title": entry.get("title", ""),
            "duration": entry.get("duration"),
            "duration_display": format_duration(entry.get("duration")),
            "channel": entry.get("channel") or entry.get("uploader", ""),
            "views": entry.get("view_count"),
            "views_display": format_views(entry.get("view_count")),
            "url": f"https://www.youtube.com/watch?v={entry.get('id', '')}",
            "description": (entry.get("description") or "")[:300],
        })

    return results


def download_subs(video_id, output_path):
    """Download English subtitles for a video. Prefers manual > auto-generated."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    # First, try manual English subs
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "vtt",
        "outtmpl": output_path.replace(".vtt", ""),
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Check available subs
    manual_subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})

    video_meta = {
        "id": video_id,
        "title": info.get("title", ""),
        "duration": info.get("duration"),
        "channel": info.get("channel") or info.get("uploader", ""),
        "description": (info.get("description") or "")[:500],
    }

    # Try manual English subs first
    if "en" in manual_subs:
        ydl_opts["writesubtitles"] = True
        ydl_opts["writeautomaticsub"] = False
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return {"success": True, "type": "manual", "path": output_path, **video_meta}

    # Fall back to auto-generated English
    if "en" in auto_subs:
        ydl_opts["writesubtitles"] = False
        ydl_opts["writeautomaticsub"] = True
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return {"success": True, "type": "auto", "path": output_path, **video_meta}

    # Try en-US or similar variants
    for lang_code in list(manual_subs.keys()) + list(auto_subs.keys()):
        if lang_code.startswith("en"):
            is_manual = lang_code in manual_subs
            ydl_opts["subtitleslangs"] = [lang_code]
            ydl_opts["writesubtitles"] = is_manual
            ydl_opts["writeautomaticsub"] = not is_manual
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return {
                "success": True,
                "type": "manual" if is_manual else "auto",
                "path": output_path,
                **video_meta,
            }

    # No English subs available
    return {"success": False, "error": "no English subtitles available", **video_meta}


def download_audio(video_id, output_path, max_duration=480):
    """Download audio-only from a YouTube video as mp3."""
    url = f"https://www.youtube.com/watch?v={video_id}"

    # First, fetch video info to check duration
    info_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    duration = info.get("duration") or 0
    title = info.get("title", "")
    channel = info.get("channel") or info.get("uploader", "")

    if duration > max_duration:
        return {
            "success": False,
            "error": f"Video is {format_duration(duration)} — exceeds max duration of {format_duration(max_duration)}",
            "title": title,
            "duration": duration,
            "channel": channel,
        }

    # Download audio only
    dl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "extractaudio": True,
        "outtmpl": output_path.replace(".mp3", ""),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "5",
        }],
    }

    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        ydl.download([url])

    # yt-dlp adds .mp3 extension via postprocessor
    # Check both the exact path and the path with .mp3 appended
    actual_path = output_path
    if not os.path.exists(actual_path) and os.path.exists(output_path + ".mp3"):
        actual_path = output_path + ".mp3"
    elif not os.path.exists(actual_path):
        # outtmpl had .mp3 stripped, so file should be at output_path
        # Try the original path as-is
        if not os.path.exists(output_path):
            return {
                "success": False,
                "error": f"Download completed but file not found at {output_path}",
                "title": title,
                "duration": duration,
                "channel": channel,
            }

    return {
        "success": True,
        "path": actual_path,
        "title": title,
        "duration": duration,
        "duration_display": format_duration(duration),
        "channel": channel,
    }


def main():
    parser = argparse.ArgumentParser(description="YouTube search and subtitle download")
    subparsers = parser.add_subparsers(dest="command")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search YouTube")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--max", type=int, default=5, help="Max results")

    # Subs command
    subs_parser = subparsers.add_parser("subs", help="Download subtitles")
    subs_parser.add_argument("video_id", help="YouTube video ID")
    subs_parser.add_argument("--output", required=True, help="Output .vtt path")

    # Audio command
    audio_parser = subparsers.add_parser("audio", help="Download audio as mp3")
    audio_parser.add_argument("video_id", help="YouTube video ID")
    audio_parser.add_argument("--output", required=True, help="Output .mp3 path")
    audio_parser.add_argument("--max-duration", type=int, default=480,
                              help="Max video duration in seconds (default: 480 = 8 min)")

    args = parser.parse_args()

    if args.command == "search":
        results = search_youtube(args.query, args.max)
        print(json.dumps(results, indent=2))

    elif args.command == "subs":
        result = download_subs(args.video_id, args.output)
        print(json.dumps(result, indent=2))

    elif args.command == "audio":
        result = download_audio(args.video_id, args.output, args.max_duration)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
