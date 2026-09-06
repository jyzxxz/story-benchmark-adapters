"""Launch untouched AI4VisualNovel source using external benchmark observers."""
import argparse
import functools
import importlib.util
import json
import os
from pathlib import Path
import sys

# This directory belongs to the adapter, never to the native source snapshot.
# Shared execution rules are imported from the same adapter checkout in each
# native subprocess; no helper is copied into the frozen source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ai4vn_observer as benchmark_trace


def install_sdk_observers():
    """Wrap the SDK in memory; original native Python files stay byte-identical."""
    if not benchmark_trace.enabled():
        return
    import openai
    from openai.resources.chat.completions import Completions
    if getattr(openai.OpenAI, '_benchmark_external_installed', False):
        return
    original_init = openai.OpenAI.__init__
    original_create = Completions.create

    @functools.wraps(original_init)
    def initialize(instance, *args, **kwargs):
        if kwargs.get('http_client') is None:
            kwargs.update(benchmark_trace.openai_client_kwargs())
        else:
            client = kwargs['http_client']
            client.event_hooks['request'].append(benchmark_trace.http_request)
            client.event_hooks['response'].append(benchmark_trace.http_response)
        return original_init(instance, *args, **kwargs)

    @functools.wraps(original_create)
    def create(instance, *args, **kwargs):
        if args:
            raise RuntimeError('benchmark_sdk_positional_arguments_unsupported')
        return benchmark_trace.sdk_call('openai', lambda **parameters: original_create(instance, **parameters), kwargs)

    openai.OpenAI.__init__ = initialize
    Completions.create = create
    openai.OpenAI._benchmark_external_installed = True


def attach_receipt_observer(native_main):
    original = native_main.resolve_design_inputs

    @functools.wraps(original)
    def resolve_design_inputs(args):
        result = original(args)
        if benchmark_trace.enabled():
            # Observe the actual decoded native return value without rewriting it.
            receipt = {'boundary': 'native_requirements_reader', 'received_task': result[0], 'oc_character_count': len(result[1]),
                       'requirements_file': args.requirements_file,
                       'observer': 'external_launcher', 'source_mode': 'pristine_copy'}
            directory = Path(os.environ['BENCH_TRACE_DIR'])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f'received-ai4vn-{os.getpid()}.json').write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        return result

    native_main.resolve_design_inputs = resolve_design_inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-root', required=True)
    args, native_args = parser.parse_known_args()
    native_root = Path(args.native_root).resolve()
    if native_args[:1] == ['--']:
        native_args = native_args[1:]
    if benchmark_trace.enabled() and os.getenv('TEXT_PROVIDER', 'google').lower() != 'openai':
        raise RuntimeError('benchmark_provider_http_budget_unsupported')
    os.chdir(native_root)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(native_root))
    install_sdk_observers()
    spec = importlib.util.spec_from_file_location('_benchmark_native_ai4vn_main', native_root / 'main.py')
    native_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native_main)
    attach_receipt_observer(native_main)
    sys.argv = [str(native_root / 'main.py'), *native_args]
    native_main.main()


if __name__ == '__main__':
    main()
