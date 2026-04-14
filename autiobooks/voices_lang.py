import sys


voices_internal = [
    'af_alloy',
    'af_aoede',
    'af_bella',
    'af_heart',
    'af_jessica',
    'af_kore',
    'af_nicole',
    'af_nova',
    'af_river',
    'af_sarah',
    'af_sky',
    'am_adam',
    'am_echo',
    'am_eric',
    'am_fenrir',
    'am_liam',
    'am_michael',
    'am_onyx',
    'am_puck',
    'am_santa',
    'bf_alice',
    'bf_emma',
    'bf_isabella',
    'bf_lily',
    'bm_daniel',
    'bm_fable',
    'bm_george',
    'bm_lewis',
    'ef_dora',
    'em_alex',
    'em_santa',
    'ff_siwis',
    'hf_alpha',
    'hf_beta',
    'hm_omega',
    'hm_psi',
    'if_sara',
    'im_nicola',
    'jf_alpha',
    'jf_gongitsune',
    'jf_nezumi',
    'jf_tebukuro',
    'jm_kumo',
    'pf_dora',
    'pm_alex',
    'pm_santa',
    'zf_xiaobei',
    'zf_xiaoni',
    'zf_xiaoxiao',
    'zf_xiaoyi',
    'zm_yunjian',
    'zm_yunxi',
    'zm_yunxia',
    'zm_yunyang'
]


LANGUAGE_TO_FLAG = {
    "en-us": "🇺🇸",
    "en-gb": "🇬🇧",
    "fr-fr": "🇫🇷",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "cmn": "🇨🇳",
    "es": "🇪🇸",
    "hi": "🇮🇳",
    "it": "🇮🇹",
    "pt-br": "🇧🇷"
}


_PREFIX_TO_LANGUAGE = {
    'a': 'en-us', 'b': 'en-gb', 'e': 'es', 'f': 'fr-fr',
    'h': 'hi', 'i': 'it', 'j': 'ja', 'p': 'pt-br', 'z': 'cmn',
}


def get_language_from_voice(voice):
    try:
        return _PREFIX_TO_LANGUAGE[voice[0]]
    except (KeyError, IndexError):
        print(f"Warning: unknown voice prefix {voice!r}, defaulting to en-us",
              file=sys.stderr)
        return 'en-us'


def emojify_voice(voice):
    language = get_language_from_voice(voice)
    if language in LANGUAGE_TO_FLAG:
        return LANGUAGE_TO_FLAG[language] + " " + voice
    return voice


def deemojify_voice(voice):
    if voice[:2] in LANGUAGE_TO_FLAG.values():
        return voice[3:]
    return voice


voices = list(voices_internal)
voices_emojified = [emojify_voice(x) for x in voices]
