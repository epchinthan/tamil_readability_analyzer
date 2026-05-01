"""Local/offline Tamil ASR adapter for reading assessment.

High quality is achieved by running ASR on the server, not in the browser.
Supported now:
  - custom command via TAMIL_READING_ASR_CMD
  - whisper.cpp via WHISPER_CPP_BIN + WHISPER_CPP_MODEL

The custom command may contain {audio}, {wav}, and {lang}; it should print the
Tamil transcript to stdout.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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


def transcribe(audio_path: str, lang: str = 'ta') -> dict:
    custom = os.environ.get('TAMIL_READING_ASR_CMD', '').strip()
    wav_path = ''
    if custom:
        wav_path = convert_to_wav(audio_path) if '{wav}' in custom else ''
        cmd = custom.format(audio=audio_path, wav=wav_path, lang=lang)
        proc = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or 'ASR command failed.')
        return {'engine': 'custom', 'transcript': proc.stdout.strip()}

    whisper_bin = os.environ.get('WHISPER_CPP_BIN', '').strip()
    model = os.environ.get('WHISPER_CPP_MODEL', '').strip()
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
    ], timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'whisper.cpp transcription failed.')
    txt_path = out_prefix + '.txt'
    transcript = Path(txt_path).read_text(encoding='utf-8').strip() if os.path.exists(txt_path) else proc.stdout.strip()
    return {'engine': 'whisper.cpp', 'transcript': transcript}
