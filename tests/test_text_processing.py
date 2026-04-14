import pytest

from autiobooks.text_processing import (
    normalize_unicode,
    strip_diacritics,
    expand_abbreviations,
    expand_roman_numerals,
    clean_special_characters,
    apply_substitutions,
    normalize_text,
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

    # strip_diacritics is called inside normalize_unicode
    def test_diacritics_stripped(self):
        result = normalize_unicode("caf\u00e9")
        assert result == "cafe"


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

    def test_heteronyms_dict_contains_read(self):
        assert "read" in HETERONYMS

    def test_heteronyms_dict_contains_lead(self):
        assert "lead" in HETERONYMS

    def test_heteronyms_dict_does_not_contain_wind(self):
        assert "wind" not in HETERONYMS

    def test_heteronyms_dict_does_not_contain_tear(self):
        assert "tear" not in HETERONYMS

    def test_heteronyms_dict_does_not_contain_wound(self):
        assert "wound" not in HETERONYMS

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_resolve_returns_string(self):
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("I read a book yesterday.")
        assert isinstance(result, str)

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_resolve_present_read(self):
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("I read books every day.")
        # Present tense "read" should become "reed"
        assert "reed" in result

    @pytest.mark.skipif(not HAS_SPACY, reason="spaCy not installed")
    def test_resolve_without_spacy_passthrough(self):
        # When spaCy IS available, the function should still handle plain text
        from autiobooks.text_processing import resolve_heteronyms
        result = resolve_heteronyms("Hello world")
        assert result == "Hello world"


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
