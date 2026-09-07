"""Shared input/output contracts. Never generate or repair story content."""
import json
from .io import BenchmarkError, sha256

INPUT_CONTRACT = {
    'version': '3.0', 'task_delivery': 'verbatim_first_creative_request',
    'character_policy': 'exact_declared_cast', 'adapter_story_reinjection': 'none',
}


def expected_output_contract(case):
    if case.get('output_contract', {}).get('version') == '4.0':
        return {'version':'4.0', 'scope':'readable_window',
                'window_chars':case['output_contract'].get('window_chars'),
                'character_metric':'unicode_codepoints_in_new_visible_body',
                'cut_rule':'first_sentence_boundary_at_or_after_threshold',
                'media':'images', 'native_choice_previews':'separate',
                'require_story_ending':False}
    return {
        'version': '3.0', 'scope': 'first_unselected_choice',
        'decision_id': case['decisions'][0]['id'],
        'choice_count': len(case['decisions'][0]['options']),
        'selection_executed': False, 'allow_empty_body': True,
        'native_choice_previews': 'separate',
    }


def validate_contracts(case, required=False):
    present = 'input_contract' in case or 'output_contract' in case
    if not present:
        if required:
            raise BenchmarkError('v3_contract_required')
        return False
    if 'output_boundary' in case:
        raise BenchmarkError('mixed_legacy_and_v3_contract')
    if case.get('input_contract') != INPUT_CONTRACT:
        raise BenchmarkError('invalid_v3_input_contract')
    try:
        expected = expected_output_contract(case)
    except (KeyError, IndexError, TypeError):
        raise BenchmarkError('invalid_v3_decision_contract') from None
    value = case.get('output_contract')
    if isinstance(value,dict) and value.get('version')=='4.0':
        if (value != expected or type(value.get('window_chars')) is not int
                or value['window_chars']<=0 or value.get('require_story_ending') is not False
                or case.get('profile')!='FULL_VN'):
            raise BenchmarkError('invalid_v4_readable_window_contract')
        return True
    if (value != expected or type(value.get('choice_count')) is not int
            or value.get('selection_executed') is not False
            or value.get('allow_empty_body') is not True):
        raise BenchmarkError('invalid_v3_output_contract')
    if case.get('profile') != 'TEXT_CONTINUATION_DEV':
        raise BenchmarkError('unsupported_v3_profile')
    return True


def canonical_case_hash(case):
    return sha256(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
