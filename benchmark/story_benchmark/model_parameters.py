"""Explicit common model controls; never accepts prompt/schema/endpoint overrides."""
from copy import deepcopy
from .io import BenchmarkError


def validate_model_parameters(parameters):
    if not isinstance(parameters, dict):
        raise BenchmarkError('model_parameters_must_be_object')
    if set(parameters) - {'thinking', 'reasoning_effort'}:
        raise BenchmarkError('unsupported_common_model_parameter')
    if 'thinking' in parameters:
        value=parameters['thinking']
        if not isinstance(value,dict) or set(value)!={'type'} or value['type'] not in ('enabled','disabled'):
            raise BenchmarkError('invalid_thinking_parameter')
    if 'reasoning_effort' in parameters:
        value=parameters['reasoning_effort']
        if not isinstance(value,str) or value not in ('none','minimal','low','medium','high','xhigh','max'):
            raise BenchmarkError('invalid_reasoning_effort')
        if parameters.get('thinking',{}).get('type')=='disabled':
            raise BenchmarkError('disabled_thinking_conflicts_with_reasoning_effort')
    return deepcopy(parameters)


def apply_model_parameters(payload, parameters):
    parameters=validate_model_parameters(parameters)
    if not isinstance(payload,dict):
        raise BenchmarkError('model_payload_must_be_object')
    result=deepcopy(payload)
    result.update(parameters)
    return result
