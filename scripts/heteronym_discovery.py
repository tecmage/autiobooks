"""Discovery harness: find heteronyms misaki does NOT disambiguate.

For each (word, sense-A sentence, sense-B sentence) the two sentences are run
through the real normalize_text -> misaki pipeline and the target word's
phonemes are extracted from each. If the two senses yield the SAME phonemes,
misaki (plus our contextual rules) isn't distinguishing them -> a likely gap.
DIFFER = handled. Prints a compact table sorted so gaps surface first.

Usage: python scripts/heteronym_discovery.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from autiobooks.misaki import en
from autiobooks.text_processing import normalize_text
from audit_pronunciations import misaki_to_ipa

# (word, sentenceA, expected-A-hint, sentenceB, expected-B-hint)
# hints are human labels only; correctness is judged by whether A != B.
CASES = [
    # --- N/V stress pairs not in the main audit ---
    ('convert',  'He is a convert to the cause.',          'noun',
                 'They will convert the building.',         'verb'),
    ('conduct',  'Her conduct was exemplary.',             'noun',
                 'He will conduct the orchestra.',          'verb'),
    ('contest',  'She won the contest easily.',            'noun',
                 'They contest the ruling in court.',       'verb'),
    ('contrast', 'There is a sharp contrast here.',        'noun',
                 'Contrast the two approaches.',            'verb'),
    ('digest',   'Read the digest of the report.',         'noun',
                 'It takes hours to digest a meal.',        'verb'),
    ('escort',   'The escort arrived at noon.',            'noun',
                 'I will escort you to the door.',          'verb'),
    ('export',   'Oil is their main export.',              'noun',
                 'They export grain to Europe.',            'verb'),
    ('import',   'Steel is a major import.',               'noun',
                 'They import cars from Japan.',            'verb'),
    ('incline',  'There was a steep incline ahead.',       'noun',
                 'I incline toward the first option.',      'verb'),
    ('perfect',  'She has a perfect record.',              'adj',
                 'Practice will perfect your craft.',       'verb'),
    ('pervert',  'The pervert was arrested.',              'noun',
                 'Do not pervert the course of justice.',   'verb'),
    ('protest',  'They held a loud protest.',              'noun',
                 'I protest this decision.',                'verb'),
    ('transfer', 'Request a transfer to another team.',    'noun',
                 'They transfer funds every Friday.',       'verb'),
    ('upset',    'The loss was a major upset.',            'noun',
                 'Do not upset the baby.',                  'verb'),
    ('discount', 'They offered a steep discount.',         'noun',
                 'Do not discount his opinion.',            'verb'),
    ('defect',   'The car had a small defect.',            'noun',
                 'Several soldiers defect each year.',      'verb'),
    ('console',  'He sat at the game console.',            'noun',
                 'She tried to console him.',               'verb'),
    ('combat',   'He saw combat overseas.',                'noun',
                 'They combat the disease daily.',          'verb'),
    ('segment',  'Cut it into one segment.',               'noun',
                 'They segment the audience by age.',       'verb'),
    ('recall',   'There was a product recall.',            'noun',
                 'I do not recall the meeting.',            'verb'),
    ('refund',   'I want a full refund.',                  'noun',
                 'They will refund your money.',            'verb'),
    ('recount',  'The recount changed the result.',        'noun',
                 'Let me recount what happened.',           'verb'),
    ('extract',  'She read an extract from the book.',     'noun',
                 'Dentists extract bad teeth.',             'verb'),
    ('exploit',  'It was a daring exploit.',               'noun',
                 'They exploit cheap labor.',               'verb'),
    ('torment',  'He lived in constant torment.',          'noun',
                 'Bullies torment the weak.',               'verb'),
    ('prospect',  'The prospect of rain loomed.',          'noun',
                  'Miners prospect for gold.',              'verb'),
    ('process',   'Follow the proper process.',            'noun',
                  'They process the data overnight.',       'verb'),
    ('proceeds',  'The proceeds went to charity.',         'noun',
                  'She proceeds with caution.',             'verb'),

    # --- voiced/voiceless s/z and th ---
    ('excuse',   'That is a poor excuse.',                 'noun /s/',
                 'Please excuse my absence.',               'verb /z/'),
    ('misuse',   'It was a misuse of power.',              'noun /s/',
                 'Do not misuse the tool.',                 'verb /z/'),
    ('diffuse',  'The light was soft and diffuse.',        'adj /s/',
                 'The gas will diffuse quickly.',           'verb /z/'),
    ('mouth',    'She covered her mouth.',                 'noun /th/',
                 'Do not mouth the words.',                 'verb /dh/'),

    # --- -ate verb vs noun/adjective (extra) ---
    ('graduate',  'She is a recent graduate.',             'noun /ət/',
                  'They graduate in June.',                 'verb /eɪt/'),
    ('delegate',  'Send a delegate to the summit.',        'noun /ət/',
                  'Good leaders delegate tasks.',           'verb /eɪt/'),
    ('intimate',  'They shared an intimate moment.',       'adj /ət/',
                  'He did intimate that he knew.',          'verb /eɪt/'),
    ('aggregate', 'Look at the aggregate total.',          'adj /ət/',
                  'They aggregate the scores.',             'verb /eɪt/'),
    ('animate',   'He drew an animate figure.',            'adj /ət/',
                  'They animate the characters.',           'verb /eɪt/'),
    ('predicate', 'Find the predicate of the sentence.',   'noun /ət/',
                  'Rights predicate on duties.',            'verb /eɪt/'),
    ('syndicate', 'A crime syndicate ran the docks.',      'noun /ət/',
                  'They syndicate the column widely.',      'verb /eɪt/'),

    # --- -ed adjective vs verb past ---
    ('learned',   'He is a learned professor.',            'adj /ɪd/',
                  'She learned the truth quickly.',         'verb /d/'),
    ('blessed',   'It was a blessed event.',               'adj /ɪd/',
                  'The priest blessed the crowd.',          'verb /t/'),
    ('aged',      'An aged man sat there.',                 'adj /ɪd/',
                  'The cheese aged for years.',             'verb /d/'),
    ('dogged',    'She showed dogged persistence.',        'adj /ɪd/',
                  'Bad luck dogged him all year.',          'verb /d/'),
    ('beloved',   'Our beloved leader spoke.',             'adj /ɪd/',
                  'He beloved her once.',                   'verb /d/'),
    ('crooked',   'They walked a crooked path.',           'adj /ɪd/',
                  'He crooked his finger.',                 'verb /t/'),
    ('supposed',  'He is supposed to be here.',            'adj /ɪd/',
                  'They supposed it was true.',             'verb /d/'),
    ('cursed',    'It was a cursed place.',                 'adj /ɪd/',
                  'He cursed under his breath.',            'verb /t/'),
    ('ragged',    'She wore a ragged coat.',               'adj /ɪd/',
                  'Worry ragged at his nerves.',            'verb /d/'),

    # --- vowel/other classics not in main audit ---
    ('entrance',  'They met at the entrance.',             'noun EN-trance',
                  'Her voice can entrance a crowd.',        'verb en-TRANCE'),
    ('invalid',   'The coupon is invalid.',                'adj in-VAL-id',
                  'He cared for the invalid.',              'noun IN-va-lid'),
    ('number',    'Pick a small number.',                  'noun NUM-ber',
                  'My foot grew number than before.',       'adj NUMB-er'),
    ('second',    'Wait one second please.',               'noun SEC-ond',
                  'I second that motion.',                  'verb se-COND'),
    ('mobile',    'A baby mobile hung above.',             'noun MO-bile',
                  'The unit is highly mobile.',             'adj MO-bil'),
    ('moped',     'She rode a red moped.',                 'noun MO-ped',
                  'He moped around all day.',               'verb MOPED'),
    ('buffet',    'They served a grand buffet.',           'noun buf-FAY',
                  'Waves buffet the shore.',                'verb BUF-fet'),
    ('slough',    'He fell into the slough.',              'noun (swamp)',
                  'Snakes slough their skin.',              'verb (shed)'),
    ('sewer',     'The pipe drained to the sewer.',        'noun SOO-er',
                  'She is a fine sewer of cloth.',          'noun SOH-er'),
    ('putting',   'He was putting on the green.',          'golf PUT-ing',
                  'She is putting it away.',                'place PUH-ting'),
    ('axes',      'The two axes of the graph meet.',       'axis pl AKS-eez',
                  'They sharpened their axes.',             'axe pl AKS-iz'),
    ('lives',     'He lives in the country.',              'verb LIVZ',
                  'They risked their lives.',               'noun LYVZ'),
]


def phon(word, sentence, g2p):
    text = normalize_text(sentence)
    _, toks = g2p(text)
    target = word.lower()
    variants = {target, 'led', 'leed', 'red', 'reed'}
    for t in toks:
        s = (t.text or '').lower().strip('.,;:!?"\'')
        if s == target or (target in s and ('[' in s or '/' in s)):
            return misaki_to_ipa(t.phonemes) if t.phonemes else '?'
    return '(not found)'


def main():
    g2p = en.G2P(trf=False, british=False, fallback=None)
    rows = []
    for word, sa, la, sb, lb in CASES:
        pa = phon(word, sa, g2p)
        pb = phon(word, sb, g2p)
        same = (pa == pb)
        rows.append((same, word, la, pa, lb, pb))
    rows.sort(key=lambda r: (not r[0], r[1]))  # gaps (same=True) first
    gaps = sum(1 for r in rows if r[0])
    print(f"{'FLAG':5} {'WORD':11} {'SENSE-A':14} {'IPA-A':14} {'SENSE-B':14} {'IPA-B':14}")
    print('-' * 78)
    for same, word, la, pa, lb, pb in rows:
        flag = 'SAME!' if same else 'diff'
        print(f"{flag:5} {word:11} {la:14} {pa:14} {lb:14} {pb:14}")
    print(f"\n{gaps} of {len(rows)} produced IDENTICAL phonemes for both senses (likely gaps).")


if __name__ == '__main__':
    main()
