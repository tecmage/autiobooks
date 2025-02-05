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


def get_language_from_voice(voice):
    if voice.startswith("a"):
        return "en-us"
    elif voice.startswith("b"):
        return "en-gb"
    elif voice.startswith("e"):
        return "es"
    elif voice.startswith("f"):
        return "fr-fr"
    elif voice.startswith("h"):
        return "hi"
    elif voice.startswith("i"):
        return "it"
    elif voice.startswith("j"):
        return "ja"
    elif voice.startswith("p"):
        return "pt-br"
    elif voice.startswith("z"):
        return "cmn"
    else:
        print("Voice not recognized.")
        exit(1)


def emojify_voice(voice):
    language = get_language_from_voice(voice)
    if language in LANGUAGE_TO_FLAG:
        return LANGUAGE_TO_FLAG[language] + " " + voice
    return voice


def deemojify_voice(voice):
    if voice[:2] in LANGUAGE_TO_FLAG.values():
        return voice[3:]
    return voice


# filter out non-english voices (they're not working yet)
voices = [x for x in voices_internal if x.startswith("a") or x.startswith("b")]
voices_emojified = [emojify_voice(x) for x in voices]
