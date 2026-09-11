import pytest

from autiobooks.text_processing import (
    normalize_unicode,
    strip_diacritics,
    expand_abbreviations,
    expand_roman_numerals,
    clean_special_characters,
    apply_substitutions,
    apply_phoneme_overrides,
    apply_builtin_phoneme_overrides,
    apply_acronym_spellout,
    apply_contextual_overrides,
    normalize_text,
    _to_misaki_phonemes,
    HETERONYMS,
    FRACTION_REPLACEMENTS,
    HAS_SPACY,
)


# ---------------------------------------------------------------------------
# 1. normalize_unicode
# ---------------------------------------------------------------------------

class TestNormalizeUnicode:
    """Tests for normalize_unicode(text, is_english=True)."""

    # Smart quotes -> straight quotes
    def test_left_double_quote(self):
        assert normalize_unicode("\u201cHello\u201d") == '"Hello"'

    def test_left_single_quote(self):
        assert normalize_unicode("\u2018it\u2019s") == "'it's"

    # Ellipsis
    def test_ellipsis(self):
        assert normalize_unicode("wait\u2026") == "wait..."

    # Non-breaking space
    def test_non_breaking_space(self):
        assert normalize_unicode("hello\u00a0world") == "hello world"

    # Characters that should be removed
    def test_soft_hyphen_removed(self):
        assert normalize_unicode("hy\u00adphen") == "hyphen"

    def test_zero_width_space_removed(self):
        assert normalize_unicode("zero\u200bwidth") == "zerowidth"

    def test_bom_removed(self):
        assert normalize_unicode("\ufeffstart") == "start"

    # Ligatures
    def test_fi_ligature(self):
        assert normalize_unicode("\ufb01nd") == "find"

    def test_fl_ligature(self):
        assert normalize_unicode("\ufb02ow") == "flow"

    def test_ff_ligature(self):
        assert normalize_unicode("o\ufb00") == "off"

    def test_ffi_ligature(self):
        assert normalize_unicode("o\ufb03ce") == "office"

    def test_ffl_ligature(self):
        assert normalize_unicode("ba\ufb04e") == "baffle"

    # Em-dash
    def test_em_dash(self):
        assert normalize_unicode("word\u2014another") == "word, another"

    # En-dash between numbers (English only)
    def test_en_dash_between_numbers_english(self):
        result = normalize_unicode("10\u201320", is_english=True)
        assert result == "10 to 20"

    def test_en_dash_between_numbers_non_english(self):
        result = normalize_unicode("10\u201320", is_english=False)
        assert result == "10 - 20"

    # En-dash elsewhere
    def test_en_dash_not_between_numbers(self):
        result = normalize_unicode("word\u2013word")
        assert result == "word - word"

    # Superscript digits
    def test_superscript_digits(self):
        text = "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
        assert normalize_unicode(text) == "0123456789"

    # Prime and double prime
    def test_prime_to_apostrophe(self):
        assert normalize_unicode("5\u2032") == "5'"

    def test_double_prime_to_quote(self):
        assert normalize_unicode("5\u2033") == '5"'

    # Low-9 quotation marks
    def test_single_low_9_quote(self):
        assert normalize_unicode("\u201a") == ","

    def test_double_low_9_quote(self):
        assert normalize_unicode("\u201e") == '"'

    # strip_diacritics is called inside normalize_unicode for English only
    def test_diacritics_stripped_english(self):
        result = normalize_unicode("caf\u00e9", is_english=True)
        assert result == "cafe"

    def test_diacritics_preserved_non_english(self):
        result = normalize_unicode("caf\u00e9", is_english=False)
        assert result == "caf\u00e9"

    def test_spanish_tilde_preserved(self):
        # a\u00f1o (year) must not become "ano" for Spanish voices
        assert normalize_unicode("a\u00f1o", is_english=False) == "a\u00f1o"


# ---------------------------------------------------------------------------
# 2. strip_diacritics
# ---------------------------------------------------------------------------

class TestStripDiacritics:
    """Tests for strip_diacritics(text)."""

    def test_cafe(self):
        assert strip_diacritics("caf\u00e9") == "cafe"

    def test_naive(self):
        assert strip_diacritics("na\u00efve") == "naive"

    def test_resume(self):
        assert strip_diacritics("r\u00e9sum\u00e9") == "resume"

    def test_uber(self):
        assert strip_diacritics("\u00fcber") == "uber"

    def test_plain_ascii_unchanged(self):
        assert strip_diacritics("hello world") == "hello world"

    def test_mixed_text(self):
        assert strip_diacritics("The caf\u00e9 r\u00e9sum\u00e9") == "The cafe resume"


# ---------------------------------------------------------------------------
# 2b. r\u00e9sum\u00e9 (CV/noun) preservation through normalize_unicode
# ---------------------------------------------------------------------------

class TestResumeNounPreservation:
    """`r\u00e9sum\u00e9` (with at least one accent) must be tagged as the noun IPA
    BEFORE strip_diacritics destroys the cue. Plain `resume` is left alone
    and inherits misaki gold's verb pronunciation."""

    def test_accented_singular_wrapped(self):
        # Lowercase 'r' in source \u2192 lowercase 'r' in display text (the
        # wrapper preserves the original case).
        out = normalize_unicode("Her r\u00e9sum\u00e9 impressed.")
        assert "[resume](/\u02c8\u0279\u025bz\u0259mA/)" in out

    def test_capitalized_accented_wrapped(self):
        # Title-cased R\u00e9sum\u00e9 (e.g. at sentence start) keeps its capital R.
        out = normalize_unicode("R\u00e9sum\u00e9 writing is hard.")
        assert "[Resume](/\u02c8\u0279\u025bz\u0259mA/)" in out

    def test_accented_plural_wrapped(self):
        out = normalize_unicode("A stack of r\u00e9sum\u00e9s on her desk.")
        assert "[resumes](/\u02c8\u0279\u025bz\u0259mAz/)" in out

    def test_only_second_accent(self):
        # `resum\u00e9` (acute only on the final e) \u2014 common ASCII-keyboard fallback.
        out = normalize_unicode("Her resum\u00e9 was perfect.")
        assert "[resume](/" in out

    def test_only_first_accent(self):
        # `r\u00e9sume` \u2014 rare but possible OCR variant.
        out = normalize_unicode("Her r\u00e9sume was perfect.")
        assert "[resume](/" in out

    def test_plain_resume_untouched(self):
        # No accents anywhere \u2014 must stay bare for misaki's verb default.
        out = normalize_unicode("She will resume reading.")
        assert "[Resume" not in out
        assert "[resume" not in out
        assert "resume" in out

    def test_resumed_resuming_untouched(self):
        # Verb inflections \u2014 never the noun, must not match the regex.
        out = normalize_unicode("He resumed work. She is resuming now.")
        assert "[Resume" not in out
        assert "resumed" in out and "resuming" in out

    def test_non_english_leaves_accents(self):
        # is_english=False skips strip_diacritics AND the noun preservation,
        # because the surrounding text expects to keep accents for foreign G2P.
        out = normalize_unicode("Su r\u00e9sum\u00e9 es bueno.", is_english=False)
        assert "r\u00e9sum\u00e9" in out
        assert "[Resume" not in out

    def test_survives_full_pipeline(self):
        # End-to-end: through normalize_text the markup must survive every pass
        # and reach the final string intact.
        out = normalize_text("Her r\u00e9sum\u00e9 was strong.")
        assert "[resume](/\u02c8\u0279\u025bz\u0259mA/)" in out


# ---------------------------------------------------------------------------
# 3. Fraction expansion (in normalize_unicode, English only)
# ---------------------------------------------------------------------------

class TestFractionExpansion:
    """Tests for fraction characters expanded in normalize_unicode (English)."""

    def test_one_half(self):
        result = normalize_unicode("\u00bd")
        assert "one half" in result

    def test_one_quarter(self):
        result = normalize_unicode("\u00bc")
        assert "one quarter" in result

    def test_three_quarters(self):
        result = normalize_unicode("\u00be")
        assert "three quarters" in result

    def test_one_third(self):
        result = normalize_unicode("\u2153")
        assert "one third" in result

    def test_fraction_in_context(self):
        result = normalize_unicode("2\u00bd cups")
        assert "one half" in result

    def test_fractions_not_expanded_non_english(self):
        result = normalize_unicode("\u00bd", is_english=False)
        # The fraction character should remain (not expanded to English words)
        assert "one half" not in result

    def test_fraction_replacements_dict_populated(self):
        assert len(FRACTION_REPLACEMENTS) > 0
        assert "\u00bd" in FRACTION_REPLACEMENTS


# ---------------------------------------------------------------------------
# 4. expand_abbreviations
# ---------------------------------------------------------------------------

class TestExpandAbbreviations:
    """Tests for expand_abbreviations(text)."""

    def test_mr(self):
        assert expand_abbreviations("Mr. Smith") == "Mister Smith"

    def test_dr(self):
        assert expand_abbreviations("Dr. Jones") == "Doctor Jones"

    def test_etc(self):
        # Sentence-final "etc." keeps a period so the TTS pause survives.
        assert expand_abbreviations("and etc.") == "and et cetera."
        # Mid-sentence "etc." (followed by lowercase) does not gain a period.
        assert expand_abbreviations("etc. and so on") == "et cetera and so on"

    def test_eg(self):
        assert expand_abbreviations("e.g. this") == "for example this"

    def test_ie(self):
        assert expand_abbreviations("i.e. that") == "that is that"

    def test_st_saint_sentence_start(self):
        assert expand_abbreviations("St. Louis") == "Saint Louis"
        assert expand_abbreviations("St. Patrick was here.") == "Saint Patrick was here."

    def test_st_saint_after_lowercase(self):
        assert expand_abbreviations("downtown St. Louis") == "downtown Saint Louis"
        assert expand_abbreviations("in St. Louis, Missouri") == "in Saint Louis, Missouri"

    def test_st_street_after_capital(self):
        # End of text is a sentence boundary, so the terminator is kept
        # (audit §1.3). Mid-sentence cases below stay bare.
        assert expand_abbreviations("Main St.") == "Main Street."
        assert expand_abbreviations("Wall St. closed early.") == "Wall Street closed early."
        assert expand_abbreviations("He lives on Main St. now.") == "He lives on Main Street now."

    def test_st_street_ordinal(self):
        assert expand_abbreviations("42nd St. is busy.") == "42nd Street is busy."

    def test_st_place_prefix(self):
        assert expand_abbreviations("Mount St. Helens") == "Mount Saint Helens"

    def test_st_title_prefix_via_dr(self):
        # 'Dr.' is expanded to 'Doctor' first (dict loop), then St. resolves to Saint.
        assert expand_abbreviations("Dr. St. James") == "Doctor Saint James"

    def test_st_royal_title(self):
        assert expand_abbreviations("King St. Louis IX") == "King Saint Louis IX"

    def test_st_at_end(self):
        # "St." at end of text IS the sentence terminator, so 'Street' keeps
        # the period (audit §1.3). This test previously pinned the opposite —
        # it characterized the swallowed-terminator bug, not desired output.
        assert expand_abbreviations("He lives on Main St.") == "He lives on Main Street."

    # Military abbreviations
    def test_maj(self):
        assert expand_abbreviations("Maj. Smith") == "Major Smith"

    def test_pvt(self):
        assert expand_abbreviations("Pvt. Jones") == "Private Jones"

    # Geographic abbreviations
    def test_mt(self):
        assert expand_abbreviations("Mt. Everest") == "Mount Everest"

    def test_ft(self):
        assert expand_abbreviations("Ft. Worth") == "Fort Worth"

    # Publishing abbreviations
    def test_vol(self):
        assert expand_abbreviations("Vol. 2") == "Volume 2"

    def test_ch(self):
        assert expand_abbreviations("Ch. 3") == "Chapter 3"

    def test_no_dot_with_digit(self):
        assert expand_abbreviations("Lot No. 5 was selected.") == "Lot Number 5 was selected."
        assert expand_abbreviations("Issue No. 42") == "Issue Number 42"

    def test_no_dot_one_word_reply(self):
        # 'No.' as a one-word reply must stay as 'No' — not 'Number'.
        assert expand_abbreviations("She nodded. No. Not yet.") == "She nodded. No. Not yet."
        assert expand_abbreviations("He said: No. I refused.") == "He said: No. I refused."

    def test_no_mid_word_match(self):
        # "Mister" should not be re-expanded or mangled
        result = expand_abbreviations("Mister Smith")
        assert result == "Mister Smith"

    def test_abbreviation_at_end_of_text(self):
        result = expand_abbreviations("See the Dr.")
        assert result == "See the Doctor"


# ---------------------------------------------------------------------------
# 5. expand_roman_numerals
# ---------------------------------------------------------------------------

class TestExpandRomanNumerals:
    """Tests for expand_roman_numerals(text)."""

    def test_chapter_iv(self):
        assert expand_roman_numerals("Chapter IV") == "Chapter 4"

    def test_part_xii(self):
        assert expand_roman_numerals("Part XII") == "Part 12"

    def test_volume_iii(self):
        assert expand_roman_numerals("Volume III") == "Volume 3"

    def test_act_i(self):
        assert expand_roman_numerals("Act I") == "Act 1"

    def test_scene_ii(self):
        assert expand_roman_numerals("Scene II") == "Scene 2"

    def test_case_insensitive(self):
        assert expand_roman_numerals("chapter iv") == "chapter 4"

    def test_no_standalone_roman_numeral(self):
        # Without a keyword, Roman numerals should NOT be expanded
        result = expand_roman_numerals("I went home")
        assert result == "I went home"

    def test_book_mcmxcix(self):
        assert expand_roman_numerals("Book MCMXCIX") == "Book 1999"

    def test_section_keyword(self):
        assert expand_roman_numerals("Section VII") == "Section 7"

    def test_appendix_keyword(self):
        assert expand_roman_numerals("Appendix IX") == "Appendix 9"


# ---------------------------------------------------------------------------
# 5b. resolve_contractions (requires spaCy)
# ---------------------------------------------------------------------------

class TestResolveContractions:
    """Tests for resolve_contractions(text). Skipped if spaCy is unavailable.

    The disambiguation hinges on the tag spaCy assigns to the 's/'d token
    itself: a genuine contraction's 's is VBZ and 'd is MD, while a
    possessive 's is POS. Expanding a possessive corrupts noun phrases
    ("the book's torn pages" -> "the book has torn pages").
    """

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_genuine_s_is_expands(self):
        from autiobooks.text_processing import resolve_contractions
        assert resolve_contractions("He's running late.") == "He is running late."

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_genuine_s_has_expands(self):
        from autiobooks.text_processing import resolve_contractions
        assert resolve_contractions("She's gone home.") == "She has gone home."

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_genuine_d_had_expands(self):
        from autiobooks.text_processing import resolve_contractions
        assert resolve_contractions("He'd seen it.") == "He had seen it."

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_genuine_d_would_expands(self):
        from autiobooks.text_processing import resolve_contractions
        assert resolve_contractions("He'd go there.") == "He would go there."

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_possessive_before_past_participle_untouched(self):
        from autiobooks.text_processing import resolve_contractions
        # Regression: used to become "the book has torn pages".
        assert resolve_contractions("the book's torn pages") == "the book's torn pages"

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_possessive_before_gerund_untouched(self):
        from autiobooks.text_processing import resolve_contractions
        # Regression: used to become "the city is growing population".
        assert (resolve_contractions("the city's growing population")
                == "the city's growing population")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_lets_untouched(self):
        from autiobooks.text_processing import resolve_contractions
        assert resolve_contractions("let's go") == "let's go"


# ---------------------------------------------------------------------------
# 6. resolve_heteronyms (requires spaCy)
# ---------------------------------------------------------------------------

class TestResolveHeteronyms:
    """Tests for resolve_heteronyms(text). Skipped if spaCy is unavailable."""

    def test_heteronyms_dict_contains_lead(self):
        assert "lead" in HETERONYMS

    def test_heteronyms_dict_does_not_contain_read(self):
        # `read` is handled natively by misaki's POS-aware gold lexicon
        # (VBD/VBN/VBP/ADJ → /ɹɛd/, DEFAULT → /ɹid/); the legacy respelling
        # would mis-pronounce past-tense cases that spaCy tags VBP.
        assert "read" not in HETERONYMS

    def test_heteronyms_dict_does_not_contain_wind(self):
        assert "wind" not in HETERONYMS

    def test_heteronyms_dict_does_not_contain_tear(self):
        assert "tear" not in HETERONYMS

    def test_heteronyms_dict_does_not_contain_wound(self):
        assert "wound" not in HETERONYMS

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_resolve_returns_string(self):
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("She will lead the team.")
        assert isinstance(result, str)

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_resolve_without_spacy_passthrough(self):
        # When spaCy IS available, the function should still handle plain text
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("Hello world")
        assert result == "Hello world"

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_lead_present_tense_respelled_to_leed(self):
        # Non-VBD/VBN tags fall to the 'present' branch; 'leed' is a hint
        # spelling, not a real word — misaki maps it to /lid/ via espeak.
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("She will lead the team.")
        assert "leed the team" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_non_heteronym_sentence_unchanged(self):
        from autiobooks.text_processing import resolve_heteronyms
        sentence = "The dog barked loudly at the mailman."
        assert resolve_heteronyms(sentence) == sentence

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_lead_inside_markdown_preserved(self):
        # apply_contextual_overrides may wrap the metal-sense `lead` as
        # `[lead](/lˈɛd/)` before resolve_heteronyms runs. The bracket
        # display text must not be mutated. Defensive: current spaCy
        # tokenization already protects this (the inner `lead` isn't a
        # standalone token), but the explicit `_is_inside_markdown` guard
        # in resolve_heteronyms ensures correctness even if tokenization
        # changes upstream.
        from autiobooks.text_processing import resolve_heteronyms
        pre_wrapped = "He was poisoned by [lead](/lˈɛd/) paint."
        result = resolve_heteronyms(pre_wrapped)
        assert "[lead](/lˈɛd/)" in result
        assert "[leed]" not in result
        assert "[led]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_pipeline_preserves_lead_markdown(self):
        # End-to-end: contextual override emits markdown, then resolve
        # respells. The bracket text must survive both passes.
        from autiobooks.text_processing import (
            apply_contextual_overrides, resolve_heteronyms)
        text = "He was poisoned by lead paint."
        result = resolve_heteronyms(apply_contextual_overrides(text))
        assert "[lead](/lˈɛd/)" in result
        assert "[leed]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_multiple_leads_in_one_sentence(self):
        # Defensive: when multiple `lead` tokens appear, the function
        # processes each independently in reverse-index order so earlier
        # replacements don't shift later token offsets. Pins current
        # behavior so any future loop refactor is caught.
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("They lead the lead miners.")
        assert isinstance(result, str)
        assert "miners" in result
        # Both tokens respelled (verb VBP and adjective JJ both fall to
        # the 'present' branch). The contextual `_lead_rule` is a separate
        # pass that wraps material-sense lead before this function runs;
        # this test exercises resolve_heteronyms in isolation.
        assert result.count("leed") == 2


# ---------------------------------------------------------------------------
# 6a. _to_misaki_phonemes — diphthong folding for Kokoro's vocab
# ---------------------------------------------------------------------------

class TestToMisakiPhonemes:
    """Kokoro's phoneme vocab maps the five English diphthongs to single
    letters (A=eɪ, I=aɪ, O=oʊ, W=aʊ, Y=ɔɪ) plus Q=əʊ for GB. Anything we hand
    Kokoro has to be folded into that alphabet — sending raw `aʊ` makes the
    model read it as two unrelated phonemes."""

    def test_aw_diphthong(self):
        assert _to_misaki_phonemes("bˈaʊd") == "bˈWd"

    def test_ay_diphthong(self):
        assert _to_misaki_phonemes("maɪˈnut") == "mIˈnut"

    def test_ey_diphthong(self):
        assert _to_misaki_phonemes("bˈeɪs") == "bˈAs"

    def test_ow_diphthong(self):
        assert _to_misaki_phonemes("ɡoʊ") == "ɡO"

    def test_oy_diphthong(self):
        assert _to_misaki_phonemes("bˈɔɪ") == "bˈY"

    def test_gb_eu_diphthong(self):
        assert _to_misaki_phonemes("ɡəʊ") == "ɡQ"

    def test_idempotent(self):
        # Running twice must not double-substitute; misaki letters have no
        # canonical-IPA two-char sequences hiding inside them.
        once = _to_misaki_phonemes("bˈaʊd maɪˈnut")
        assert _to_misaki_phonemes(once) == once

    def test_passes_through_monophthongs(self):
        # Consonants, monophthongs, schwas, stress marks must all survive.
        assert _to_misaki_phonemes("kənˈtɛnt") == "kənˈtɛnt"
        assert _to_misaki_phonemes("lˈɛd") == "lˈɛd"
        assert _to_misaki_phonemes("tˈɪɹɪŋ") == "tˈɪɹɪŋ"

    def test_ascii_g_folds_to_ipa_g(self):
        # Audit §1.2: Kokoro's vocab has no ASCII-g (U+0067) key, only the
        # IPA 'ɡ' (U+0261) that us_gold uses exclusively — a dictionary-typed
        # override ('ˈgɜːtə', typed on a keyboard) must fold or the /g/
        # silently drops from the audio.
        assert _to_misaki_phonemes("ˈgɜːtə") == "ˈɡɜːtə"
        assert "g" not in _to_misaki_phonemes("ˈgɜːtə")

    def test_ascii_g_fold_idempotent(self):
        once = _to_misaki_phonemes("ˈgɜːtə")
        assert _to_misaki_phonemes(once) == once


# ---------------------------------------------------------------------------
# 6b. apply_contextual_overrides (requires spaCy)
# ---------------------------------------------------------------------------

class TestContextualHeteronyms:
    """Tests for apply_contextual_overrides(text). Emits `[word](/IPA/)`
    markdown when collocation cues demand a non-default pronunciation that
    misaki's POS-branching gold can't pick from POS alone."""

    def test_no_spacy_passthrough(self):
        # Returns input unchanged if spaCy isn't importable; the lazy loader
        # path returns None when the model isn't installed, also a passthrough.
        result = apply_contextual_overrides("Hello world")
        assert isinstance(result, str)

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_gesture_triggers_aw(self):
        # Diphthong /aʊ/ folds to misaki's single-letter `W` — see
        # _to_misaki_phonemes for why Kokoro requires that alphabet.
        result = apply_contextual_overrides("She took a bow after the show.")
        assert "[bow](/bˈW/)" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_archery_left_alone(self):
        result = apply_contextual_overrides("He drew the bow and fired an arrow.")
        assert "[bow]" not in result
        assert "bow" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_predicate_triggers_schwa(self):
        result = apply_contextual_overrides("She felt content with her life.")
        assert "[content](/kənˈtɛnt/)" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_noun_left_alone(self):
        result = apply_contextual_overrides("The book had useful content inside.")
        assert "[content]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_minute_adjective_triggers_diphthong(self):
        result = apply_contextual_overrides("Every minute detail was examined.")
        assert "[minute](/mIˈnut/)" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_minute_time_unit_left_alone(self):
        result = apply_contextual_overrides("Wait one minute, please.")
        assert "[minute]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_lead_material_triggers_eh(self):
        result = apply_contextual_overrides("He was poisoned by lead paint.")
        assert "[lead](/lˈɛd/)" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_lead_verb_left_alone(self):
        result = apply_contextual_overrides("She will lead the team.")
        assert "[lead]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bass_instrument_triggers_ey(self):
        result = apply_contextual_overrides("He plays the bass in a jazz band.")
        assert "[bass](/bˈAs/)" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bass_fish_left_alone(self):
        result = apply_contextual_overrides("The bass swam near the dock.")
        assert "[bass]" not in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_skips_already_wrapped_tokens(self):
        # Text pre-wrapped by phoneme_overrides or a prior pass: contextual
        # rule must not double-wrap.
        result = apply_contextual_overrides(
            "She felt [content](/kˈɑntɛnt/) with her life."
        )
        assert result.count("[content]") == 1


# ---------------------------------------------------------------------------
# 7. clean_special_characters
# ---------------------------------------------------------------------------

class TestCleanSpecialCharacters:
    """Tests for clean_special_characters(text, is_english=True)."""

    def test_url_removed(self):
        result = clean_special_characters("Visit https://example.com for info")
        assert "https://example.com" not in result
        assert "Visit" in result

    def test_email_removed(self):
        result = clean_special_characters("Email user@example.com today")
        assert "user@example.com" not in result
        assert "Email" in result

    def test_scene_break_asterisks(self):
        result = clean_special_characters("before ***  after")
        assert "***" not in result

    def test_scene_break_dashes(self):
        result = clean_special_characters("before --- after")
        assert "---" not in result

    def test_scene_break_equals(self):
        result = clean_special_characters("before === after")
        assert "===" not in result

    def test_scene_break_tildes(self):
        result = clean_special_characters("before ~~~ after")
        assert "~~~" not in result

    def test_ampersand_english(self):
        result = clean_special_characters("Tom & Jerry", is_english=True)
        assert "and" in result

    def test_ampersand_non_english(self):
        result = clean_special_characters("Tom & Jerry", is_english=False)
        assert "and" not in result

    def test_copyright_english(self):
        result = clean_special_characters("\u00a9 2024", is_english=True)
        assert "copyright" in result

    def test_section_english(self):
        result = clean_special_characters("\u00a7 5", is_english=True)
        assert "section" in result

    def test_infinity_english(self):
        result = clean_special_characters("\u221e", is_english=True)
        assert "infinity" in result

    def test_approximately_equal_english(self):
        result = clean_special_characters("\u2248", is_english=True)
        assert "approximately equal to" in result

    def test_pilcrow_removed(self):
        result = clean_special_characters("text\u00b6more")
        assert "\u00b6" not in result

    def test_dagger_removed(self):
        result = clean_special_characters("note\u2020")
        assert "\u2020" not in result

    def test_double_dagger_removed(self):
        result = clean_special_characters("note\u2021")
        assert "\u2021" not in result

    def test_arrows_to_space(self):
        result = clean_special_characters("\u2192go")
        assert "\u2192" not in result

    def test_stars_to_space(self):
        result = clean_special_characters("\u2605rating")
        assert "\u2605" not in result

    def test_multiple_spaces_collapsed(self):
        result = clean_special_characters("hello    world")
        assert "  " not in result
        assert "hello world" in result

    def test_three_plus_newlines_collapsed(self):
        result = clean_special_characters("a\n\n\n\nb")
        assert "\n\n\n" not in result
        assert "a\n\nb" == result

    def test_non_english_symbols_become_space(self):
        result = clean_special_characters("\u00a9 2024", is_english=False)
        assert "copyright" not in result


# ---------------------------------------------------------------------------
# 8. apply_substitutions
# ---------------------------------------------------------------------------

class TestApplySubstitutions:
    """Tests for apply_substitutions(text, substitutions)."""

    def test_basic_find_replace(self):
        subs = [{"find": "foo", "replace": "bar"}]
        assert apply_substitutions("I have foo here", subs) == "I have bar here"

    def test_case_insensitive_default(self):
        subs = [{"find": "hello", "replace": "hi"}]
        result = apply_substitutions("HELLO world", subs)
        assert result == "hi world"

    def test_case_sensitive(self):
        subs = [{"find": "Hello", "replace": "Hi", "case_sensitive": True}]
        result = apply_substitutions("hello world", subs)
        # Should NOT replace because case doesn't match
        assert result == "hello world"

    def test_case_sensitive_match(self):
        subs = [{"find": "Hello", "replace": "Hi", "case_sensitive": True}]
        result = apply_substitutions("Hello world", subs)
        assert result == "Hi world"

    def test_whole_word_default(self):
        subs = [{"find": "cat", "replace": "dog"}]
        result = apply_substitutions("the cat sat on concatenation", subs)
        # "cat" in "concatenation" should NOT be replaced (whole_word=True default)
        assert "dog" in result
        assert "concatenation" in result

    def test_non_whole_word(self):
        subs = [{"find": "cat", "replace": "dog", "whole_word": False}]
        result = apply_substitutions("the cat sat on concatenation", subs)
        # Without whole_word, "cat" in "concatenation" IS replaced
        assert "condogenation" in result

    def test_empty_substitutions(self):
        assert apply_substitutions("hello world", []) == "hello world"

    def test_none_substitutions(self):
        assert apply_substitutions("hello world", None) == "hello world"

    def test_empty_find_string_skipped(self):
        subs = [{"find": "", "replace": "bar"}]
        assert apply_substitutions("hello world", subs) == "hello world"

    def test_multiple_substitutions(self):
        subs = [
            {"find": "alpha", "replace": "one"},
            {"find": "beta", "replace": "two"},
        ]
        result = apply_substitutions("alpha and beta", subs)
        assert result == "one and two"

    def test_replace_with_backref_is_literal(self):
        subs = [{"find": "foo", "replace": r"\1"}]
        # Without the lambda guard, re.sub would raise re.error ("invalid
        # group reference 1") because the pattern has no capturing group.
        result = apply_substitutions("I have foo here", subs)
        assert result == r"I have \1 here"

    def test_replace_with_named_backref_is_literal(self):
        subs = [{"find": "foo", "replace": r"\g<0>"}]
        result = apply_substitutions("foo", subs)
        assert result == r"\g<0>"

    def test_replace_with_double_backslash_is_literal(self):
        subs = [{"find": "foo", "replace": r"a\b"}]
        result = apply_substitutions("foo", subs)
        # Without the fix, "\b" would be interpreted as a backslash escape.
        assert result == r"a\b"

    def test_find_with_regex_metachars_is_literal(self):
        # The "." in the find string must match literally, not any char.
        subs = [{"find": "a.b", "replace": "X"}]
        result = apply_substitutions("aXb and a.b", subs, )
        # "aXb" should stay because the pattern is a literal "a.b"
        assert result == "aXb and X"

    def test_find_with_parens_is_literal(self):
        subs = [{"find": "(group)", "replace": "X", "whole_word": False}]
        result = apply_substitutions("before (group) after", subs)
        assert result == "before X after"


# ---------------------------------------------------------------------------
# 9. normalize_text (full pipeline)
# ---------------------------------------------------------------------------

class TestNormalizeText:
    """Tests for normalize_text(text, lang, substitutions, heteronyms, contractions)."""

    def test_english_full_pipeline(self):
        text = "Mr. Smith read Chapter IV etc."
        result = normalize_text(text, lang="en-us")
        assert "Mister" in result
        assert "4" in result
        assert "et cetera" in result

    def test_non_english_skips_abbreviations(self):
        text = "Mr. Smith"
        result = normalize_text(text, lang="fr-fr")
        # Abbreviation expansion is English-only
        assert "Mr." in result or "Mister" not in result

    def test_non_english_skips_roman_numerals(self):
        text = "Chapter IV"
        result = normalize_text(text, lang="de-de")
        assert "IV" in result

    def test_non_english_symbols_not_english_words(self):
        text = "Tom & Jerry"
        result = normalize_text(text, lang="ja-jp")
        assert "and" not in result

    def test_substitutions_applied_last(self):
        subs = [{"find": "Mister", "replace": "Mr"}]
        text = "Mr. Smith"
        result = normalize_text(text, lang="en-us", substitutions=subs)
        # Abbreviation expands Mr. -> Mister, then substitution replaces Mister -> Mr
        assert "Mr Smith" in result

    def test_substitutions_applied_for_non_english(self):
        subs = [{"find": "bonjour", "replace": "hello"}]
        text = "bonjour monde"
        result = normalize_text(text, lang="fr-fr", substitutions=subs)
        assert "hello" in result

    def test_heteronyms_disabled(self):
        text = "I read books."
        # Should work without error even with heteronyms=False
        result = normalize_text(text, lang="en-us", heteronyms=False)
        assert isinstance(result, str)

    def test_contractions_disabled(self):
        text = "He's going."
        result = normalize_text(text, lang="en-us", contractions=False)
        assert isinstance(result, str)

    def test_unicode_normalized_for_all_languages(self):
        text = "\u201cHello\u201d"
        result = normalize_text(text, lang="fr-fr")
        assert "\u201c" not in result
        assert '"' in result

    def test_en_dash_between_numbers_english(self):
        text = "pages 10\u201320"
        result = normalize_text(text, lang="en-us")
        assert "10 to 20" in result

    def test_en_dash_between_numbers_non_english(self):
        text = "pages 10\u201320"
        result = normalize_text(text, lang="fr-fr")
        assert "to" not in result


# ---------------------------------------------------------------------------
# 10. apply_phoneme_overrides
# ---------------------------------------------------------------------------

class TestPhonemeOverrides:
    """Tests for apply_phoneme_overrides(text, overrides)."""

    def test_wraps_word(self):
        # User enters canonical IPA from a dictionary; the override layer
        # folds the diphthong /aɪ/ to misaki's `I` so Kokoro reads it as a
        # single phoneme rather than `a`+`ʊ` separately.
        overrides = [{"word": "Hermione", "ipa": "hɜˈmaɪəni"}]
        result = apply_phoneme_overrides("Hermione went home", overrides)
        assert result == "[Hermione](/hɜˈmIəni/) went home"

    def test_case_insensitive_default(self):
        overrides = [{"word": "Hermione", "ipa": "X"}]
        result = apply_phoneme_overrides("hermione went", overrides)
        assert result == "[hermione](/X/) went"

    def test_case_sensitive(self):
        overrides = [{"word": "Hermione", "ipa": "X", "case_sensitive": True}]
        result = apply_phoneme_overrides("hermione and Hermione", overrides)
        assert result == "hermione and [Hermione](/X/)"

    def test_preserves_matched_case(self):
        overrides = [{"word": "Foo", "ipa": "X"}]
        result = apply_phoneme_overrides("Foo foo FOO", overrides)
        assert "[Foo](/X/)" in result
        assert "[foo](/X/)" in result
        assert "[FOO](/X/)" in result

    def test_regex_metachars_escaped(self):
        overrides = [{"word": "foo.bar", "ipa": "X"}]
        # Literal dot — "fooXbar" must NOT match.
        result = apply_phoneme_overrides(
            "got foo.bar here but fooXbar stays", overrides)
        assert result == "got [foo.bar](/X/) here but fooXbar stays"

    def test_backref_ipa_is_literal(self):
        overrides = [{"word": "foo", "ipa": r"\1"}]
        result = apply_phoneme_overrides("foo here", overrides)
        assert result == r"[foo](/\1/) here"

    def test_named_backref_ipa_is_literal(self):
        # The ipa string is treated as literal replacement text, not
        # interpreted as a regex backreference — but it still passes
        # through _to_misaki_phonemes like any other ipa string, which
        # folds ASCII 'g' to misaki's 'ɡ' (audit §1.2).
        overrides = [{"word": "foo", "ipa": r"\g<0>"}]
        result = apply_phoneme_overrides("foo here", overrides)
        assert result == "[foo](/\\ɡ<0>/) here"

    def test_apostrophe_word_matches(self):
        overrides = [{"word": "O'Brien", "ipa": "X"}]
        result = apply_phoneme_overrides("O'Brien nodded", overrides)
        assert result == "[O'Brien](/X/) nodded"

    def test_hyphenated_word_matches(self):
        overrides = [{"word": "Anne-Marie", "ipa": "X"}]
        result = apply_phoneme_overrides("Anne-Marie arrived", overrides)
        assert result == "[Anne-Marie](/X/) arrived"

    def test_disabled_entry_skipped(self):
        overrides = [{"word": "Hermione", "ipa": "X", "enabled": False}]
        result = apply_phoneme_overrides("Hermione went", overrides)
        assert result == "Hermione went"

    def test_empty_word_skipped(self):
        overrides = [{"word": "", "ipa": "X"}]
        result = apply_phoneme_overrides("foo bar", overrides)
        assert result == "foo bar"

    def test_empty_ipa_skipped(self):
        overrides = [{"word": "foo", "ipa": ""}]
        result = apply_phoneme_overrides("foo bar", overrides)
        assert result == "foo bar"

    def test_empty_overrides_list(self):
        assert apply_phoneme_overrides("text", []) == "text"
        assert apply_phoneme_overrides("text", None) == "text"

    def test_word_boundary_not_partial(self):
        overrides = [{"word": "her", "ipa": "X"}]
        # "Hermione" should NOT match — \b makes "her" whole-word.
        result = apply_phoneme_overrides("Hermione and her", overrides)
        assert "[Hermione](/X/)" not in result
        assert "[her](/X/)" in result

    def test_multiple_overrides(self):
        overrides = [
            {"word": "Hermione", "ipa": "H"},
            {"word": "Ron", "ipa": "R"},
        ]
        result = apply_phoneme_overrides("Hermione and Ron", overrides)
        assert result == "[Hermione](/H/) and [Ron](/R/)"


# ---------------------------------------------------------------------------
# 10b. apply_builtin_phoneme_overrides
# ---------------------------------------------------------------------------

class TestBuiltinPhonemeOverrides:
    """Tests for apply_builtin_phoneme_overrides(text, user_overrides)."""

    def test_los_angeles_wrapped(self):
        out = apply_builtin_phoneme_overrides("I went to Los Angeles.", None)
        # ʤ ligature, not two-char dʒ — misaki's alphabet has no 'dʒ'.
        assert '[Angeles](/ˈænʤələs/)' in out
        # 'Los' too — misaki otherwise voices it /loʊz/ ("lohz").
        assert '[Los](/lˈɔs/)' in out

    def test_los_not_matched_inside_words(self):
        # \\blos\\b must not fire inside Carlos/Marcos/closet/gloss.
        out = apply_builtin_phoneme_overrides(
            "Carlos shut the closet; Marcos left.", None)
        assert '[' not in out

    def test_yosemite_wrapped(self):
        out = apply_builtin_phoneme_overrides("Yosemite is huge.", None)
        assert '[Yosemite](/' in out

    def test_irish_name_wrapped(self):
        out = apply_builtin_phoneme_overrides("Sean and Aoife", None)
        assert '[Sean](/' in out
        assert '[Aoife](/' in out

    def test_user_override_preempts_builtin(self):
        # User wraps angeles with their own IPA; builtin must NOT also wrap.
        user = [{'word': 'angeles', 'ipa': 'aaa', 'enabled': True}]
        out = apply_builtin_phoneme_overrides("Los Angeles", user)
        assert '[Angeles](/' not in out
        assert 'Angeles' in out  # untouched, user pass will wrap

    def test_disabled_user_override_does_not_preempt(self):
        user = [{'word': 'angeles', 'ipa': 'aaa', 'enabled': False}]
        out = apply_builtin_phoneme_overrides("Los Angeles", user)
        assert '[Angeles](/' in out

    def test_case_insensitive(self):
        out = apply_builtin_phoneme_overrides("ANGELES, angeles, Angeles", None)
        assert out.count('](/ˈænʤələs/)') == 3

    def test_word_boundary(self):
        # Substring match must not fire — running letters should not match
        # the bare word 'angeles' inside a glommed-together token.
        out = apply_builtin_phoneme_overrides("losangelesnoboundary", None)
        assert '[Angeles]' not in out
        assert '[angeles]' not in out

    def test_blaise_wrapped(self):
        # Blaise Pascal is absent from misaki gold/silver; the builtin
        # override ships a canonical /bleɪz/ pronunciation. Folded eɪ → A.
        out = apply_builtin_phoneme_overrides("A man named Blaise.", None)
        assert "[Blaise](/blˈAz/)" in out

    def test_dives_verb_wrapped(self):
        # misaki silver shipped the biblical /ˈdaɪvˌiːz/ for the lowercase
        # entry, breaking the common verb. Folded aɪ → I.
        out = apply_builtin_phoneme_overrides("She dives into the pool.", None)
        assert "[dives](/dˈIvz/)" in out

    def test_dives_capitalized_also_wrapped(self):
        # `apply_builtin_phoneme_overrides` is case-insensitive so capitalized
        # biblical `Dives` also gets the verb pronunciation. Documented
        # trade-off: the verb/plural is far more common than the proper noun.
        out = apply_builtin_phoneme_overrides("Dives lived in luxury.", None)
        assert "[Dives](/dˈIvz/)" in out

    def test_user_override_preempts_blaise(self):
        # User-defined override for `blaise` must suppress the builtin so the
        # user's pass (which runs after) is the only emitter — no double-wrap.
        user = [{'word': 'blaise', 'ipa': 'xxx', 'enabled': True}]
        out = apply_builtin_phoneme_overrides("Blaise Pascal", user)
        assert "[Blaise]" not in out
        assert "Blaise" in out


# ---------------------------------------------------------------------------
# 11. apply_acronym_spellout
# ---------------------------------------------------------------------------

class TestAcronymSpellout:
    """Tests for apply_acronym_spellout(text, enabled)."""

    def test_basic(self):
        assert apply_acronym_spellout("The CIA", enabled=True) == "The C. I. A."

    def test_multiple(self):
        assert (apply_acronym_spellout("CIA and FBI", enabled=True)
                == "C. I. A. and F. B. I.")

    def test_disabled_default_is_no_op(self):
        assert apply_acronym_spellout("The CIA", enabled=False) == "The CIA"

    def test_skips_gold_entries(self):
        # NATO/NASA/SQL are in misaki gold with specific pronunciations.
        out = apply_acronym_spellout(
            "NATO met with NASA and SQL", enabled=True)
        assert "NATO" in out and "NASA" in out and "SQL" in out
        assert "N. A. T. O." not in out

    def test_skips_roman_numerals(self):
        # II, III, IV, V, X etc. are in the hard stoplist.
        out = apply_acronym_spellout("Section IV and Part VII", enabled=True)
        assert "IV" in out and "VII" in out
        assert "I. V." not in out

    def test_ignores_mixed_case(self):
        out = apply_acronym_spellout("The Cia called iPad", enabled=True)
        assert out == "The Cia called iPad"

    def test_ignores_single_letter(self):
        # Single-letter all-caps ("A", "I") is below the 2-char minimum.
        out = apply_acronym_spellout("I went to A meeting", enabled=True)
        assert out == "I went to A meeting"

    def test_length_cap(self):
        # 7+ chars are past the regex ceiling — let them pass through.
        out = apply_acronym_spellout("ABCDEFGH", enabled=True)
        assert out == "ABCDEFGH"

    def test_word_boundary(self):
        # Substring "CIA" inside another uppercase token shouldn't match.
        out = apply_acronym_spellout("SOMETHINGCIA", enabled=True)
        # SOMETHINGCIA is 12 chars — past the cap — so untouched.
        assert out == "SOMETHINGCIA"

    def test_skips_pronounceable_extras(self):
        # misaki gold stores SCUBA/LASER/RADAR/SONAR/SWAT/TASER/MODEM/WASP/
        # CAPTCHA/GULAG only in lowercase; the ALL-CAPS skip-filter would
        # miss them without _ACRONYM_EXTRA_SKIP.
        pronounceable = [
            "SCUBA", "LASER", "RADAR", "SONAR", "SWAT",
            "TASER", "MODEM", "WASP", "CAPTCHA", "GULAG",
        ]
        for word in pronounceable:
            sentence = f"The {word} was used."
            out = apply_acronym_spellout(sentence, enabled=True)
            assert word in out, f"{word} should not be spelled"
            assert ". ".join(word) + "." not in out


# ---------------------------------------------------------------------------
# 12. normalize_text — interaction of new passes with the rest
# ---------------------------------------------------------------------------

class TestNormalizePronunciationPipeline:
    """End-to-end interactions between substitutions / acronym / override."""

    def test_overrides_applied(self):
        out = normalize_text(
            "Hermione nodded.", lang="en-us",
            phoneme_overrides=[{"word": "Hermione", "ipa": "hɜˈmaɪəni"}])
        assert "[Hermione](/hɜˈmIəni/)" in out

    def test_overrides_skipped_for_non_english(self):
        out = normalize_text(
            "Hermione nodded.", lang="fr-fr",
            phoneme_overrides=[{"word": "Hermione", "ipa": "X"}])
        assert "[Hermione]" not in out

    def test_auto_acronyms_runs_for_english(self):
        out = normalize_text(
            "The CIA arrived.", lang="en-us", auto_acronyms=True)
        assert "C. I. A." in out

    def test_auto_acronyms_skipped_for_non_english(self):
        out = normalize_text(
            "The CIA arrived.", lang="de-de", auto_acronyms=True)
        assert "CIA" in out
        assert "C. I. A." not in out

    def test_substitutions_run_before_acronym(self):
        # User rewrites CIA to a full phrase — auto-acronym should not fire
        # on the expanded text.
        subs = [{"find": "CIA", "replace": "Central Intelligence Agency",
                 "whole_word": True, "case_sensitive": True}]
        out = normalize_text(
            "The CIA arrived.", lang="en-us",
            substitutions=subs, auto_acronyms=True)
        assert "Central Intelligence Agency" in out
        assert "C. I. A." not in out

    def test_substitutions_chain_into_override(self):
        # "Dr." -> "Doctor" via substitution, then override "Doctor" phonemes.
        subs = [{"find": "Dr.", "replace": "Doctor", "whole_word": False}]
        overrides = [{"word": "Doctor", "ipa": "ˈdɑktɚ"}]
        out = normalize_text(
            "Dr. Smith arrived.", lang="en-us",
            substitutions=subs,
            phoneme_overrides=overrides)
        assert "[Doctor](/ˈdɑktɚ/)" in out

    def test_clean_special_characters_preserves_override_syntax(self):
        # Direct check that brackets/slashes survive the special-char cleanup
        # — guards against regressions if the scene-break regex ever widens.
        text = "Hello [Hermione](/hɜˈmaɪəni/) goodbye"
        out = clean_special_characters(text, is_english=True)
        assert "[Hermione](/hɜˈmaɪəni/)" in out

    def test_backref_in_override_ipa_literal(self):
        # Same class of bug we fixed in apply_substitutions — the IPA
        # template must not be interpreted as a regex replacement. The
        # ASCII 'g' still folds to misaki's 'ɡ' (audit §1.2) either way.
        overrides = [{"word": "foo", "ipa": r"\g<0>"}]
        out = normalize_text("foo here", lang="en-us",
                             phoneme_overrides=overrides)
        assert "[foo](/\\ɡ<0>/)" in out


# ---------------------------------------------------------------------------
# 13. Misaki preprocess whitespace patch — multi-paragraph alignment
# ---------------------------------------------------------------------------
#
# Upstream misaki.en.G2P.preprocess builds its source-token list with
# str.split(), which silently drops every whitespace run. spaCy's tokenizer
# keeps `\n` (and other whitespace) as separate tokens, so on long text the
# source list is shorter than the spaCy mutable-token list and
# Alignment.from_strings drifts further with every paragraph break. By the
# time a `[word](/IPA/)` markdown wrapping appears mid-chapter, its feature
# attaches to a punctuation/newline mutable_token instead of the actual word
# — the rating-5 IPA gets dropped silently and the override audio leaks onto
# the wrong token. autiobooks/engine.py:_patch_misaki_preprocess() monkey-
# patches the system misaki at import time; autiobooks/misaki/en.py carries
# the same fix in-place for PyInstaller builds.

class TestMisakiPreprocessWhitespacePatch:
    """Regression: the alignment-drift bug that hid `[word](/IPA/)` markdown
    overrides mid-chapter. Tests must run against multi-paragraph text — the
    bug is invisible on single-sentence inputs because there isn't enough
    accumulated drift for the wrong-token attachment to occur."""

    def test_bundled_preprocess_keeps_whitespace_tokens(self):
        # importorskip: the bundled misaki needs its runtime deps (addict,
        # num2words); minimal test envs without them skip instead of fail.
        bundled_en = pytest.importorskip('autiobooks.misaki.en')
        text = "First paragraph.\nSecond paragraph.\n[word](/wˈɜɹd/) here."
        _result, tokens, _features = bundled_en.G2P.preprocess(text)
        whitespace_tokens = [t for t in tokens if t and not t.strip()]
        # Without the patch, str.split() yields zero whitespace tokens for
        # this input; with the patch the two `\n` runs are preserved.
        assert len(whitespace_tokens) >= 2, (
            f"expected ≥2 whitespace source tokens, got {whitespace_tokens!r}")

    def test_engine_import_patches_system_misaki(self):
        # Importing autiobooks.engine must install the monkey-patch on the
        # `misaki` package Kokoro pulls in at runtime — otherwise the runtime
        # path stays broken even when the bundled copy is fixed.
        pytest.importorskip('autiobooks.engine')  # import for side effect;
        # skips in envs without engine deps (numpy/soundfile/torch/kokoro)
        system_en = pytest.importorskip('misaki.en')
        assert getattr(
            system_en.G2P.preprocess, '_autiobooks_ws_patch', False), (
            "system misaki.en.G2P.preprocess was not patched on engine import")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_markdown_override_survives_chapter_length_text(self):
        # Reproduces the Sky_Pride_Ch39 bug: a single `[bowed](/bˈWd/)`
        # wrapping placed deep in multi-paragraph text. Pre-patch, the
        # feature attached to a `?` or `\n` mutable_token and the bowed
        # token fell back to misaki's gold (`bˈOd`).
        from autiobooks import engine  # noqa: F401
        from autiobooks.misaki import en as bundled_en
        prelude = "\n".join([
            "She entered the courtyard. He waited at the gate.",
            "Cherry petals drifted across the stones.",
            "The wind carried whispers of the past.",
            "Daoist Steelshimmer was looking at him like she had "
            "discovered a treasure. \"Junior, what are you?\"",
            "That didn't seem like it had a good answer.",
            "She was waiting for him to say something.",
            "",
        ])
        text = prelude + 'He [bowed](/bˈWd/).'
        g2p = bundled_en.G2P(trf=False, british=False, fallback=None)
        _result, tokens = g2p(text)
        bowed = [t for t in tokens if (t.text or '').lower() == 'bowed']
        assert len(bowed) == 1
        assert bowed[0].phonemes == 'bˈWd', (
            f"alignment drift attached IPA to wrong token; "
            f"bowed phonemes={bowed[0].phonemes!r} (expected 'bˈWd')")
        assert bowed[0]._.rating == 5

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_multiple_markdowns_each_hit_their_target(self):
        # Three wrappings spread across a chapter-shaped block. Pre-patch,
        # the second and third would drift further than the first and miss
        # entirely — every override after the first paragraph break was at
        # risk.
        from autiobooks import engine  # noqa: F401
        from autiobooks.misaki import en as bundled_en
        para = ("This is a paragraph that runs across several sentences. "
                "It contains punctuation, dialogue, and quote marks. "
                "\"It even has a quoted line,\" she said.\n")
        text = (para + 'He [bowed](/bˈWd/) deeply.\n' +
                para + 'She [bowed](/bˈWd/) again.\n' +
                para + 'They [bowed](/bˈWd/) too.\n')
        g2p = bundled_en.G2P(trf=False, british=False, fallback=None)
        _result, tokens = g2p(text)
        bowed = [t for t in tokens if (t.text or '').lower() == 'bowed']
        assert len(bowed) == 3
        for i, t in enumerate(bowed):
            assert t.phonemes == 'bˈWd', (
                f"bowed[{i}] missed alignment: phonemes={t.phonemes!r}")
            assert t._.rating == 5

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_user_override_survives_long_text(self):
        # Same bug also broke user-entered Pronunciation Overrides on long
        # chapters — the test surface needs to cover both contextual rules
        # and user overrides since both ride the same markdown channel.
        from autiobooks import engine  # noqa: F401
        from autiobooks.misaki import en as bundled_en
        prelude = "\n".join([f"Filler paragraph number {i}." for i in range(8)])
        text = prelude + "\nThe person we met was [Hermione](/hɜˈmIəni/)."
        g2p = bundled_en.G2P(trf=False, british=False, fallback=None)
        _result, tokens = g2p(text)
        hits = [t for t in tokens if (t.text or '').lower() == 'hermione']
        assert len(hits) == 1
        assert hits[0].phonemes == 'hɜˈmIəni'
        assert hits[0]._.rating == 5

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_no_ipa_leakage_to_neighbours(self):
        # Pre-patch, the dropped feature got attached to whatever token the
        # alignment landed on (usually a punctuation mark). That token then
        # spoke the override phonemes — the audible "baud" leaking onto a
        # `?` or newline. Verify no non-target token receives our IPA.
        from autiobooks import engine  # noqa: F401
        from autiobooks.misaki import en as bundled_en
        prelude = "\n".join([f"Paragraph {i} adds enough length." for i in range(6)])
        text = prelude + '\nFinally, he [bowed](/bˈWd/) once.'
        g2p = bundled_en.G2P(trf=False, british=False, fallback=None)
        _result, tokens = g2p(text)
        for t in tokens:
            if (t.text or '').lower() == 'bowed':
                continue
            assert t.phonemes != 'bˈWd', (
                f"IPA leaked onto non-target token text={t.text!r}")


# ---------------------------------------------------------------------------
# 14. Audit regressions (2026-06): ellipsis, roman false positives,
#     abbreviation punctuation, substitution boundaries, spellout/override
# ---------------------------------------------------------------------------

class TestEllipsisPreservation:
    """The scene-break regex must not eat '...' — it is a TTS pause cue."""

    def test_inline_ellipsis_survives(self):
        assert clean_special_characters("He paused... then left.") == \
            "He paused... then left."

    def test_dialogue_trailing_ellipsis_survives(self):
        assert clean_special_characters("No... I will not.") == \
            "No... I will not."

    def test_unicode_ellipsis_survives_full_pipeline(self):
        out = normalize_text("Wait… what?")
        assert "Wait... what?" in out

    def test_four_plus_dots_collapse_to_ellipsis(self):
        assert clean_special_characters("He left....") == "He left..."
        assert clean_special_characters("Hmm......") == "Hmm..."

    def test_scene_breaks_still_removed_inline(self):
        assert "***" not in clean_special_characters("before *** after")
        assert "---" not in clean_special_characters("x --- y")
        assert "===" not in clean_special_characters("x === y")


class TestRomanNumeralFalsePositives:
    """Lowercase common words after a keyword must not parse as numerals."""

    def test_part_mix_not_expanded(self):
        text = "The recipe is part mix, part magic."
        assert expand_roman_numerals(text) == text

    def test_part_li_not_expanded(self):
        assert expand_roman_numerals("part li") == "part li"

    def test_lowercase_ivx_numerals_still_expand(self):
        assert expand_roman_numerals("chapter xi") == "chapter 11"
        assert expand_roman_numerals("scene iv") == "scene 4"

    def test_real_word_after_keyword_not_expanded(self):
        # MIX (=1009) and DIV (=504) are valid strict romans but far more
        # likely the English word after a keyword. The gold-lexicon guard
        # leaves them alone while genuine numerals still convert.
        assert expand_roman_numerals("Part MIX was great") == "Part MIX was great"
        assert expand_roman_numerals("Part DIV covers it") == "Part DIV covers it"
        assert expand_roman_numerals("Book MCM") == "Book 1900"
        assert expand_roman_numerals("Chapter XIV") == "Chapter 14"


class TestNumberRangeAndDashes:
    """Numeric hyphen ranges and typewriter em-dashes must not mash words."""

    def test_hyphen_number_range_becomes_to(self):
        assert "10 to 20" in normalize_text("pages 10-20 here", lang="en-us")
        assert "3 to 5" in normalize_text("wait 3-5 minutes", lang="en-us")

    def test_hyphen_range_left_alone_when_not_both_digits(self):
        # Letter/number compounds must survive intact.
        assert "20-year-old" in normalize_text("a 20-year-old man", lang="en-us")
        assert "Catch-22" in normalize_text("a Catch-22 here", lang="en-us")
        assert "3-D" in normalize_text("in 3-D glory", lang="en-us")

    def test_number_range_not_converted_non_english(self):
        assert "10 to 20" not in normalize_text("pages 10-20", lang="fr-fr")

    def test_double_hyphen_emdash_becomes_comma(self):
        assert normalize_text("wait--no, stop", lang="en-us") == "wait, no, stop"
        assert "paused, then" in normalize_text("she paused -- then ran", lang="en-us")

    def test_standalone_hyphen_rule_still_stripped(self):
        # A separator line of hyphens (no adjacent word char) is still removed.
        out = normalize_text("text\n---\nmore", lang="en-us")
        assert "---" not in out

    def test_degrees_not_glued_to_unit(self):
        assert "degrees F" in normalize_text("it was 98.6°F", lang="en-us")
        assert "degrees C" in normalize_text("boils at 100°C", lang="en-us")
        assert "degreesF" not in normalize_text("it was 98.6°F", lang="en-us")

    def test_litrpg_arrow_variants_stripped(self):
        # Dingbat/supplemental arrows common in LitRPG stat blocks must not
        # reach the TTS (basic ←↑→↓ were already handled; these were not).
        import re
        arrow_class = re.compile('[←-⇿➔-➿'
                                 '⟰-⟿⤀-⥿⬀-⯿]')
        for s in ["Skill (lv50) ➔ Skill (lv60)", "HP 90 ⇒ 100",
                  "Skill ➜ Evolved", "before ⟶ after", "left ⬅ right ⮕ end"]:
            assert not arrow_class.search(normalize_text(s, lang="en-us"))


class TestAbbreviationBeforePunctuation:
    """Abbreviations followed by punctuation (not just whitespace) expand."""

    def test_et_al_before_comma(self):
        assert expand_abbreviations("Smith et al., 2020") == \
            "Smith and others, 2020"

    def test_abbreviation_before_close_paren(self):
        assert expand_abbreviations("(see pp. 3, etc.)") == \
            "(see pages 3, et cetera)"

    def test_abbreviation_before_close_quote(self):
        assert expand_abbreviations('He said "etc." loudly') == \
            'He said "et cetera" loudly'


class TestSubstitutionBoundaries:
    """whole_word anchors only on word-char edges so '$100'-style finds work."""

    def test_whole_word_non_word_prefix_matches(self):
        subs = [{"find": "$100", "replace": "one hundred dollars",
                 "whole_word": True}]
        assert apply_substitutions("It costs $100 total", subs) == \
            "It costs one hundred dollars total"

    def test_whole_word_non_word_prefix_no_partial(self):
        subs = [{"find": "$100", "replace": "one hundred dollars",
                 "whole_word": True}]
        assert apply_substitutions("It costs $1000 total", subs) == \
            "It costs $1000 total"


class TestAcronymOverrideInteraction:
    """Phoneme-override words must survive auto-acronym spellout."""

    def test_user_override_protects_acronym(self):
        overrides = [{"word": "CIA", "ipa": "X"}]
        out = normalize_text("The CIA called.", auto_acronyms=True,
                             phoneme_overrides=overrides)
        assert "[CIA](/X/)" in out
        assert "C. I. A." not in out

    def test_disabled_override_does_not_protect(self):
        overrides = [{"word": "CIA", "ipa": "X", "enabled": False}]
        out = normalize_text("The CIA called.", auto_acronyms=True,
                             phoneme_overrides=overrides)
        assert "C. I. A." in out

    def test_builtin_override_word_not_spelled_out(self):
        out = normalize_text("SEAN stood up.", auto_acronyms=True)
        assert "S. E. A. N." not in out
        assert "](/" in out  # wrapped by the built-in override instead


# ---------------------------------------------------------------------------
# Audit round 2 — regression tests
# ---------------------------------------------------------------------------

class TestRomanNumeralPronounGuard:
    """Bare single-letter numerals after a lowercase keyword are far more
    likely the pronoun 'I' than the numeral 1."""

    def test_book_i_pronoun_untouched(self):
        text = "The book I read was long."
        assert expand_roman_numerals(text) == text

    def test_part_i_pronoun_untouched(self):
        text = "For my part I agree."
        assert expand_roman_numerals(text) == text

    def test_act_i_pronoun_untouched(self):
        text = "In the second act I noticed a change."
        assert expand_roman_numerals(text) == text

    def test_pronoun_with_comma_untouched(self):
        text = "For my part I, too, agreed."
        assert expand_roman_numerals(text) == text

    def test_capitalized_keyword_converts(self):
        assert expand_roman_numerals(
            "Book I covers the basics.") == "Book 1 covers the basics."

    def test_capitalized_keyword_midsentence(self):
        assert expand_roman_numerals(
            "In Act I the hero dies") == "In Act 1 the hero dies"

    def test_lowercase_heading_at_line_end(self):
        assert expand_roman_numerals("chapter i") == "chapter 1"

    def test_lowercase_heading_with_colon(self):
        assert expand_roman_numerals(
            "part i: the beginning") == "part 1: the beginning"

    def test_multiletter_numeral_unaffected(self):
        assert expand_roman_numerals(
            "She read book II at school.") == "She read book 2 at school."


class TestRomanNumeralSingleLetterCDLM:
    """Audit §1.1: single-letter C/D/L/M never convert, even in heading-like
    position — only I/V/X stay ambiguous section numerals."""

    def test_appendix_c_unchanged(self):
        text = "See Appendix C for details."
        assert expand_roman_numerals(text) == text

    def test_part_d_unchanged(self):
        text = "Part D of the contract"
        assert expand_roman_numerals(text) == text

    def test_section_c_unchanged(self):
        text = "Section C: exclusions"
        assert expand_roman_numerals(text) == text

    def test_volume_l_unchanged(self):
        text = "Volume L"
        assert expand_roman_numerals(text) == text

    def test_chapter_m_unchanged(self):
        text = "Chapter M"
        assert expand_roman_numerals(text) == text

    def test_real_numeral_still_converts(self):
        assert expand_roman_numerals("Chapter IV") == "Chapter 4"


class TestEdAbbreviation:
    """'Ed.' expands to 'Edition' only after a digit/ordinal."""

    def test_name_ed_at_sentence_end_untouched(self):
        text = "Thanks, Ed. See you soon."
        assert expand_abbreviations(text) == text

    def test_ordinal_ed_expands(self):
        assert expand_abbreviations("2nd Ed.") == "2nd Edition"

    def test_digit_ed_expands(self):
        assert expand_abbreviations(
            "The 3 Ed. printing") == "The 3 Edition printing"

    def test_bare_ed_untouched(self):
        text = "Ed. note: see appendix."
        assert expand_abbreviations(text) == text


class TestUserOverridesWin:
    """User phoneme overrides and substitutions beat every built-in
    markdown-emitting pass — no nested `[[word](/a/)](/b/)` output."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_user_override_beats_contextual_bowed(self):
        overrides = [{"word": "bowed", "ipa": "bˈOd"}]
        out = normalize_text("He bowed his head.",
                             phoneme_overrides=overrides)
        assert "[bowed](/bˈOd/)" in out
        assert "[[" not in out

    def test_user_override_beats_resume_wrap(self):
        overrides = [{"word": "resume", "ipa": "ɹɪzˈum"}]
        out = normalize_text("Her résumé was strong.",
                             phoneme_overrides=overrides)
        assert "[resume](/ɹɪzˈum/)" in out
        assert "[[" not in out

    def test_substitution_beats_resume_wrap(self):
        subs = [{"find": "resume", "replace": "CV"}]
        out = normalize_text("Her résumé was strong.", substitutions=subs)
        assert "CV" in out
        assert "[" not in out

    def test_substitution_skips_existing_markdown(self):
        subs = [{"find": "bowed", "replace": "nodded"}]
        out = apply_substitutions("[bowed](/bˈWd/) and then he bowed", subs)
        assert out == "[bowed](/bˈWd/) and then he nodded"

    def test_phoneme_override_skips_existing_markdown(self):
        overrides = [{"word": "bowed", "ipa": "x"}]
        text = "[bowed](/bˈWd/)"
        assert apply_phoneme_overrides(text, overrides) == text

    def test_builtin_override_skips_existing_markdown(self):
        text = "[sean](/ʃˈɔn/)"
        assert apply_builtin_phoneme_overrides(text, None) == text

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_user_override_lead_not_respelled(self):
        overrides = [{"word": "lead", "ipa": "lˈid"}]
        out = normalize_text("He will lead the team.",
                             phoneme_overrides=overrides)
        assert "[lead](/lˈid/)" in out
        assert "leed" not in out


class TestSentenceClampedHeteronyms:
    """Contextual rule windows must not leak cues across sentence
    boundaries."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_cue_does_not_cross_sentence(self):
        out = apply_contextual_overrides(
            "They take the stage. Bow strings snapped.")
        assert "[Bow](" not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_cue_does_not_cross_sentence(self):
        out = apply_contextual_overrides(
            "There it is. Content filtering helps.")
        assert "[Content](" not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bass_cue_does_not_cross_sentence(self):
        out = apply_contextual_overrides(
            "He played jazz. Bass swam in the lake.")
        assert "[Bass](" not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_gesture_still_wraps(self):
        out = apply_contextual_overrides("He took a bow.")
        assert "[bow](/bˈW/)" in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_predicate_still_wraps(self):
        out = apply_contextual_overrides("He was content with that.")
        assert "[content](/kənˈtɛnt/)" in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_noun_compound_not_wrapped_despite_copula(self):
        # A copula earlier in the window must not force the adjective sense
        # when 'content' is a compound modifier of a following noun
        # ("content creator/manager/writers" = CON-tent, not con-TENT).
        for s in ("He is a content creator.",
                  "She was the content manager.",
                  "They are content writers."):
            assert "[content]" not in apply_contextual_overrides(s)


class TestEdAdjectiveHeteronyms:
    """Attributive -ed adjectives take the syllabic /ɪd/ form; verb past
    stays misaki's 1-syllable default. Plus 'beloved' and 'delegate'."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_learned_adjective_wraps(self):
        assert "[learned](/lˈɜɹnɪd/)" in apply_contextual_overrides(
            "A learned man spoke.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_learned_verb_not_wrapped(self):
        assert "[learned]" not in apply_contextual_overrides(
            "She learned the truth quickly.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_blessed_aged_cursed_adjective_wrap(self):
        assert "[blessed](" in apply_contextual_overrides("a blessed event")
        assert "[aged](" in apply_contextual_overrides("an aged man")
        assert "[cursed](" in apply_contextual_overrides("a cursed sword")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_blessed_verb_not_wrapped(self):
        assert "[blessed]" not in apply_contextual_overrides(
            "The priest blessed them.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_beloved_always_wraps(self):
        for s in ("Our beloved leader spoke.", "She is my beloved.",
                  "Dearly beloved, we gather here."):
            assert "[beloved](/bɪˈlʌvɪd/)" in apply_contextual_overrides(s)

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_delegate_verb_wraps_noun_does_not(self):
        assert "[delegate](" in apply_contextual_overrides(
            "They delegate authority to her.")
        assert "[delegate]" not in apply_contextual_overrides(
            "Send a delegate to the summit.")


class TestMinuteBowEdgeCases:
    """Regression guards for the minute/bow false positives/negatives."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_minute_time_compound_not_wrapped(self):
        # 'minute hand'/'minute book' are the time unit (MIN-it), not 'tiny'.
        assert "[minute]" not in apply_contextual_overrides(
            "The minute hand moved slowly.")
        assert "[minute]" not in apply_contextual_overrides(
            "The minute book recorded it.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_minute_tiny_after_a_still_wraps(self):
        # Regression: the old 'a' block suppressed the 'tiny' sense here.
        assert "[minute](/mIˈnut/)" in apply_contextual_overrides(
            "A minute amount of dust fell.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_ribbon_not_gesture(self):
        # A ribbon bow is /boʊ/, not the bowing gesture /baʊ/.
        assert "[bow]" not in apply_contextual_overrides(
            "She tied a bow in her hair.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bow_gesture_still_wraps_after_fix(self):
        assert "[bow](/bˈW/)" in apply_contextual_overrides("He took a bow.")


class TestPrayerFrequentConsummate:
    """prayer/frequent/consummate from the comprehensive heteronym sweep."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_prayer_devotion_one_syllable(self):
        assert "[prayer](/pɹˈɛɹ/)" in apply_contextual_overrides(
            "She said a quiet prayer.")
        assert "[prayers](/pɹˈɛɹz/)" in apply_contextual_overrides(
            "He whispered his prayers.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_prayer_not_matched_as_substring(self):
        # 'prayers' inside other words / agent-noun edge cases aside, the
        # tokenizer keeps it whole — but guard the common 'prayer book'.
        out = apply_contextual_overrides("The prayer book was worn.")
        assert "[prayer](/pɹˈɛɹ/)" in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_frequent_verb_vs_adjective(self):
        assert "[frequent](/fɹiˈkwɛnt/)" in apply_contextual_overrides(
            "They frequent the tavern.")
        assert "[frequent]" not in apply_contextual_overrides(
            "He is a frequent visitor.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_frequent_inflections_wrap(self):
        assert "[frequented](" in apply_contextual_overrides(
            "She frequented the library.")
        assert "[frequents](" in apply_contextual_overrides(
            "He frequents the bar.")

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_consummate_adjective_vs_verb(self):
        assert "[consummate](/kənˈsʌmət/)" in apply_contextual_overrides(
            "She is a consummate professional.")
        assert "[consummate]" not in apply_contextual_overrides(
            "They consummate the merger today.")


class TestAcronymSpelloutEmphasis:
    """All-caps emphasis of ordinary words and roman numerals must not be
    letterized when auto-acronym spellout is on."""

    def test_shouted_word_not_letterized(self):
        out = apply_acronym_spellout("He shouted STOP at once.", True)
        assert "S. T. O. P." not in out
        assert "STOP" in out

    def test_the_end_not_letterized(self):
        out = apply_acronym_spellout("THE END", True)
        assert out == "THE END"

    def test_roman_numeral_beyond_twelve_not_letterized(self):
        out = apply_acronym_spellout("XIII", True)
        assert out == "XIII"

    def test_large_roman_numeral_not_letterized(self):
        out = apply_acronym_spellout("Section XLVII begins.", True)
        assert "X. L. V. I. I." not in out

    def test_genuine_acronym_still_letterized(self):
        out = apply_acronym_spellout("The FBI arrived.", True)
        assert "F. B. I." in out


# ---------------------------------------------------------------------------
# 14. Audit round 3 regressions (2026-07)
# ---------------------------------------------------------------------------

class TestAuditRound3Heteronyms:
    """Contextual-rule misfires found by the 2026-07 audit. Each pair pins
    the fixed case AND the neighbouring case that must keep working."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_archery_bow_to_not_gesture(self):
        # §1.1 — the ungated next-token check forced /baʊ/ onto the noun.
        out = apply_contextual_overrides(
            "He drew the bow to his cheek and fired.")
        assert '[bow](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_verb_bowed_to_still_gesture(self):
        out = apply_contextual_overrides("She bowed to the king.")
        assert '[bowed](/bˈWd/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_ten_minute_walk_is_time_unit(self):
        # §1.2 — number words before 'minute' fix the time sense.
        out = apply_contextual_overrides("They took a ten minute walk.")
        assert '[minute](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_last_minute_is_time_unit(self):
        out = apply_contextual_overrides("He made a last minute decision.")
        assert '[minute](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_minute_amount_still_tiny(self):
        out = apply_contextual_overrides("A minute amount of poison remained.")
        assert '[minute](/mIˈnut/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_every_minute_detail_still_tiny(self):
        out = apply_contextual_overrides("Every minute detail was examined.")
        assert '[minute](/mIˈnut/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_middle_aged_not_syllabic(self):
        # §1.4 — hyphenated compounds keep the 1-syllable verb form.
        out = apply_contextual_overrides("A middle-aged man answered.")
        assert '[aged](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_bare_attributive_aged_still_syllabic(self):
        out = apply_contextual_overrides("An aged wizard entered.")
        assert '[aged](/ˈAʤɪd/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_content_of_the_letter_is_noun(self):
        # §1.5 — determiner immediately before 'content' wins over a copula
        # earlier in the window.
        out = apply_contextual_overrides("Such was the content of the letter.")
        assert '[content](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_predicate_content_still_adjective(self):
        out = apply_contextual_overrides("He was content with the result.")
        assert '[content](/kənˈtɛnt/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_row_between_vines_is_line_sense(self):
        # §1.12 — 'between' dropped from the argument-next cues.
        out = apply_contextual_overrides(
            "She walked down the row between the vines.")
        assert '[row](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_huge_row_still_argument(self):
        out = apply_contextual_overrides("They had a huge row about money.")
        assert '[row](/ɹˈW/)' in out


class TestAuditRound3Abbreviations:
    def test_lettered_list_c_not_circa(self):
        # §1.13 — 'c.' expands only before a digit.
        out = expand_abbreviations("a. apples b. pears c. plums")
        assert 'circa' not in out

    def test_bc_era_not_circa(self):
        out = expand_abbreviations("around 400 b.c. the city fell")
        assert 'circa' not in out

    def test_c_before_year_is_circa(self):
        out = expand_abbreviations("The manuscript dates from c. 1850.")
        assert 'circa 1850' in out

    def test_capitalized_ave_expands(self):
        # §1.14 — street abbreviations are capitalized in real text.
        assert 'Avenue' in expand_abbreviations("He lived on Fifth Ave. for years.")

    def test_capitalized_blvd_expands(self):
        assert 'Boulevard' in expand_abbreviations("Sunset Blvd. was empty.")


class TestAuditRound4TerminalAbbreviations:
    # §1.3 — suffix/place abbreviations trail their noun, so they end
    # sentences routinely; a plain expansion ate the terminator and merged
    # the sentences into a pauseless run-on.
    def test_st_street_keeps_sentence_period(self):
        assert normalize_text("42 Elm St. It was cold", lang="en-us") == \
            "42 Elm Street. It was cold"

    def test_ave_keeps_sentence_period(self):
        assert normalize_text("Fifth Ave. The rain fell.", lang="en-us") == \
            "Fifth Avenue. The rain fell."

    def test_dept_keeps_sentence_period(self):
        assert normalize_text("Ask the dept. They know.", lang="en-us") == \
            "Ask the department. They know."

    def test_street_midsentence_drops_period(self):
        # Not a boundary — no terminator may be invented.
        assert normalize_text("He lived on Elm St. in Ohio", lang="en-us") == \
            "He lived on Elm Street in Ohio"

    def test_saint_never_gains_period(self):
        # 'Saint' precedes a name, so it can't be sentence-final.
        assert normalize_text("We met at St. Peter for lunch.", lang="en-us") == \
            "We met at Saint Peter for lunch."

    def test_title_never_gains_period(self):
        assert "Mister Smith" in normalize_text("Mr. Smith arrived.", lang="en-us")

    def test_etc_control_still_keeps_period(self):
        assert "et cetera. Then" in normalize_text(
            "...and so on, etc. Then he left.", lang="en-us")

    def test_jr_deliberately_excluded(self):
        # _ends_sentence can't separate "Smith Jr. He was tall." from
        # "Jr. High School" / "King Jr. Day", so Jr./Sr. keep the plain
        # expansion. Pins the documented trade-off, not desired prose.
        out = normalize_text("Martin Luther King Jr. Day", lang="en-us")
        assert out == "Martin Luther King Junior Day"


class TestAuditRound3IpaFolding:
    def test_affricate_dz_folds_to_ligature(self):
        # §1.15 — misaki's alphabet has no two-char 'dʒ'/'tʃ'.
        assert _to_misaki_phonemes('ˈændʒələs') == 'ˈænʤələs'
        assert _to_misaki_phonemes('tʃɜɹtʃ') == 'ʧɜɹʧ'

    def test_folding_idempotent(self):
        once = _to_misaki_phonemes('ˈændʒələs aʊ tʃ')
        assert _to_misaki_phonemes(once) == once

    def test_parens_stripped(self):
        # §1.3 belt-and-braces — paren IPA breaks misaki's LINK_REGEX.
        assert _to_misaki_phonemes('ˈlɪs(ə)n') == 'ˈlɪsən'

    def test_paren_ipa_survives_normalize_text(self):
        out = normalize_text(
            "Listen carefully.",
            heteronyms=False, contractions=False,
            phoneme_overrides=[
                {'word': 'listen', 'ipa': 'ˈlɪs(ə)n', 'enabled': True}])
        assert '[Listen](/ˈlɪsən/)' in out
        assert ')n' not in out


class TestAuditRound3UserAlwaysWins:
    def test_accented_override_matches_folded_text(self):
        # §4.2 — strip_diacritics folds the text before user passes run;
        # the user's accented word must fold the same way.
        out = normalize_text(
            "Zoë smiled.", heteronyms=False, contractions=False,
            phoneme_overrides=[
                {'word': 'Zoë', 'ipa': 'zˈoʊi', 'enabled': True}])
        assert '[Zoe](/zˈOi/)' in out

    def test_accented_substitution_matches_folded_text(self):
        out = normalize_text(
            "Zoë smiled.", heteronyms=False, contractions=False,
            substitutions=[
                {'find': 'Zoë', 'replace': 'Zoey', 'enabled': True}])
        assert 'Zoey smiled.' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_contraction_substitution_wins(self):
        # §4.3 — resolve_contractions must skip suppressed contractions.
        out = normalize_text(
            "She'd seen it before.", heteronyms=False, contractions=True,
            substitutions=[
                {'find': "She'd", 'replace': 'She had already'}])
        assert 'She had already seen it before.' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_multiword_substitution_beats_contextual_rule(self):
        # §4.4 — each word of a multi-word find joins the suppression set.
        out = normalize_text(
            "He was poisoned by lead paint.", contractions=False,
            substitutions=[
                {'find': 'lead paint', 'replace': 'led paint'}])
        assert 'led paint' in out
        assert '[lead](' not in out


class TestAuditRound3AcronymSpellout:
    def test_markdown_span_not_letterized(self):
        # §1.6 — the spellout must not corrupt [WORD](/IPA/) markdown
        # emitted by the contextual pass.
        out = apply_acronym_spellout("HE [BOWED](/bˈWd/) HIS HEAD.", True)
        assert '[BOWED](/bˈWd/)' in out

    def test_real_word_caps_not_letterized(self):
        # 'bowed' is absent from misaki gold (no inflections) but is a real
        # word — all-caps emphasis must not be spelled out.
        out = apply_acronym_spellout("HE BOWED BEFORE THE KING", True)
        assert 'B. O. W. E. D.' not in out

    def test_short_acronyms_still_letterized(self):
        # FBI/CIA are cmudict entries; the real-word check is 4+ letters so
        # short letter-read acronyms keep spelling out.
        out = apply_acronym_spellout("The FBI and CIA arrived.", True)
        assert 'F. B. I.' in out
        assert 'C. I. A.' in out


class TestCorpusSweepRegressions:
    """Misfires found by the 2026-07 1.4M-word library sweep."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_hyphenated_minute_compound_is_time_unit(self):
        # spaCy tokenizes 'five-minute' as five / - / minute; the blocklist
        # must look through the hyphen.
        for text in ("It was a five-minute walk outside town.",
                     "He made a last-minute decision.",
                     "It was about a 20-minute walk away."):
            assert '[minute](' not in apply_contextual_overrides(text), text

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_unhyphenated_middle_aged_not_syllabic(self):
        out = apply_contextual_overrides("A middle aged man answered.")
        assert '[aged](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_matured_goods_aged_not_syllabic(self):
        for text in ("The scent of aged wood filled the room.",
                     "That is ten-year aged Gouda."):
            assert '[aged](' not in apply_contextual_overrides(text), text

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_aged_wizard_still_syllabic(self):
        out = apply_contextual_overrides("An aged wizard entered.")
        assert '[aged](/ˈAʤɪd/)' in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_learned_lesson_not_syllabic(self):
        out = apply_contextual_overrides(
            "The words carried the weight of a learned lesson.")
        assert '[learned](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_learned_man_still_syllabic(self):
        out = apply_contextual_overrides("A learned man spoke to the crowd.")
        assert '[learned](/lˈɜɹnɪd/)' in out


# ---------------------------------------------------------------------------
# 15. Audit round 4 regressions (2026-07-16)
# ---------------------------------------------------------------------------

class TestAuditRound4BowArchery:
    """§1.4 — the archery-cue suppression only ran inside `if is_verb_form`,
    so a _BOW_VERB_CUES word ('took'/'gave'/'made') paired with an archery
    cue in the same sentence still forced the gesture sense onto the noun."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_took_bow_nocked_arrow_not_gesture(self):
        out = apply_contextual_overrides(
            "She took the bow and nocked an arrow.")
        assert '[bow](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_took_bow_from_archer_not_gesture(self):
        out = apply_contextual_overrides("He took the bow from the archer.")
        assert '[bow](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_gave_bow_and_quiver_not_gesture(self):
        out = apply_contextual_overrides("She gave him the bow and a quiver.")
        assert '[bow](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_deep_bow_still_gesture(self):
        # The true positive that must keep working after hoisting the guard.
        out = apply_contextual_overrides("He took a deep bow.")
        assert '[bow](/bˈW/)' in out


class TestAuditRound4RowOf:
    """§1.5 — 'row of X' is the canonical line sense regardless of a
    preceding argument-cue adjective; 'right'/'family' are dropped from
    _ROW_ARGUMENT_PREV as high-frequency line-sense modifiers."""

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_big_row_of_houses_is_line_sense(self):
        out = apply_contextual_overrides(
            "They stood before a big row of houses.")
        assert '[row](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_huge_row_of_tents_is_line_sense(self):
        out = apply_contextual_overrides(
            "A huge row of tents lined the field.")
        assert '[row](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_right_row_no_longer_argument(self):
        out = apply_contextual_overrides("He sat in the right row and waited.")
        assert '[row](' not in out

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_huge_row_about_money_still_argument(self):
        # The true positive that must keep working.
        out = apply_contextual_overrides("They had a huge row about money.")
        assert '[row](/ɹˈW/)' in out


class TestAuditRound4EmptyFoldGuard:
    """§1.6 — the emptiness check ran BEFORE strip_diacritics, so a find/word
    that is Mn-only (a bare combining accent) folded to '' and built the
    empty regex pattern, which matches at every position in the text."""

    def test_substitution_mn_only_find_skipped(self):
        out = apply_substitutions(
            'abc', [{'find': '́', 'replace': 'X'}],
            fold_diacritics=True)
        assert out == 'abc'

    def test_phoneme_override_mn_only_word_skipped(self):
        out = apply_phoneme_overrides(
            'abc', [{'word': '́', 'ipa': 'k'}],
            fold_diacritics=True)
        assert out == 'abc'
