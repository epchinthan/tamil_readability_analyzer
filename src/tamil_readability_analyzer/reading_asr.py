"""Tamil ASR adapter for reading assessment.

Supported now:
  - OpenAI transcription API via OPENAI_API_KEY
  - OpenAI-compatible Whisper large-v3 API via TAMIL_READING_WHISPER_API_KEY
  - custom command via TAMIL_READING_ASR_CMD
  - whisper.cpp via WHISPER_CPP_BIN + WHISPER_CPP_MODEL

The custom command may contain {audio}, {wav}, and {lang}; it should print the
Tamil transcript to stdout.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


class ASRNotConfigured(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


def convert_to_wav(audio_path: str) -> str:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ASRNotConfigured('ffmpeg is required to convert browser audio to 16 kHz mono WAV.')
    wav_path = str(Path(tempfile.gettempdir()) / (Path(audio_path).stem + '_16k.wav'))
    proc = _run([ffmpeg, '-y', '-i', audio_path, '-ac', '1', '-ar', '16000', wav_path], timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'Audio conversion failed.')
    return wav_path


def _multipart_form(fields: dict[str, str], files: dict[str, str]) -> tuple[bytes, str]:
    boundary = '----TamilReadingASRBoundary' + os.urandom(12).hex()
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode('utf-8'))
        body.extend(b'\r\n')
    for name, path in files.items():
        filename = Path(path).name
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
        body.extend(f'Content-Type: {content_type}\r\n\r\n'.encode())
        body.extend(Path(path).read_bytes())
        body.extend(b'\r\n')
    body.extend(f'--{boundary}--\r\n'.encode())
    return bytes(body), f'multipart/form-data; boundary={boundary}'


def _api_transcribe(
    audio_path: str,
    *,
    api_key: str,
    api_url: str,
    model: str,
    lang: str,
    engine_label: str,
) -> dict:
    if not api_key:
        raise ASRNotConfigured(f'{engine_label} is not configured. Set the API key environment variable first.')

    payload, content_type = _multipart_form(
        {
            'model': model,
            'language': lang,
            'response_format': 'json',
        },
        {'file': audio_path},
    )
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': content_type,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            raw = resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        details = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{engine_label} transcription failed: {details or e.reason}') from e
    except urllib.error.URLError as e:
        raise RuntimeError(f'{engine_label} transcription failed: {e.reason}') from e

    try:
        data = json.loads(raw)
        transcript = data.get('text') or data.get('transcript') or ''
    except json.JSONDecodeError:
        transcript = raw.strip()
    return {'engine': f'{engine_label} ({model})', 'transcript': transcript.strip()}


def _setting(config: dict | None, key: str, env_name: str, default: str = '') -> str:
    if config and str(config.get(key, '')).strip():
        return str(config.get(key, '')).strip()
    return os.environ.get(env_name, default).strip()


def transcribe(audio_path: str, lang: str = 'ta', mode: str = 'auto', config: dict | None = None) -> dict:
    mode = (mode or 'auto').strip().lower()
    if mode == 'openai':
        return _api_transcribe(
            audio_path,
            api_key=_setting(config, 'openai_api_key', 'OPENAI_API_KEY'),
            api_url=_setting(config, 'openai_url', 'OPENAI_TRANSCRIPTION_URL', 'https://api.openai.com/v1/audio/transcriptions'),
            model=_setting(config, 'openai_model', 'OPENAI_TRANSCRIPTION_MODEL', 'gpt-4o-transcribe'),
            lang=lang,
            engine_label='OpenAI API',
        )

    if mode in {'groq_large_v3', 'groq-large-v3'}:
        whisper_model = 'whisper-large-v3'
        engine_label = 'Groq Whisper Large V3'
    elif mode in {'groq_large_v3_turbo', 'groq-large-v3-turbo'}:
        whisper_model = 'whisper-large-v3-turbo'
        engine_label = 'Groq Whisper Large V3 Turbo'
    else:
        whisper_model = _setting(config, 'whisper_api_model', 'TAMIL_READING_WHISPER_API_MODEL', 'whisper-large-v3')
        engine_label = 'Whisper API'

    if mode in {'whisper_api', 'whisper-large-v3', 'large-v3-api', 'groq_large_v3', 'groq-large-v3', 'groq_large_v3_turbo', 'groq-large-v3-turbo'}:
        return _api_transcribe(
            audio_path,
            api_key=(
                _setting(config, 'whisper_api_key', 'TAMIL_READING_WHISPER_API_KEY')
                or os.environ.get('GROQ_API_KEY', '').strip()
                or os.environ.get('WHISPER_API_KEY', '').strip()
            ),
            api_url=_setting(config, 'whisper_api_url', 'TAMIL_READING_WHISPER_API_URL', 'https://api.groq.com/openai/v1/audio/transcriptions'),
            model=whisper_model,
            lang=lang,
            engine_label=engine_label,
        )

    custom = os.environ.get('TAMIL_READING_ASR_CMD', '').strip()
    wav_path = ''
    if mode in {'auto', 'custom'} and custom:
        wav_path = convert_to_wav(audio_path) if '{wav}' in custom else ''
        cmd = custom.format(audio=audio_path, wav=wav_path, lang=lang)
        proc = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or 'ASR command failed.')
        return {'engine': 'custom', 'transcript': proc.stdout.strip()}

    whisper_bin = os.environ.get('WHISPER_CPP_BIN', '').strip()
    model = os.environ.get('WHISPER_CPP_MODEL', '').strip()
    if mode not in {'auto', 'local', 'whisper_cpp'}:
        raise ASRNotConfigured(f"Unknown ASR mode: {mode}")
    if not whisper_bin or not model:
        raise ASRNotConfigured(
            'Tamil ASR is not configured. Set WHISPER_CPP_BIN and WHISPER_CPP_MODEL, '
            'or set TAMIL_READING_ASR_CMD.'
        )
    if not os.path.exists(whisper_bin):
        raise ASRNotConfigured(f'WHISPER_CPP_BIN not found: {whisper_bin}')
    if not os.path.exists(model):
        raise ASRNotConfigured(f'WHISPER_CPP_MODEL not found: {model}')

    wav_path = convert_to_wav(audio_path)
    out_prefix = str(Path(tempfile.gettempdir()) / (Path(audio_path).stem + '_asr'))
    proc = _run([
        whisper_bin, '-m', model, '-f', wav_path,
        '-l', lang, '-otxt', '-of', out_prefix, '--no-timestamps',
    ], timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'whisper.cpp transcription failed.')
    txt_path = out_prefix + '.txt'
    transcript = Path(txt_path).read_text(encoding='utf-8').strip() if os.path.exists(txt_path) else proc.stdout.strip()
    return {'engine': 'whisper.cpp', 'transcript': transcript}
