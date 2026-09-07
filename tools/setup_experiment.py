#!/usr/bin/env python3
"""Interactive local provider/key setup. Never contacts a provider or prints keys."""
from __future__ import annotations
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.rstrip('/').endswith('/v1')):
        raise ValueError('Supply a credential-free http(s) endpoint ending in /v1')
    return value


def ask(label: str, current: str) -> str:
    return input(f'{label} [{current}]: ').strip() or current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'work/config/batch.local.json')
    parser.add_argument('--secrets',type=Path,default=ROOT/'work/secrets.env')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise ValueError('Interactive setup needs a terminal. For automation, provision the local JSON/env files yourself.')
    from dotenv import dotenv_values, set_key
    sys.path.insert(0,str(ROOT/'benchmark'))
    from story_benchmark.batch import load_batch_config
    config = json.loads(args.config.read_text(encoding='utf-8'))
    saved = dotenv_values(args.secrets,interpolate=False) if args.secrets.exists() else {}
    updates = {}
    names = [config['providers'][role]['api_key_env'] for role in ('text','image','vision')]
    if len(set(names)) != len(names) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) for name in names):
        raise ValueError('Use distinct valid API-key environment variable names for this wizard')
    print('Configure shared text/image/native-vision providers. These URLs will receive the corresponding API keys during generation.')
    print('Nothing is sent now. Image model is fixed to gpt-image-2 by this adapter protocol.')
    for role in ('text','image','vision'):
        provider = config['providers'][role]
        old_url = provider['base_url']
        print('\nProvider role:',role)
        provider['base_url'] = endpoint(ask('Authorized provider URL',old_url))
        if role != 'image':
            provider['model'] = ask('Model name available from that provider',provider['model'])
            controls = ask('Shared model parameters JSON (use {} to clear)',json.dumps(provider.get('parameters',{}),ensure_ascii=False))
            provider['parameters'] = json.loads(controls)
        print('Selected endpoint/model:',provider['base_url'],provider['model'])
        if input('Type USE to accept this destination for this role: ').strip() != 'USE':
            raise ValueError('Destination not accepted; nothing saved')
        name = provider['api_key_env']
        key = getpass.getpass(f'{name} (hidden; Enter keeps saved key only for an unchanged URL): ')
        if any(c in key for c in '\r\n\0'):
            raise ValueError('API keys must be a single line')
        if not key:
            if provider['base_url'] != old_url or not saved.get(name):
                raise ValueError('New/changed endpoint requires an explicitly entered key; no automatic credential reuse')
        else:
            updates[name]=key
        if os.environ.get(name):
            print(f'WARNING: exported {name} overrides secrets.env during runs. Check/unset it when changing credentials.')
    # Validate config without sending requests, then atomically replace each local file.
    args.config.parent.mkdir(parents=True,exist_ok=True)
    args.secrets.parent.mkdir(parents=True,exist_ok=True)
    temp_paths=[]
    try:
        fd,name=tempfile.mkstemp(dir=args.config.parent,prefix='.experiment-config-'); temp_config=Path(name);temp_paths.append(temp_config)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(config,stream,ensure_ascii=False,indent=2);stream.write('\n')
        load_batch_config(temp_config)
        fd,name=tempfile.mkstemp(dir=args.secrets.parent,prefix='.experiment-secrets-'); temp_secrets=Path(name);temp_paths.append(temp_secrets)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(args.secrets.read_text(encoding='utf-8') if args.secrets.exists() else '# Local only. Never commit.\n')
        for name,key in updates.items():
            set_key(str(temp_secrets),name,key,quote_mode='always')
        values=dotenv_values(temp_secrets,interpolate=False)
        if any(values.get(name)!=key for name,key in updates.items()):
            raise ValueError('Credential serialization mismatch; nothing saved')
        os.chmod(temp_secrets,0o600)
        os.replace(temp_secrets,args.secrets)
        os.replace(temp_config,args.config)
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)
    print('Saved local configuration and owner-only credentials. Zero model calls.')
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,OSError,KeyError,EOFError) as exc:
        # Validation exceptions can contain configuration details, so never echo raw exceptions/keys.
        print('Setup failed or cancelled. Check endpoint/model/configuration and local file permissions. No provider request was made.',file=sys.stderr)
        raise SystemExit(2)
