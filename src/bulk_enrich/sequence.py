"""Campaign-owned sequence rendering; no research, model calls or sending."""
from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from bulk_enrich.llm_focus import _unapproved_claim

if TYPE_CHECKING:
    from bulk_enrich.config import CampaignConfig

FOLLOWUPS = ('followup_2a', 'followup_2b', 'followup_3a', 'followup_3b')
SEQUENCE_FIELDS = (*FOLLOWUPS, 'sequence_status', 'sequence_error')
FIELDS = {'first_name', 'company_name', 'company_short_name', 'company_focus',
          'buyer_phrase', 'sender_name', 'cta', 'risk_reversal'}
MERGE = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')


def validate_sequence(data: dict) -> None:
    seq = data.get('sequence')
    if seq is None:
        return
    if not isinstance(seq, dict):
        raise ValueError('sequence must be an object')
    allowed = {'greeting', 'followups', 'neutral_followups', 'ps_variants',
               'max_followup_words', 'editorial_replacements', 'company_name_overrides'}
    if set(seq) - allowed:
        raise ValueError('unknown sequence configuration fields')
    if seq.get('greeting', 'inline') not in ('inline', 'paragraph'):
        raise ValueError('sequence.greeting must be inline or paragraph')
    limit = seq.get('max_followup_words', 55)
    if type(limit) is not int or not 10 <= limit <= 200:
        raise ValueError('sequence.max_followup_words must be between 10 and 200')
    for key in ('followups', 'neutral_followups'):
        templates = seq.get(key, {})
        if not isinstance(templates, dict) or (key == 'followups' or templates) and set(templates) != set(FOLLOWUPS):
            raise ValueError(f'sequence.{key} must contain all four follow-up variants')
        for text in templates.values():
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f'sequence.{key} templates must be nonempty strings')
            if set(MERGE.findall(text)) - FIELDS:
                raise ValueError(f'unknown merge field in sequence.{key}')
            if re.search(r'(?im)^p\.s\.', text) or '{{sender_name}}' in text:
                raise ValueError('follow-up templates must omit signature and P.S.; renderer appends them')
    ps = seq.get('ps_variants', [])
    if not isinstance(ps, list) or any(not isinstance(x, str) or not x.strip() for x in ps):
        raise ValueError('sequence.ps_variants must be an array of nonempty strings')
    for text in ps:
        if not text.startswith('p.s. ') or '\n' in text or '{{' in text:
            raise ValueError('P.S. variants must be single literal lines beginning p.s. ')
    if len(ps) != len(set(ps)):
        raise ValueError('P.S. variants must be distinct')
    for key in ('editorial_replacements', 'company_name_overrides'):
        edits = seq.get(key, {})
        if not isinstance(edits, dict):
            raise ValueError(f'sequence.{key} must be an object')
        for original, edit in edits.items():
            if (not original.strip() or not isinstance(edit, dict)
                    or set(edit) != {'text', 'reason'}
                    or any(not isinstance(v, str) or not v.strip() for v in edit.values())):
                raise ValueError(f'sequence.{key} requires text and reason for each exact replacement')
    if seq.get('greeting', 'inline') == 'inline':
        body = data['email']['body']
        prefix = 'Hi {{first_name}},\n\n{{' + data['personalization']['output_field'] + '}}'
        if not body.startswith(prefix):
            raise ValueError('inline sequence greeting requires the standard Hi/pitch body prefix')


def editorial_context(campaign: CampaignConfig, context: dict, domain: str) -> dict:
    result = dict(context)
    seq = campaign.data.get('sequence', {})
    for field in ('company_focus', 'buyer_phrase'):
        original = str(result.get(field, ''))
        result[field] = seq.get('editorial_replacements', {}).get(original, {}).get('text', original)
    edit = seq.get('company_name_overrides', {}).get(domain)
    if edit:
        result['company_name'] = edit['text']
        result['company_short_name'] = edit['text']
    return result


def inline_body(campaign: CampaignConfig, body: str) -> str:
    if campaign.data.get('sequence', {}).get('greeting') == 'paragraph' or 'sequence' not in campaign.data:
        return body
    match = re.match(r'^(Hi [^\n]+),\n\n(\S+)', body)
    if not match:
        raise ValueError('malformed inline greeting')
    word = match[2]
    # Lowercase sentence starters only. Preserve I, names and acronyms.
    if word in {'We', 'Would', 'Can', 'Could', 'Are', 'Do', 'Have', 'Want', 'Is'}:
        word = word.lower()
    return match[1] + ' - ' + word + body[match.end():]


def render_sequence(campaign: CampaignConfig, context: dict, domain: str, first_body: str) -> dict[str, str]:
    from bulk_enrich.renderer import render_template, word_count
    from bulk_enrich.config import forbidden_copy_character

    seq = campaign.data.get('sequence')
    if seq is None:
        return {'personalized_email': first_body}
    context = editorial_context(campaign, context, domain)
    context['sender_name'] = campaign.data['sender']['name']
    sender = str(context['sender_name'])
    templates = seq['followups']
    required = set().union(*(set(MERGE.findall(x)) for x in templates.values()))
    if any(not str(context.get(k, '')).strip() for k in required):
        templates = seq.get('neutral_followups', {})
        if not templates:
            raise ValueError('missing sequence slots and no approved neutral follow-ups')
    bodies = {'personalized_email': first_body}
    for field, template in templates.items():
        if any(not str(context.get(k, '')).strip() for k in MERGE.findall(template)):
            raise ValueError(f'{field}: missing or empty merge field')
        bodies[field] = render_template(template, context).strip() + '\n\n' + sender
    variants = seq.get('ps_variants', [])
    base = int(hashlib.sha256(domain.encode()).hexdigest(), 16)
    claims = tuple(str(x) for x in campaign.data['offer'].get('approved_claims', []))
    claims += (str(context.get('cta', '')), str(context.get('risk_reversal', '')))
    for index, (field, body) in enumerate(bodies.items()):
        if body.splitlines().count(sender) != 1 or not body.endswith('\n\n' + sender):
            raise ValueError(f'{field}: missing or duplicate signature')
        if re.search(r'(?im)^p\.s\.', body):
            raise ValueError(f'{field}: duplicate P.S.')
        # Research slots and editorial overrides cannot introduce commercial promises.
        for slot in ('company_focus', 'buyer_phrase'):
            if _unapproved_claim(str(context.get(slot, '')), claims):
                raise ValueError(f'{field}: unapproved claim in {slot}')
        if field in FOLLOWUPS and _unapproved_claim(body.replace(str(context.get('company_name', '')), ''), claims):
            raise ValueError(f'{field}: unapproved figure or commercial promise')
        if variants:
            body += '\n\n' + variants[(base + index) % len(variants)]
        limit = campaign.max_body_words if field == 'personalized_email' else seq.get('max_followup_words', 55)
        if word_count(body) > limit:
            raise ValueError(f'{field}: exceeds {limit} words including P.S.')
        if '{{' in body or '}}' in body or '\\n' in body or not body.strip():
            raise ValueError(f'{field}: unresolved or malformed copy')
        if forbidden_copy_character(body) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]', body):
            raise ValueError(f'{field}: forbidden character')
        check = body.replace(str(context.get('company_name', '')), '').casefold()
        if any(phrase in check for phrase in campaign.banned_phrases):
            raise ValueError(f'{field}: banned wording')
        bodies[field] = body
    return {**bodies, 'sequence_status': 'ready', 'sequence_error': ''}
