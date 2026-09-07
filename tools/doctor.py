#!/usr/bin/env python3
"""Local deployment diagnostics. Never reads secrets.env or calls a model API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ("if_line", "ai4visualnovel", "infiplot")
SOURCE_NAMES = {"if_line": "if_line", "ai4visualnovel": "AI4VisualNovel", "infiplot": "infiplot"}
sys.path.insert(0, str(ROOT / "benchmark"))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean_environment(config=None):
    """Only OS settings survive; no provider secrets, proxy or user startup hooks."""
    keep = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    paths = [str(ROOT / "work/node/bin"), "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    if config:
        for key in ("node_executable", "pnpm_executable", "redis_executable"):
            if config.get(key):
                paths.insert(0, str(Path(config[key]).parent))
    env.update(PATH=os.pathsep.join(dict.fromkeys(paths)), PYTHONDONTWRITEBYTECODE="1",
               PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYGAME_HIDE_SUPPORT_PROMPT="1",
               SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", NEXT_TELEMETRY_DISABLED="1",
               PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "work/browser-cache")))
    return env


def load_config(path):
    from story_benchmark.batch import load_batch_config
    return load_batch_config(path)


def source_report(system, config):
    from story_benchmark.provenance import verify_repository
    lock = json.loads((ROOT / "baseline-lock.json").read_text())
    return verify_repository(Path(config["repo_path"]), lock["systems"][SOURCE_NAMES[system]]["base_commit"], config)


def run_probe(command, env, timeout=45):
    process = None

    def stop_owned_group():
        if process is None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.communicate()

    try:
        process = subprocess.Popen([str(x) for x in command], cwd=ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=os.name == "posix")
        stdout, stderr = process.communicate(timeout=timeout)
        return {"ok": process.returncode == 0, "returncode": process.returncode,
                "stdout": stdout[-16000:], "stderr": stderr[-8000:]}
    except subprocess.TimeoutExpired:
        stop_owned_group()
        return {"ok": False, "error": "local_probe_timeout", "timeout_seconds": timeout,
                "owned_process_group_cleanup_requested": True}
    except KeyboardInterrupt:
        stop_owned_group()
        raise
    except OSError as exc:
        stop_owned_group()
        return {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}


NO_REMOTE_PYTHON = """
import sys
def local_network_only(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('doctor disallows remote network connections')
sys.addaudithook(local_network_only)
"""


def python_probe(executable, modules, env):
    code = NO_REMOTE_PYTHON + """
import importlib, importlib.metadata, json, platform
modules = json.loads(sys.argv[1]); rows = []
for module in modules:
    try:
        value = importlib.import_module(module)
        rows.append({'module': module, 'ok': True, 'version': str(getattr(value, '__version__', 'not_exposed'))})
    except BaseException as exc:
        rows.append({'module': module, 'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)})
print(json.dumps({'python': platform.python_version(), 'executable': sys.executable, 'modules': rows}))
raise SystemExit(0 if all(row['ok'] for row in rows) else 1)
"""
    return run_probe([executable, "-c", code, json.dumps(modules)], env, 90)


def model_probe(executable, path, env):
    path = Path(path)
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "model_missing_prepare_during_installation"}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    code = NO_REMOTE_PYTHON + """
import onnxruntime as ort, json
options = ort.SessionOptions(); options.intra_op_num_threads = 1; options.inter_op_num_threads = 1
session = ort.InferenceSession(sys.argv[1], sess_options=options, providers=['CPUExecutionProvider'])
print(json.dumps({'providers':session.get_providers(), 'inputs':[x.name for x in session.get_inputs()]}))
"""
    return {**run_probe([executable, "-c", code, str(path)], env, 90), "path": str(path),
            "bytes": path.stat().st_size, "sha256": digest.hexdigest(),
            "scope": "local_ONNX_load_only_no_download_or_generation"}


def browser_probe(executable, module, env, out, chromium=None):
    code = """
const fs=require('fs');
const {chromium}=require(process.argv[1]);
(async()=>{
  let browser;
  try {
    const options={headless:true,args:['--disable-background-networking']};
    if(process.argv[3]) options.executablePath=process.argv[3];
    browser=await chromium.launch(options);
    const context=await browser.newContext();
    await context.route('**/*',route=>route.abort());
    const page=await context.newPage();
    await page.setContent('<meta charset="UTF-8"><p>中文环境检查：雨夜信号</p>');
    await page.screenshot({path:process.argv[2]});
    console.log(JSON.stringify({version:browser.version(),headless:true,screenshot:process.argv[2]}));
  } finally {if(browser) await browser.close();}
})().catch(e=>{console.error(String(e));process.exitCode=1;});
"""
    return run_probe([executable, "-e", code, str(module), str(out), chromium or ""], env, 60)


def diagnose(system, config, output):
    env = clean_environment(config)
    checks = []

    def add(name, value):
        checks.append({"check": name, **value})

    add("source_provenance", source_report(system, config))
    fc = shutil.which("fc-list", path=env["PATH"])
    if fc:
        result = run_probe([fc, ":lang=zh", "family"], env)
        result["ok"] = result["ok"] and bool(result.get("stdout", "").strip())
        add("chinese_fonts", result)
    else:
        add("chinese_fonts", {"ok": False, "error": "fontconfig_missing_install_fonts_noto_cjk_and_fontconfig"})
    if system == "if_line":
        executable = config.get("python_executable") or ROOT / "work/envs/if_line/bin/python"
        add("python_dependencies", python_probe(executable,
            ["PIL", "fastapi", "uvicorn", "sqlalchemy", "alembic", "psycopg", "celery", "rembg", "onnxruntime", "aiohttp"], env))
        add("python_dependency_consistency", run_probe([executable, "-m", "pip", "check"], env))
        add("ordinary_user", {"ok": not hasattr(os, "geteuid") or os.geteuid() != 0,
                              "requirement": "initdb refuses root; batch process requires ordinary user"})
        pg = Path(config.get("pg_bin") or "/usr/lib/postgresql/16/bin")
        for name in ("initdb", "pg_ctl", "createdb"):
            add(name, run_probe([pg / name, "--version"], env))
        add("redis", run_probe([config.get("redis_executable") or "redis-server", "--version"], env))
        add("u2net_model", model_probe(executable, Path(config.get("rembg_model_dir") or ROOT / "work/models/if_line") / "u2net.onnx", env))
    elif system == "ai4visualnovel":
        executable = config.get("python_executable") or ROOT / "work/envs/ai4visualnovel/bin/python"
        add("python_dependencies", python_probe(executable,
            ["pygame", "PIL", "openai", "dotenv", "jsonschema", "yaml", "rembg", "onnxruntime"], env))
        add("python_dependency_consistency", run_probe([executable, "-m", "pip", "check"], env))
        add("isnet_anime_model", model_probe(executable,
            Path(config.get("rembg_model_dir") or ROOT / "work/models/ai4visualnovel") / "isnet-anime.onnx", env))
        code = NO_REMOTE_PYTHON + """
import pygame
pygame.display.init()
try:
    surface=pygame.display.set_mode((320,240)); surface.fill((30,60,90))
    pygame.display.flip(); pygame.image.save(surface,sys.argv[1])
    print('pygame dummy display ready')
finally: pygame.quit()
"""
        add("pygame_headless_display", run_probe([executable, "-c", code, str(output / "pygame.png")], env))
        font_code = NO_REMOTE_PYTHON + """
import ast, json, os
from pathlib import Path
import pygame
repo=Path(sys.argv[1]); os.chdir(repo); sys.path.insert(0,str(repo))
source=ast.parse((repo/'game_engine/ui.py').read_text())
function=next(node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=='get_font')
lists={}
for node in function.body:
    if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('local_fonts','font_names'):
        lists[node.targets[0].id]=ast.literal_eval(node.value)
pygame.font.init()
try:
    from game_engine.ui import get_font
    matches={name:pygame.font.match_font(name) for name in lists['font_names']}
    local=[str(repo/name) for name in lists['local_fonts'] if (repo/name).is_file()]
    font=get_font(28)
    image=font.render('中文环境检查：雨夜信号',True,(255,255,255),(30,60,90))
    pygame.image.save(image,sys.argv[2])
    supported=all(value is not None for value in font.metrics('中文雨夜信号'))
    print(json.dumps({'native_function':'game_engine.ui.get_font','local_candidates':local,
        'system_candidate_matches':matches,'glyph_metrics_available':supported,
        'cwd_matches_native_runtime':True,'screenshot':sys.argv[2]},ensure_ascii=False))
    raise SystemExit(0 if supported and (local or any(matches.values())) else 1)
finally: pygame.font.quit()
"""
        add("native_pygame_chinese_font", run_probe([executable, "-c", font_code,
            config["repo_path"], str(output / "pygame-native-font.png")], env))
    else:
        add("pnpm", run_probe([config.get("pnpm_executable") or "pnpm", "--version"], env))
        dependency_root = ROOT / "work/infiplot-deps"
        packages = ("next", "react", "openai", "@supabase/ssr")
        missing = [name for name in packages if not (dependency_root / "node_modules" / name / "package.json").is_file()]
        add("prepared_native_dependencies", {"ok": not missing, "path": str(dependency_root), "missing": missing,
            "limitation": "package presence does not prove offline pnpm store completeness; run smoke_test"})
    if system in ("if_line", "infiplot"):
        node = config.get("node_executable") or ROOT / "work/node/bin/node"
        add("node", run_probe([node, "--version"], env))
        modules = Path(config.get("node_modules") or ROOT / "work/media-tools/node_modules")
        playwright = Path(config.get("playwright_module") or modules / "playwright")
        if system == "if_line":
            missing = [x for x in ("vue", "@vue/compiler-sfc", "esbuild", "playwright") if not (modules / x / "package.json").is_file()]
            add("vue_renderer_packages", {"ok": not missing, "missing": missing, "node_modules": str(modules)})
        add("chromium_actual_launch", browser_probe(node, playwright, env, output / "chromium.png", config.get("chromium_executable")))
    return {"ok": all(check.get("ok") is True for check in checks), "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=("all", *SYSTEMS), default="all")
    parser.add_argument("--config", type=Path, default=ROOT / "work/config/batch.local.json")
    parser.add_argument("--out", type=Path, help="New evidence directory (default work/doctor/<UTC timestamp>)")
    args = parser.parse_args(argv)
    out = (args.out or ROOT / "work/doctor" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")).absolute()
    if out.exists():
        parser.error("--out must be a new directory")
    out.mkdir(parents=True)
    report = {"schema_version": "deployment-doctor.1", "started_at": utc_now(), "platform": platform.platform(),
              "report_directory": str(out), "model_api_calls": 0, "secrets_file_read": False,
              "systems": {}, "limitations": ["No provider authentication or generation test.",
              "Chromium screenshot proves local rendering, not complete native story flow.",
              "Inspect Chinese screenshot visually; font presence alone does not prove every glyph."]}
    try:
        config = load_config(args.config)
        common = ROOT / "work/envs/common/bin/python"
        report["common_python"] = python_probe(common, ["PIL", "pytest", "jsonschema"], clean_environment())
        from story_benchmark.compiler import verify_bundle
        report["bundles"] = [{"path": bundle, "verification": verify_bundle(Path(bundle))} for bundle in config["bundles"]]
        for system in SYSTEMS if args.system == "all" else (args.system,):
            folder = out / system; folder.mkdir()
            try:
                report["systems"][system] = diagnose(system, config["systems"][system], folder)
                # Same native preflight as batch, under a credential-free child.
                # Calling driver.preflight directly avoids checking real key presence.
                probe_env = clean_environment(config["systems"][system])
                probe_env["PYTHONPATH"] = str(ROOT / "benchmark")
                code = NO_REMOTE_PYTHON + """
import json
from pathlib import Path
from story_benchmark.batch import driver_for
settings=json.loads(sys.argv[1]); rows=[]
for bundle in settings['bundles']:
    manifest=json.loads((Path(bundle)/'manifest.json').read_text())
    policy={**settings['policy'], 'window_chars':manifest['output_contract']['window_chars']}
    rows.append(driver_for(settings['system']).preflight(settings['config'],Path(bundle),policy))
print(json.dumps(rows,ensure_ascii=False))
raise SystemExit(0 if all(row['ok'] for row in rows) else 1)
"""
                settings = {"system": system, "config": config["systems"][system],
                            "bundles": config["bundles"], "policy": config["policy"]}
                preflight = run_probe([common, "-c", code, json.dumps(settings)], probe_env, 90)
                report["systems"][system]["checks"].append({"check": "native_batch_preflight_without_credentials", **preflight})
                report["systems"][system]["ok"] = report["systems"][system]["ok"] and preflight["ok"]
            except Exception as exc:
                report["systems"][system] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
        report["ok"] = report["common_python"]["ok"] and all(row["ok"] for row in report["systems"].values())
    except Exception as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    report["finished_at"] = utc_now()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
