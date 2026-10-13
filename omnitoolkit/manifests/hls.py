import re
import urllib.parse

from _request import Request
from traversal import traverse_obj
from utils import (
    determine_ext,
    encode_data_uri,
    float_or_none,
    join_nonempty,
    orderedSet,
    parse_codecs,
    qualities,
)


def parse_m3u8_attributes(attrib):
    info = {}
    for (key, val) in re.findall(r'(?P<key>[A-Z0-9-]+)=(?P<val>"[^"]+"|[^",]+)(?:,|$)', attrib):
        if val.startswith('"'):
            val = val[1:-1]
        info[key] = val
    return info


def has_drm(manifest):
    return bool(re.search('|'.join((
        r'#EXT-X-(?:SESSION-)?KEY:.*?URI="skd://',  # Apple FairPlay
        # Apple FairPlay
        r'#EXT-X-(?:SESSION-)?KEY:.*?KEYFORMAT="com\.apple\.streamingkeydelivery"',
        # Microsoft PlayReady
        r'#EXT-X-(?:SESSION-)?KEY:.*?KEYFORMAT="com\.microsoft\.playready"',
        r'#EXT-X-FAXS-CM:',  # Adobe Flash Access
    )), manifest))


def parse_m3u8_formats_and_subtitles(
        m3u8_doc, m3u8_url=None, ext=None, entry_protocol='m3u8_native',
        preference=None, quality=None, m3u8_id=None, live=False):
    formats, subtitles = [], {}
    has_drm = has_drm(m3u8_doc)

    def format_url(url):
        return url if re.match(r'https?://', url) else urllib.parse.urljoin(m3u8_url, url)

    def _extract_m3u8_playlist_indices(*args, **kwargs):
        return [None]

    # References:
    # 1. https://tools.ietf.org/html/draft-pantos-http-live-streaming-21
    # 2. https://github.com/ytdl-org/youtube-dl/issues/12211
    # 3. https://github.com/ytdl-org/youtube-dl/issues/18923

    # We should try extracting formats only from master playlists [1, 4.3.4],
    # i.e. playlists that describe available qualities. On the other hand
    # media playlists [1, 4.3.3] should be returned as is since they contain
    # just the media without qualities renditions.
    # Fortunately, master playlist can be easily distinguished from media
    # playlist based on particular tags availability. As of [1, 4.3.3, 4.3.4]
    # master playlist tags MUST NOT appear in a media playlist and vice versa.
    # As of [1, 4.3.3.1] #EXT-X-TARGETDURATION tag is REQUIRED for every
    # media playlist and MUST NOT appear in master playlist thus we can
    # clearly detect media playlist with this criterion.

    if '#EXT-X-TARGETDURATION' in m3u8_doc:  # media playlist, return as is
        formats = [{
            'format_id': join_nonempty(m3u8_id, idx),
            'format_index': idx,
            'url': m3u8_url or encode_data_uri(m3u8_doc.encode(), 'application/x-mpegurl'),
            'ext': ext,
            'protocol': entry_protocol,
            'preference': preference,
            'quality': quality,
            'has_drm': has_drm,
        } for idx in _extract_m3u8_playlist_indices(m3u8_doc=m3u8_doc)]

        return formats, subtitles

    groups = {}
    last_stream_inf = {}

    def extract_media(x_media_line):
        media = parse_m3u8_attributes(x_media_line)
        # As per [1, 4.3.4.1] TYPE, GROUP-ID and NAME are REQUIRED
        media_type, group_id, name = media.get(
            'TYPE'), media.get('GROUP-ID'), media.get('NAME')
        if not (media_type and group_id and name):
            return
        groups.setdefault(group_id, []).append(media)
        # <https://tools.ietf.org/html/rfc8216#section-4.3.4.1>
        if media_type == 'SUBTITLES':
            # According to RFC 8216 §4.3.4.2.1, URI is REQUIRED in the
            # EXT-X-MEDIA tag if the media type is SUBTITLES.
            # However, lack of URI has been spotted in the wild.
            # e.g. NebulaIE; see https://github.com/yt-dlp/yt-dlp/issues/339
            if not media.get('URI'):
                return
            url = format_url(media['URI'])
            sub_info = {
                'url': url,
                'ext': determine_ext(url),
            }
            if sub_info['ext'] == 'm3u8':
                # Per RFC 8216 §3.1, the only possible subtitle format m3u8
                # files may contain is WebVTT:
                # <https://tools.ietf.org/html/rfc8216#section-3.1>
                sub_info['ext'] = 'vtt'
                sub_info['protocol'] = 'm3u8_native'
            lang = media.get('LANGUAGE') or 'und'
            subtitles.setdefault(lang, []).append(sub_info)
        if media_type not in ('VIDEO', 'AUDIO'):
            return
        media_url = media.get('URI')
        if media_url:
            manifest_url = format_url(media_url)
            is_audio = media_type == 'AUDIO'
            is_alternate = media.get(
                'DEFAULT') == 'NO' or media.get('AUTOSELECT') == 'NO'
            formats.extend({
                'format_id': join_nonempty(m3u8_id, group_id, name, idx),
                'format_note': name,
                'format_index': idx,
                'url': manifest_url,
                'manifest_url': m3u8_url,
                'language': media.get('LANGUAGE'),
                'ext': ext,
                'protocol': entry_protocol,
                'preference': preference,
                'quality': quality,
                'has_drm': has_drm,
                'vcodec': 'none' if is_audio else None,
                # Alternate audio formats (e.g. audio description) should be deprioritized
                'source_preference': -2 if is_audio and is_alternate else None,
                # Save this to assign source_preference based on associated video stream
                '_audio_group_id': group_id if is_audio and not is_alternate else None,
            } for idx in _extract_m3u8_playlist_indices(manifest_url))

    def build_stream_name():
        # Despite specification does not mention NAME attribute for
        # EXT-X-STREAM-INF tag it still sometimes may be present (see [1]
        # or vidio test in TestInfoExtractor.test_parse_m3u8_formats)
        # 1. http://www.vidio.com/watch/165683-dj_ambred-booyah-live-2015
        stream_name = last_stream_inf.get('NAME')
        if stream_name:
            return stream_name
        # If there is no NAME in EXT-X-STREAM-INF it will be obtained
        # from corresponding rendition group
        stream_group_id = last_stream_inf.get('VIDEO')
        if not stream_group_id:
            return
        stream_group = groups.get(stream_group_id)
        if not stream_group:
            return stream_group_id
        rendition = stream_group[0]
        return rendition.get('NAME') or stream_group_id

    # parse EXT-X-MEDIA tags before EXT-X-STREAM-INF in order to have the
    # chance to detect video only formats when EXT-X-STREAM-INF tags
    # precede EXT-X-MEDIA tags in HLS manifest such as [3].
    for line in m3u8_doc.splitlines():
        if line.startswith('#EXT-X-MEDIA:'):
            extract_media(line)

    for line in m3u8_doc.splitlines():
        if line.startswith('#EXT-X-STREAM-INF:'):
            last_stream_inf = parse_m3u8_attributes(line)
        elif line.startswith('#') or not line.strip():
            continue
        else:
            tbr = float_or_none(
                last_stream_inf.get('AVERAGE-BANDWIDTH')
                or last_stream_inf.get('BANDWIDTH'), scale=1000)
            manifest_url = format_url(line.strip())

            for idx in _extract_m3u8_playlist_indices(manifest_url):
                format_id = [m3u8_id, None, idx]
                # Bandwidth of live streams may differ over time thus making
                # format_id unpredictable. So it's better to keep provided
                # format_id intact.
                if not live:
                    stream_name = build_stream_name()
                    format_id[1] = stream_name or '%d' % (tbr or len(formats))
                f = {
                    'format_id': join_nonempty(*format_id),
                    'format_index': idx,
                    'url': manifest_url,
                    'manifest_url': m3u8_url,
                    'tbr': tbr,
                    'ext': ext,
                    'fps': float_or_none(last_stream_inf.get('FRAME-RATE')),
                    'protocol': entry_protocol,
                    'preference': preference,
                    'quality': quality,
                    'has_drm': has_drm,
                }

                # YouTube-specific
                if yt_audio_content_id := last_stream_inf.get('YT-EXT-AUDIO-CONTENT-ID'):
                    f['language'] = yt_audio_content_id.split('.')[0]

                resolution = last_stream_inf.get('RESOLUTION')
                if resolution:
                    mobj = re.search(
                        r'(?P<width>\d+)[xX](?P<height>\d+)', resolution)
                    if mobj:
                        f['width'] = int(mobj.group('width'))
                        f['height'] = int(mobj.group('height'))
                # Unified Streaming Platform
                mobj = re.search(
                    r'audio.*?(?:%3D|=)(\d+)(?:-video.*?(?:%3D|=)(\d+))?', f['url'])
                if mobj:
                    abr, vbr = mobj.groups()
                    abr, vbr = float_or_none(
                        abr, 1000), float_or_none(vbr, 1000)
                    f.update({
                        'vbr': vbr,
                        'abr': abr,
                    })
                codecs = parse_codecs(last_stream_inf.get('CODECS'))
                f.update(codecs)
                audio_group_id = last_stream_inf.get('AUDIO')
                # As per [1, 4.3.4.1.1] any EXT-X-STREAM-INF tag which
                # references a rendition group MUST have a CODECS attribute.
                # However, this is not always respected. E.g. [2]
                # contains EXT-X-STREAM-INF tag which references AUDIO
                # rendition group but does not have CODECS and despite
                # referencing an audio group it represents a complete
                # (with audio and video) format. So, for such cases we will
                # ignore references to rendition groups and treat them
                # as complete formats.
                if audio_group_id and codecs and f.get('vcodec') != 'none':
                    # Save this to determine quality of audio formats that only have a GROUP-ID
                    f['_audio_group_id'] = audio_group_id
                    audio_group = groups.get(audio_group_id)
                    if audio_group and audio_group[0].get('URI'):
                        # TODO: update acodec for audio only formats with
                        # the same GROUP-ID
                        f['acodec'] = 'none'
                if not f.get('ext'):
                    f['ext'] = 'm4a' if f.get('vcodec') == 'none' else 'mp4'
                formats.append(f)

                # for DailyMotion
                progressive_uri = last_stream_inf.get('PROGRESSIVE-URI')
                if progressive_uri:
                    http_f = f.copy()
                    del http_f['manifest_url']
                    http_f.update({
                        'format_id': f['format_id'].replace('hls-', 'http-'),
                        'protocol': 'http',
                        'url': progressive_uri,
                    })
                    formats.append(http_f)

            last_stream_inf = {}

    # Some audio-only formats only have a GROUP-ID without any other quality/bitrate/codec info
    # Each audio GROUP-ID corresponds with one or more video formats' AUDIO attribute
    # For sorting purposes, set source_preference based on the quality of the video formats they are grouped with
    # See https://github.com/yt-dlp/yt-dlp/issues/11178
    audio_groups_by_quality = orderedSet(f['_audio_group_id'] for f in sorted(
        traverse_obj(formats, lambda _, v: v.get('vcodec')
                     != 'none' and v['_audio_group_id']),
        key=lambda x: (x.get('tbr') or 0, x.get('width') or 0)))
    audio_quality_map = {
        audio_groups_by_quality[0]: 'low',
        audio_groups_by_quality[-1]: 'high',
    } if len(audio_groups_by_quality) > 1 else None
    audio_preference = qualities(audio_groups_by_quality)
    for fmt in formats:
        audio_group_id = fmt.pop('_audio_group_id', None)
        if not audio_quality_map or not audio_group_id or fmt.get('vcodec') != 'none':
            continue
        # Use source_preference since quality and preference are set by params
        fmt['source_preference'] = audio_preference(audio_group_id)
        fmt['format_note'] = join_nonempty(
            fmt.get('format_note'), audio_quality_map.get(audio_group_id), delim=', ')

    return formats, subtitles


def parse_m3u8_vod_duration(m3u8_vod):
    if '#EXT-X-ENDLIST' not in m3u8_vod:
        return None

    return int(sum(
        float(line[len('#EXTINF:'):].split(',')[0])
        for line in m3u8_vod.splitlines() if line.startswith('#EXTINF:'))) or None


def extract_m3u8_vod_duration(m3u8_vod_url, data=None, headers={}, query={}):
    m3u8_vod = Request(m3u8_vod_url, data=data,
                       headers=headers, query=query)().text
    return parse_m3u8_vod_duration(m3u8_vod or '')


def parse_m3u8_vod_duration(m3u8_vod):
    if '#EXT-X-ENDLIST' not in m3u8_vod:
        return None

    return int(sum(
        float(line[len('#EXTINF:'):].split(',')[0])
        for line in m3u8_vod.splitlines() if line.startswith('#EXTINF:'))) or None


def extract_m3u8_formats(*args, **kwargs):
    fmts, _ = extract_m3u8_formats_and_subtitles(*args, **kwargs)
    return fmts


def extract_m3u8_formats_and_subtitles(
        m3u8_url, ext=None, entry_protocol='m3u8_native',
        preference=None, quality=None, m3u8_id=None, live=False,
        data=None, headers={}, query={}
):
    if not m3u8_url:
        return [], {}
    response = Request(m3u8_url, data=data, headers=headers, query=query)()
    if response is False:
        return [], {}

    if not response.text.startswith('#EXTM3U'):
        return [], {}

    return parse_m3u8_formats_and_subtitles(
        response.text, response.url, ext=ext, entry_protocol=entry_protocol,
        preference=preference, quality=quality, m3u8_id=m3u8_id,
        live=live, data=data, headers=headers, query=query)
