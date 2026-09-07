"""Audit a visible stopping boundary without inventing or selecting choices."""
from .audit import resolve_pointer
from .io import read_json, safe_child


def choice_label(choice):
    # A post-choice branch preview is not a label or already-visible prose.
    return choice.get('label', choice.get('text')) if isinstance(choice, dict) else None


def validate_choice_sources(run_dir, choices):
    issues = []
    for index, choice in enumerate(choices):
        try:
            label = choice_label(choice)
            if not isinstance(label, str) or not label.strip():
                raise ValueError('missing_choice_label')
            path = safe_child(run_dir, choice['native_source'])
            pointer = choice['native_pointer']
            if path.suffix == '.json' and isinstance(pointer, str):
                value = resolve_pointer(read_json(path), pointer)
                actual = value.get('label', value.get('text')) if isinstance(value, dict) else value
                if actual != label:
                    raise ValueError('label_not_at_native_pointer')
            elif isinstance(pointer, dict) and type(pointer.get('line')) is int:
                lines = path.read_text(encoding='utf-8').splitlines()
                line = pointer['line']
                if line < 1 or line > len(lines) or label not in lines[line - 1]:
                    raise ValueError('label_not_at_native_line')
            else:
                raise ValueError('unsupported_choice_pointer')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            issues.append({'choice_index': index, 'issue': str(exc)})
    return bool(choices) and not issues, issues


def audit_output_boundary(run_dir, case, export):
    contract = case.get('output_boundary')
    if contract is None:
        return {'requested': False, 'technical_boundary_passed': None}
    choices = export.get('choices', [])
    labels = [choice_label(c) for c in choices]
    required = [o['text'] for o in case['decisions'][0]['options']]
    valid, issues = validate_choice_sources(run_dir, choices)
    # These flags are emitted only by adapters which actually stop before an
    # option. InfiPlot traverses continue edges; AI4VN follows native jumps.
    reached = (export.get('stop_reason') == 'first_choice'
               or export.get('export_scope') == 'entry_to_first_choice' and bool(choices))
    unexecuted = export.get('selection_executed') is False
    return {
        'requested': True, 'contract': contract, 'native_choice_boundary_reached': reached,
        'selection_executed': export.get('selection_executed'),
        'choice_count': len(choices), 'expected_choice_count': len(required),
        'choice_labels': labels, 'required_choice_labels': required,
        'choice_source_mapping_valid': valid, 'choice_source_mapping_issues': issues,
        'exact_choice_labels_match': all(isinstance(label, str) for label in labels)
                                    and len(labels) == len(required) and set(labels) == set(required),
        'semantic_equivalence': 'not_evaluated_exact_mismatch_does_not_prove_semantic_mismatch',
        'technical_boundary_passed': reached and unexecuted and valid and len(choices) == len(required),
        'native_capability_status': export.get('native_capability_status'),
        'unselected_preview_count': len(export.get('unselected_previews', [])),
        'previews_are_current_path_prose': False,
    }
