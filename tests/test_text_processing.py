import pytest

from autiobooks.text_processing import (
    normalize_unicode,
    strip_diacritics,
    expand_abbreviations,
    expand_roman_numerals,
    clean_special_characters,
    apply_substitutions,
    apply_phoneme_overrides,
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
        assert expand_abbreviations("and etc.") == "and et cetera"

    def test_eg(self):
        assert expand_abbreviations("e.g. this") == "for example this"

    def test_ie(self):
        assert expand_abbreviations("i.e. that") == "that is that"

    def test_st(self):
        assert expand_abbreviations("St. Louis") == "Saint Louis"

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
        overrides = [{"word": "foo", "ipa": r"\g<0>"}]
        result = apply_phoneme_overrides("foo here", overrides)
        assert result == r"[foo](/\g<0>/) here"

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
        # template must not be interpreted as a regex replacement.
        overrides = [{"word": "foo", "ipa": r"\g<0>"}]
        out = normalize_text("foo here", lang="en-us",
                             phoneme_overrides=overrides)
        assert r"[foo](/\g<0>/)" in out


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
        from autiobooks.misaki import en as bundled_en
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
        from autiobooks import engine  # noqa: F401  (import for side effect)
        from misaki import en as system_en
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
