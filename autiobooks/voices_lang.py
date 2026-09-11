import re
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


def get_kokoro_lang_code(voice):
    """Return the single-char Kokoro `lang_code` (a KPipeline `LANG_CODES`
    key, e.g. 'a', 'b', 'z') for `voice`, derived from its first character.

    This is the ONE place that decides whether a voice's prefix is valid —
    get_language_from_voice is defined in terms of it below, so the two
    can no longer disagree about a voice's language. Previously
    get_language_from_voice tolerated an unrecognized prefix and fell back
    to en-us while engine.gen_audio_segments fed KPipeline the raw,
    unvalidated `voice[0]` directly, so an unrecognized prefix (`steve.pt`
    -> 's') hard-crashed inside KPipeline's `assert lang_code in
    LANG_CODES` — a bare tuple dump, repeated once per chapter, that names
    neither the offending voice file nor what a valid prefix looks like.

    Raises ValueError with a message naming the voice and the expected
    prefix set instead.
    """
    prefix = voice[0] if voice else ''
    if prefix not in _PREFIX_TO_LANGUAGE:
        raise ValueError(
            f"Voice {voice!r} has an unrecognized language prefix "
            f"{prefix!r}; expected one of {sorted(_PREFIX_TO_LANGUAGE)}.")
    return prefix


def get_language_from_voice(voice):
    try:
        return _PREFIX_TO_LANGUAGE[get_kokoro_lang_code(voice)]
    except (ValueError, IndexError):
        print(f"Warning: unknown voice prefix {voice!r}, defaulting to en-us",
              file=sys.stderr)
        return 'en-us'


_CUSTOM_PREFIX = '✨ '


def emojify_voice(voice):
    # Custom voices win name collisions with built-ins (resolve_voice checks
    # the custom dir first), so the sparkle must be based on membership in
    # the discovered custom set, not merely `voice not in voices_internal` —
    # that check is always False for a colliding name even though the custom
    # tensor is what actually gets loaded.
    sparkle = _CUSTOM_PREFIX if voice in discover_custom_voices() else ''
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


# Kokoro's KPipeline and get_kokoro_lang_code both derive a voice's
# language from ONLY its first character, so a bare stem like `steve.pt`
# (prefix 's', not a valid Kokoro lang_code) crashes with a bare assertion
# tuple repeated once per chapter, and — worse — a bare stem like
# `zoe.pt`/`jim.pt` (prefix 'z'/'j', which ARE valid Kokoro lang_codes) is
# silently read as Mandarin/Japanese: no crash, no warning, wrong language
# for the whole book. Requiring an explicit `<lang>[<gender>]_` prefix
# turns both failure modes into a same-glob, named rejection. The gender
# letter is deliberately OPTIONAL — only voice[0] is ever consulted by
# either code path, so `a_custom.pt` already resolves correctly today
# (lang_code 'a'), and requiring a gender letter would reject a stem that
# works and isn't ambiguous.
_CUSTOM_VOICE_STEM_RE = re.compile(r'^[abefhijpz][fm]?_.+$')


def _is_valid_custom_voice_stem(stem):
    return bool(_CUSTOM_VOICE_STEM_RE.match(stem))


def discover_custom_voices():
    """Return sorted list of voice names found as `*.pt` in the voices dir.

    Stems that don't match `<lang>[<gender>]_<name>` (see
    _CUSTOM_VOICE_STEM_RE above) are excluded — reported to stderr rather
    than silently dropped or silently mis-synthesized in an unintended
    language — so they never reach the dropdown or CLI --voice validation.
    """
    d = get_voices_dir()
    try:
        if not d.is_dir():
            return []
        stems = sorted(p.stem for p in d.glob('*.pt') if p.is_file())
    except OSError:
        return []
    valid = []
    for stem in stems:
        if _is_valid_custom_voice_stem(stem):
            valid.append(stem)
        else:
            print(
                f"Warning: custom voice '{stem}' must be named "
                f"<lang><gender>_<name>.pt (e.g. am_steve.pt) — skipping "
                f"(found in {d}).", file=sys.stderr)
    return valid


def get_custom_voice_stat(name):
    """Return (mtime_ns, size) for `name`'s `.pt` file, or None if `name`
    isn't backed by a stat-able custom voice file right now.

    Used by engine.render_key to fold tensor identity into the resume WAV
    cache key — the same root cause as the in-process tensor cache going
    stale (see _load_custom_tensor): a kvoicewalk re-run overwrites a
    `.pt` in place with the SAME name, so a name-only key can't tell the
    old and new tensor apart and a resume after that re-run splices the
    discarded voice's audio into the new run. A built-in voice name (no
    matching file in the voices dir) returns None so render_key can omit
    this component entirely for it — the resume key for built-in voices
    must stay byte-identical to before this was added. Any stat failure
    (missing file, permission error, etc.) also returns None rather than
    raising, so a conversion never crashes over this — the real
    FileNotFoundError still surfaces later, from resolve_voice, when the
    voice is actually loaded for synthesis.
    """
    path = get_voices_dir() / f'{name}.pt'
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def resolve_voice(name):
    """Return a value suitable as KPipeline's `voice` argument.

    For built-in names we return the string and let Kokoro download/lookup.
    For custom names we load the `.pt` tensor (cached, CPU, float32) so
    Kokoro's `load_voice` short-circuits on the FloatTensor branch.

    A custom voice always wins a name collision with a built-in — checked
    first, so a user's own `bm_daniel.pt` shadows the stock `bm_daniel`
    rather than being silently defeated by it.
    """
    if name in discover_custom_voices():
        return _load_custom_tensor(name)
    if name in voices_internal:
        return name
    return _load_custom_tensor(name)


def _load_custom_tensor(name):
    import torch  # lazy: keep this module importable without torch
    path = get_voices_dir() / f'{name}.pt'
    # stat() BEFORE the cache read so a missing file raises here instead of
    # a stale cache hit masking it, and so the cache key folds in mtime/size
    # — a kvoicewalk re-run overwriting the .pt in place used to return the
    # discarded tensor forever (t2 is t1) even after refresh_voices().
    try:
        st = path.stat()
    except OSError:
        raise FileNotFoundError(f'Custom voice not found: {path}') from None
    cache_key = (str(path), st.st_mtime_ns, st.st_size)
    with _voice_tensor_lock:
        cached = _voice_tensor_cache.get(cache_key)
    if cached is not None:
        return cached
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
    if tensor.ndim != 3 or tensor.shape[1] != 1 or tensor.shape[-1] != 256:
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
    # dict.fromkeys dedupes so a custom voice colliding with a built-in name
    # appears once in the list (still resolves/emojifies as the custom one).
    voices = list(dict.fromkeys(list(voices_internal) + discover_custom_voices()))
    voices_emojified = [emojify_voice(x) for x in voices]


voices = list(dict.fromkeys(list(voices_internal) + discover_custom_voices()))
voices_emojified = [emojify_voice(x) for x in voices]
