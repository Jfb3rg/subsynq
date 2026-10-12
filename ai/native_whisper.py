"""Local CTranslate2 Whisper: one cache, CPU or NVIDIA, no HTTP service."""
import ctranslate2
from faster_whisper import WhisperModel

DEVICE_ID = 'nvidia-cuda'


def cuda_health():
    """Probe the runtime, not subtitle quality. Never load Whisper or Qwen."""
    try:
        if ctranslate2.get_cuda_device_count() < 1:
            return None
        if 'float16' not in ctranslate2.get_supported_compute_types('cuda', 0):
            return None
        return {'status': 'ready', 'device': 'NVIDIA CUDA',
                'device_id': DEVICE_ID, 'backend': 'local-ctranslate2'}
    except (RuntimeError, OSError):
        return None


def load_model(name, device='cpu'):
    if device not in ('cpu', 'cuda'):
        raise ValueError('Whisper-enheten måste vara cpu eller cuda.')
    if device == 'cuda' and cuda_health() is None:
        raise RuntimeError('NVIDIA CUDA är inte redo; ingen tyst CPU-reserv startas.')
    return WhisperModel(name, device=device,
                        compute_type='int8' if device == 'cpu' else 'float16',
                        cpu_threads=6, download_root='/models', local_files_only=True)
