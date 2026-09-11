"""Comprehensive heteronym gap sweep (phase 2).

Covers canonical English heteronyms NOT already validated by
audit_heteronyms.py (145 cases) or heteronym_discovery.py (60 cases). Same
method: run both senses through normalize_text -> misaki, flag SAME phonemes
(= not disambiguated = likely gap). DIFFER = handled.

Usage: python scripts/heteronym_discovery2.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from autiobooks.misaki import en
from autiobooks.text_processing import normalize_text
from audit_pronunciations import misaki_to_ipa

CASES = [
    # --- N/V stress pairs (initial-stress noun/adj vs final-stress verb) ---
    ('abstract', 'Read the abstract of the paper.',          'Do not abstract the data yet.'),
    ('addict',   'He is a drug addict.',                     'Games can addict young players.'),
    ('ally',     'She is a close ally.',                     'They ally with the rebels.'),
    ('annex',    'They built a new annex.',                  'The empire will annex the island.'),
    ('combine',  'The farmer drove the combine.',            'They combine the ingredients.'),
    ('compact',  'She opened her powder compact.',           'Snow will compact under weight.'),
    ('complex',  'He has a guilt complex.',                  'The rules complex matters further.'),
    ('compress', 'Apply a cold compress.',                   'They compress the files nightly.'),
    ('concert',  'We went to a concert.',                    'They concert their efforts well.'),
    ('confine',  'He stayed within the confines.',           'They confine the dog at night.'),
    ('conscript','He was a young conscript.',                'They conscript farmers for war.'),
    ('consort',  'The queen and her consort.',               'Do not consort with thieves.'),
    ('construct','It is a social construct.',                'They construct the bridge slowly.'),
    ('converse', 'The converse is also true.',               'We converse every morning.'),
    ('default',  'It loaded the default setting.',           'They will default on the loan.'),
    ('dictate',  'He took dictation from the dictate.',      'She will dictate the letter.'),
    ('discard',  'Put it in the discard pile.',              'Please discard the old files.'),
    ('discharge','He got an honorable discharge.',           'They discharge the patient today.'),
    ('discourse','It was a long discourse.',                 'They discourse on philosophy.'),
    ('dispatch', 'Send the dispatch at once.',               'They dispatch the troops now.'),
    ('ferment',  'The wine is in ferment.',                  'They ferment the grapes slowly.'),
    ('fragment', 'It was a small fragment.',                 'Glass will fragment on impact.'),
    ('frequent', 'He is a frequent visitor.',                'They frequent the old tavern.'),
    ('impact',   'The impact was severe.',                   'Costs impact the budget greatly.'),
    ('implant',  'She got a dental implant.',                'They implant the chip surgically.'),
    ('impress',  'The seal left an impress.',                'They impress the judges easily.'),
    ('imprint',  'It left a deep imprint.',                  'They imprint the logo on shirts.'),
    ('incense',  'The temple smelled of incense.',           'His words incense the crowd.'),
    ('insert',   'Read the small insert.',                   'Please insert the card now.'),
    ('intern',   'She is a summer intern.',                  'They intern the prisoners abroad.'),
    ('intrigue', 'It was a tale of intrigue.',               'Mysteries intrigue me greatly.'),
    ('invite',   'I got an invite to the party.',            'They invite us every year.'),
    ('mandate',  'They won a clear mandate.',                'Laws mandate seat belts now.'),
    ('purport',  'I doubt the purport of it.',               'They purport to know the truth.'),
    ('recess',   'The children went to recess.',             'The walls recess into shadow.'),
    ('recoil',   'The gun had a strong recoil.',             'They recoil from the smell.'),
    ('redress',  'They sought legal redress.',               'They redress the old wrongs.'),
    ('regress',  'It was a sad regress.',                    'Patients sometimes regress badly.'),
    ('relapse',  'He suffered a relapse.',                   'Addicts often relapse early.'),
    ('relay',    'They ran the relay race.',                 'They relay the message onward.'),
    ('research', 'She does cancer research.',                'They research the topic deeply.'),
    ('traverse', 'It was a difficult traverse.',             'They traverse the ridge slowly.'),
    ('update',   'I read the latest update.',                'They update the records daily.'),
    ('upgrade',  'She paid for an upgrade.',                 'They upgrade the system tonight.'),
    ('uplift',   'It gave a sense of uplift.',               'Kind words uplift the weary.'),
    ('alloy',    'It is a strong alloy.',                    'They alloy copper with tin.'),
    ('essay',    'She wrote a fine essay.',                  'They essay a bold new plan.'),
    ('finance',  'He works in finance.',                     'They finance the whole project.'),
    ('detail',   'Note every small detail.',                 'They detail the car weekly.'),
    ('overflow', 'There was an overflow of water.',          'The banks overflow each spring.'),
    ('overthrow','It was a bloody overthrow.',               'They overthrow the king at dawn.'),
    ('reprint',  'This is a later reprint.',                 'They reprint the book yearly.'),
    ('rerun',    'It was just a rerun.',                     'They rerun the failed test.'),
    ('refit',    'The ship went in for a refit.',            'They refit the engine fully.'),

    # --- -ate verb vs noun/adjective (final vowel /eɪt/ vs /ət/) ---
    ('coordinate',  'Give me the map coordinate.',           'They coordinate the rescue well.'),
    ('subordinate', 'He is my subordinate.',                 'They subordinate cost to safety.'),
    ('designate',   'She is the designate heir.',            'They designate a meeting spot.'),
    ('consummate',  'He is a consummate liar.',              'They consummate the merger today.'),
    ('desolate',    'It was a desolate moor.',               'War will desolate the land.'),
    ('duplicate',   'Make a duplicate key.',                 'Do not duplicate the work.'),

    # --- -ed adjective vs verb past (syllabic /ɪd/ vs /d/,/t/) ---
    ('jagged',   'The jagged rocks loomed.',                 'Lightning jagged across the sky.'),
    ('rugged',   'He had rugged features.',                  'Storms rugged the coastline.'),
    ('wicked',   'She gave a wicked grin.',                  'He wicked the sweat away.'),
    ('wretched', 'It was a wretched hovel.',                 'They wretched in misery.'),
    ('naked',    'A naked flame flickered.',                 'They naked the truth.'),
    ('alleged',  'The alleged thief fled.',                  'They alleged he was lying.'),
    ('legged',   'A four legged beast appeared.',            'He legged it down the road.'),
    ('crabbed',  'She had crabbed handwriting.',             'He crabbed about the weather.'),

    # --- vowel/other classics not yet tested ---
    ('gill',     'The fish flared its gill.',                'She drank a gill of ale.'),
    ('slaver',   'The dog began to slaver.',                 'The slaver sold his captives.'),
    ('tarry',    'Do not tarry too long.',                   'It left a tarry residue.'),
    ('viola',    'She plays the viola.',                     'Viola loved the duke dearly.'),
    ('bologna',  'He ate a bologna sandwich.',               'They drove through Bologna.'),
    ('prayer',   'She said a quiet prayer.',                 'He is a humble prayer to God.'),
    ('august',   'It was an august assembly.',               'We met last August in Rome.'),
    ('polish',   'She likes to polish silver.',              'He is of Polish descent.'),
    ('primer',   'Apply a coat of primer.',                  'She read the reading primer.'),
    ('routed',   'The army was routed.',                     'They routed the cable upstairs.'),
    ('moderate', 'She is a political moderate.',             'He will moderate the debate.'),
    ('resume',   'Send me your resume.',                     'They resume the meeting now.'),
    ('wound',    'He nursed the wound.',                     'She wound the old clock.'),
    ('peaked',   'She wore a peaked cap.',                   'Sales peaked in July.'),
    ('grease',   'The pan was full of grease.',              'Please grease the squeaky hinge.'),
    ('sake',     'Do it for my sake.',                       'They drank warm sake.'),
]


def phon(word, sentence, g2p):
    text = normalize_text(sentence)
    _, toks = g2p(text)
    target = word.lower()
    for t in toks:
        s = (t.text or '').lower().strip('.,;:!?"\'')
        if s == target or (target in s and ('[' in s or '/' in s)):
            return misaki_to_ipa(t.phonemes) if t.phonemes else '?'
    return '(nf)'


def main():
    g2p = en.G2P(trf=False, british=False, fallback=None)
    rows = []
    for word, sa, sb in CASES:
        pa = phon(word, sa, g2p)
        pb = phon(word, sb, g2p)
        rows.append((pa == pb, word, pa, pb))
    rows.sort(key=lambda r: (not r[0], r[1]))
    gaps = [r for r in rows if r[0]]
    print(f"{'FLAG':5} {'WORD':12} {'IPA-A':16} {'IPA-B':16}")
    print('-' * 52)
    for same, word, pa, pb in rows:
        print(f"{'SAME!' if same else 'diff':5} {word:12} {pa:16} {pb:16}")
    print(f"\n{len(gaps)} of {len(rows)} are SAME (likely gaps): "
          f"{', '.join(r[1] for r in gaps)}")


if __name__ == '__main__':
    main()
