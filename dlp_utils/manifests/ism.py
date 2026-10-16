import re
import urllib.parse

from .._request import Request
from ..utils import (
    ISO639Utils,
    int_or_none,
    join_nonempty,
)


def extract_ism_formats(*args, **kwargs):
    fmts, _ = extract_ism_formats_and_subtitles(*args, **kwargs)
    return fmts


def extract_ism_formats_and_subtitles(ism_url, ism_id=None, data=None, headers={}, query={}):
    resp = Request(ism_url, data=data, headers=headers, query=query)()
    return parse_ism_formats_and_subtitles(resp.text, resp.url, ism_id)


def parse_ism_formats_and_subtitles(ism_doc, ism_url, ism_id=None):
    """
    Parse formats from ISM manifest.
    References:
     1. [MS-SSTR]: Smooth Streaming Protocol,
        https://msdn.microsoft.com/en-us/library/ff469518.aspx
    """
    if ism_doc.get('IsLive') == 'TRUE':
        return [], {}

    duration = int(ism_doc.attrib['Duration'])
    timescale = int_or_none(ism_doc.get('TimeScale')) or 10000000

    formats = []
    subtitles = {}
    for stream in ism_doc.findall('StreamIndex'):
        stream_type = stream.get('Type')
        if stream_type not in ('video', 'audio', 'text'):
            continue
        url_pattern = stream.attrib['Url']
        stream_timescale = int_or_none(stream.get('TimeScale')) or timescale
        stream_name = stream.get('Name')
        # IsmFD expects ISO 639 Set 2 language codes (3-character length)
        # See: https://github.com/yt-dlp/yt-dlp/issues/11356
        stream_language = stream.get('Language') or 'und'
        if len(stream_language) != 3:
            stream_language = ISO639Utils.short2long(stream_language) or 'und'
        for track in stream.findall('QualityLevel'):
            KNOWN_TAGS = {'255': 'AACL', '65534': 'EC-3'}
            fourcc = track.get('FourCC') or KNOWN_TAGS.get(
                track.get('AudioTag'))
            # TODO: add support for WVC1 and WMAP
            if fourcc not in ('H264', 'AVC1', 'AACL', 'TTML', 'EC-3'):
                continue
            tbr = int(track.attrib['Bitrate']) // 1000
            # [1] does not mention Width and Height attributes. However,
            # they're often present while MaxWidth and MaxHeight are
            # missing, so should be used as fallbacks
            width = int_or_none(track.get('MaxWidth') or track.get('Width'))
            height = int_or_none(track.get('MaxHeight') or track.get('Height'))
            sampling_rate = int_or_none(track.get('SamplingRate'))

            track_url_pattern = re.sub(
                r'{[Bb]itrate}', track.attrib['Bitrate'], url_pattern)
            track_url_pattern = urllib.parse.urljoin(
                ism_url, track_url_pattern)

            fragments = []
            fragment_ctx = {
                'time': 0,
            }
            stream_fragments = stream.findall('c')
            for stream_fragment_index, stream_fragment in enumerate(stream_fragments):
                fragment_ctx['time'] = int_or_none(
                    stream_fragment.get('t')) or fragment_ctx['time']
                fragment_repeat = int_or_none(stream_fragment.get('r')) or 1
                fragment_ctx['duration'] = int_or_none(
                    stream_fragment.get('d'))
                if not fragment_ctx['duration']:
                    try:
                        next_fragment_time = int(
                            stream_fragment[stream_fragment_index + 1].attrib['t'])
                    except IndexError:
                        next_fragment_time = duration
                    fragment_ctx['duration'] = (
                        next_fragment_time - fragment_ctx['time']) / fragment_repeat
                for _ in range(fragment_repeat):
                    fragments.append({
                        'url': re.sub(r'{start[ _]time}', str(fragment_ctx['time']), track_url_pattern),
                        'duration': fragment_ctx['duration'] / stream_timescale,
                    })
                    fragment_ctx['time'] += fragment_ctx['duration']

            if stream_type == 'text':
                subtitles.setdefault(stream_language, []).append({
                    'ext': 'ismt',
                    'protocol': 'ism',
                    'url': ism_url,
                    'manifest_url': ism_url,
                    'fragments': fragments,
                    '_download_params': {
                        'stream_type': stream_type,
                        'duration': duration,
                        'timescale': stream_timescale,
                        'fourcc': fourcc,
                        'language': stream_language,
                        'codec_private_data': track.get('CodecPrivateData'),
                    },
                })
            elif stream_type in ('video', 'audio'):
                formats.append({
                    'format_id': join_nonempty(ism_id, stream_name, tbr),
                    'url': ism_url,
                    'manifest_url': ism_url,
                    'ext': 'ismv' if stream_type == 'video' else 'isma',
                    'width': width,
                    'height': height,
                    'tbr': tbr,
                    'asr': sampling_rate,
                    'vcodec': 'none' if stream_type == 'audio' else fourcc,
                    'acodec': 'none' if stream_type == 'video' else fourcc,
                    'protocol': 'ism',
                    'fragments': fragments,
                    'has_drm': ism_doc.find('Protection') is not None,
                    'language': stream_language,
                    'audio_channels': int_or_none(track.get('Channels')),
                    '_download_params': {
                        'stream_type': stream_type,
                        'duration': duration,
                        'timescale': stream_timescale,
                        'width': width or 0,
                        'height': height or 0,
                        'fourcc': fourcc,
                        'language': stream_language,
                        'codec_private_data': track.get('CodecPrivateData'),
                        'sampling_rate': sampling_rate,
                        'channels': int_or_none(track.get('Channels', 2)),
                        'bits_per_sample': int_or_none(track.get('BitsPerSample', 16)),
                        'nal_unit_length_field': int_or_none(track.get('NALUnitLengthField', 4)),
                    },
                })
    return formats, subtitles
