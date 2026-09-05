import copy
import unittest
from pathlib import Path

from bulk_enrich.config import CampaignConfig, load_campaign
from bulk_enrich.renderer import render_email
from bulk_enrich.sequence import FOLLOWUPS, editorial_context, render_sequence, validate_sequence

ROOT = Path(__file__).resolve().parents[1]


def sequence_config():
    data = copy.deepcopy(load_campaign(ROOT/'campaigns/examples/scale-olympus.json').data)
    data['sequence'] = {
        'greeting': 'inline', 'max_followup_words': 55,
        'followups': {k: 'Would an outline for {{company_focus}} help?' for k in FOLLOWUPS},
        'neutral_followups': {k: 'Would an outline help?' for k in FOLLOWUPS},
        'ps_variants': ['p.s. if this isn’t of interest, reply "no thanks" and I’ll take you off my list.',
                        'p.s. if you’d prefer not to hear from me, reply "no thanks" and I’ll take you off my list.'],
    }
    return CampaignConfig(ROOT/'campaigns/examples/scale-olympus.json', data)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.config = sequence_config()
        self.context = {'first_name': 'Ana', 'company_name': 'Brand Potential',
                        'company_focus': 'ai research', 'buyer_phrase': 'technology companies'}
        self.body = 'Hi Ana - we can start a conversation.\n\nDylan'

    def test_inline_greeting_preserves_names_i_and_acronyms(self):
        for pitch, expected in [('We can help.', 'we can help.'), ('I can help.', 'I can help.'),
                                ('IBM uses this.', 'IBM uses this.')]:
            _, body = render_email(self.config, self.context, pitch, '{{company_name}}')
            self.assertTrue(body.startswith('Hi Ana - '+expected))

    def test_complete_sequence_is_deterministic_and_non_mutating(self):
        before = dict(self.context)
        a = render_sequence(self.config, self.context, 'example.test', self.body)
        b = render_sequence(self.config, self.context, 'example.test', self.body)
        self.assertEqual(a, b)
        self.assertEqual(self.context, before)
        for k in ('personalized_email', *FOLLOWUPS):
            self.assertEqual(a[k].count('p.s.'), 1)
            self.assertEqual(a[k].splitlines().count('Dylan'), 1)
            self.assertIn('"no thanks"', a[k])

    def test_explicit_neutral_fallback_for_missing_slots(self):
        self.context['company_focus'] = ''
        result = render_sequence(self.config, self.context, 'example.test', self.body)
        self.assertTrue(result['followup_2a'].startswith('Would an outline help?'))
        del self.config.data['sequence']['neutral_followups']
        with self.assertRaisesRegex(ValueError, 'missing sequence slots'):
            render_sequence(self.config, self.context, 'example.test', self.body)

    def test_bad_followup_blocks_delivery(self):
        self.config.data['sequence']['followups']['followup_3b'] = 'We guarantee five qualified meetings every week.'
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            render_sequence(self.config, self.context, 'example.test', self.body)

    def test_ps_counts_toward_length_and_cannot_be_appended_twice(self):
        self.config.data['sequence']['max_followup_words'] = 10
        with self.assertRaisesRegex(ValueError, 'including P.S.'):
            render_sequence(self.config, self.context, 'example.test', self.body)
        with self.assertRaisesRegex(ValueError, 'signature|P.S.'):
            render_sequence(self.config, self.context, 'example.test', self.body+'\n\np.s. stop')

    def test_editorial_data_does_not_change_evidence_or_source(self):
        self.config.data['sequence']['editorial_replacements'] = {'ai research': {'text': 'ai consulting', 'reason': 'approved wording'}}
        self.config.data['sequence']['company_name_overrides'] = {'example.test': {'text': 'Example', 'reason': 'verified homepage'}}
        self.context['evidence'] = 'ai research';self.context['source'] = 'https://example.test/'
        result = editorial_context(self.config, self.context, 'example.test')
        self.assertEqual(result['company_focus'], 'ai consulting')
        self.assertEqual(result['evidence'], 'ai research')
        self.assertEqual(self.context['company_name'], 'Brand Potential')

    def test_configuration_rejects_unknown_empty_and_duplicate_fields(self):
        for edit in [lambda s:s.update(greeting='broken'),
                     lambda s:s['followups'].update(followup_2a='{{unknown}}'),
                     lambda s:s['followups'].update(followup_2a='{{sender_name}}'),
                     lambda s:s.update(ps_variants=['p.s. hello','p.s. hello'])]:
            cfg = sequence_config(); edit(cfg.data['sequence'])
            with self.assertRaises(ValueError):validate_sequence(cfg.data)

    def test_legacy_configuration_unchanged(self):
        del self.config.data['sequence']
        self.assertEqual(render_sequence(self.config, {}, '', self.body), {'personalized_email': self.body})
        _,body = render_email(self.config,self.context,'We can help.','Hello')
        self.assertTrue(body.startswith('Hi Ana,\n\nWe can help.'))
