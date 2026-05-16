import sys
import threading
from pathlib import Path


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


_CUSTOM_PREFIX = '✨ '


def emojify_voice(voice):
    sparkle = _CUSTOM_PREFIX if voice not in voices_internal else ''
    language = get_language_from_voice(voice)
    if language in LANGUAGE_TO_FLAG:
        return sparkle + LANGUAGE_TO_FLAG[language] + " " + voice
    return sparkle + voice


def deemojify_voice(voice):
    if voice.startswith(_CUSTOM_PREFIX):
        voice = voice[len(_CUSTOM_PREFIX):]
    if voice[:2] in LANGUAGE_TO_FLAG.values():
        return voice[3:]
    return voice


_voices_dir_override = None
_voice_tensor_cache = {}
_voice_tensor_lock = threading.Lock()


def get_voices_dir():
    """Directory where user-supplied .pt voice tensors live."""
    if _voices_dir_override is not None:
        return Path(_voices_dir_override)
    return Path.home() / '.autiobooks' / 'voices'


def set_voices_dir(path):
    """Override the voices directory (used by tests)."""
    global _voices_dir_override
    _voices_dir_override = path
    clear_voice_cache()


def discover_custom_voices():
    """Return sorted list of voice names found as `*.pt` in the voices dir."""
    d = get_voices_dir()
    try:
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob('*.pt') if p.is_file())
    except OSError:
        return []


def is_custom_voice(name):
    return name not in voices_internal


def resolve_voice(name):
    """Return a value suitable as KPipeline's `voice` argument.

    For built-in names we return the string and let Kokoro download/lookup.
    For custom names we load the `.pt` tensor (cached, CPU, float32) so
    Kokoro's `load_voice` short-circuits on the FloatTensor branch.
    """
    if name in voices_internal:
        return name
    return _load_custom_tensor(name)


def _load_custom_tensor(name):
    import torch  # lazy: keep this module importable without torch
    path = get_voices_dir() / f'{name}.pt'
    cache_key = str(path)
    with _voice_tensor_lock:
        cached = _voice_tensor_cache.get(cache_key)
    if cached is not None:
        return cached
    if not path.is_file():
        raise FileNotFoundError(f'Custom voice not found: {path}')
    try:
        loaded = torch.load(path, weights_only=True, map_location='cpu')
    except Exception as e:
        raise RuntimeError(
            f'Failed to load custom voice {path.name}: {e}') from e
    if not isinstance(loaded, torch.Tensor):
        raise ValueError(
            f'Voice file {path.name} did not contain a tensor '
            f'(got {type(loaded).__name__}).')
    tensor = loaded.detach().to(dtype=torch.float32, device='cpu')
    # Kokoro voice packs are shape (N, 1, 256). Sanity-check up front so
    # the error message names the file instead of crashing inside synthesis.
    if tensor.ndim != 3 or tensor.shape[-1] != 256:
        raise ValueError(
            f'Voice file {path.name} has unexpected shape '
            f'{tuple(tensor.shape)}; expected (N, 1, 256).')
    with _voice_tensor_lock:
        _voice_tensor_cache[cache_key] = tensor
    return tensor


def clear_voice_cache():
    with _voice_tensor_lock:
        _voice_tensor_cache.clear()


def refresh_voices():
    """Rebuild module-level `voices` / `voices_emojified` from disk."""
    global voices, voices_emojified
    voices = list(voices_internal) + discover_custom_voices()
    voices_emojified = [emojify_voice(x) for x in voices]


voices = list(voices_internal) + discover_custom_voices()
voices_emojified = [emojify_voice(x) for x in voices]
