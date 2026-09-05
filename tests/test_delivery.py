import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from bulk_enrich.cli import main
from bulk_enrich.config import load_campaign
from bulk_enrich.delivery import run_render_only
from bulk_enrich.focus import CommercialFocusTable
from bulk_enrich.hooks import TitleHookTable
from bulk_enrich.pipeline import RunOptions, run_enrichment
from tests.test_pipeline import MappingDomainEnricher
from tests.test_sequence import sequence_config

ROOT = Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        data = copy.deepcopy(load_campaign(ROOT/'campaigns/examples/scale-olympus.json').data)
        data['personalization']['focus_rules_file'] = str(ROOT/'campaigns/examples/scale-olympus-focus.csv')
        data['quality']['max_body_words'] = 150
        for angle in data['personalization']['angles']:
            for template in angle['templates']:
                template['pitch'] = 'We can introduce you to {{buyer_phrase}}.'
        data['sequence'] = sequence_config().data['sequence']
        self.path = self.p/'campaign.json';self.path.write_text(json.dumps(data))
        self.campaign = load_campaign(self.path)
        self.input = self.p/'leads.csv'
        self.input.write_text('Email,Email status,First name,Job title,Company name,Website\n'
                             'amy@example.com,verified,Amy,Founder,Core Adviser,core.example\n'
                             'ben@example.com,verified,Ben,Founder,Core Adviser,core.example\n'
                             'c@example.com,verified,Chris,Founder,Off Niche,off.example\n')
        self.output = self.p/'audit.csv';self.smart = self.p/'smartlead.csv'

    def run_source(self):
        return run_enrichment(input_path=self.input,output_path=self.output,campaign=self.campaign,
            title_hooks=TitleHookTable.load(ROOT/'config/title-hooks.csv'),
            commercial_focuses=CommercialFocusTable.load(self.campaign.focus_rules_path),
            options=RunOptions(cache_dir=self.p/'cache',smartlead_output_path=self.smart),
            domain_enricher=MappingDomainEnricher())

    def test_full_run_exports_only_complete_unique_sequences_and_offline_replays(self):
        report = self.run_source()
        self.assertEqual(report['delivery']['included'],1)
        self.assertEqual(report['delivery']['held'],2)
        with self.smart.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
        self.assertTrue(rows[0]['personalized_email'].startswith('Hi Amy - '))
        self.assertIn('"no thanks"',rows[0]['followup_3b'])
        self.assertEqual(rows[0]['personalized_email'].count('p.s.'),1)
        # Any attempt to research or invoke a CLI binary must fail this test.
        with patch('bulk_enrich.cli.provider_ready',side_effect=AssertionError('provider called')), \
             patch('bulk_enrich.pipeline._enrich_domains',side_effect=AssertionError('fetch called')), \
             patch('subprocess.run',side_effect=AssertionError('real binary called')), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main(['--input',str(self.output),'--output',str(self.p/'again.csv'),
                '--campaign',str(self.path),'--render-only','--allow-test-campaign',
                '--smartlead-output',str(self.p/'again-smart.csv')])
        self.assertEqual(status,0)
        self.assertEqual(self.smart.read_bytes(),(self.p/'again-smart.csv').read_bytes())

    def test_bad_followup_is_held_and_can_be_repaired_without_research(self):
        self.campaign.data['sequence']['followups']['followup_3b']='We guarantee five qualified meetings every week.'
        report=self.run_source();self.assertEqual(report['delivery']['included'],0)
        result=run_render_only(input_path=self.output,output_path=self.p/'fixed.csv',
            campaign=load_campaign(self.path),options=RunOptions(cache_dir=self.p/'cache',smartlead_output_path=self.p/'fixed-smart.csv'))
        self.assertEqual(result['delivery']['included'],1)
        self.assertEqual(result['llm_focus']['calls'],0)

    def test_replay_rejects_changed_audit_and_targeting_before_writing(self):
        self.run_source()
        for section,key,value in [('offer','audience','different buyers'),('personalization','min_confidence',0.9)]:
            cfg=load_campaign(self.path);cfg.data[section][key]=value
            with self.assertRaisesRegex(ValueError,'classification inputs changed'):
                run_render_only(input_path=self.output,output_path=self.p/'rejected.csv',campaign=cfg,options=RunOptions(cache_dir=self.p/'cache'))
            self.assertFalse((self.p/'rejected.csv').exists())
        self.output.write_text(self.output.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'audit changed'):
            run_render_only(input_path=self.output,output_path=self.p/'rejected.csv',campaign=self.campaign,options=RunOptions(cache_dir=self.p/'cache'))

    def test_output_collision_is_rejected_before_research(self):
        with patch('bulk_enrich.pipeline._enrich_domains',side_effect=AssertionError('fetched')):
            with self.assertRaisesRegex(ValueError,'distinct'):
                run_enrichment(input_path=self.input,output_path=self.output,campaign=self.campaign,
                    title_hooks=TitleHookTable.load(ROOT/'config/title-hooks.csv'),
                    commercial_focuses=CommercialFocusTable.load(self.campaign.focus_rules_path),
                    options=RunOptions(cache_dir=self.p/'cache',smartlead_output_path=self.input))

    def test_ps_change_replays_without_duplicate_ps_or_changed_evidence(self):
        self.run_source()
        self.campaign.data['sequence']['ps_variants']=['p.s. if you would prefer no more emails, reply "no thanks" and I will remove you.']
        result=run_render_only(input_path=self.output,output_path=self.p/'new.csv',campaign=self.campaign,
            options=RunOptions(cache_dir=self.p/'cache',smartlead_output_path=self.p/'new-smart.csv'))
        self.assertEqual(result['delivery']['included'],1)
        with (self.p/'new-smart.csv').open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['personalized_email'].count('p.s.'),1)
        with self.output.open(encoding='utf-8-sig') as f: original=list(csv.DictReader(f))
        with (self.p/'new.csv').open(encoding='utf-8-sig') as f: revised=list(csv.DictReader(f))
        self.assertEqual([r['company_fit_evidence'] for r in original],[r['company_fit_evidence'] for r in revised])
