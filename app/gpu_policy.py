"""Require full cloud GPU residency before asking an Ollama model to generate text."""
import json
import urllib.request


def require_cloud_gpu(url, request, actual_name):
    if request.get('keep_alive') == 0:
        return request
    options = {**request.get('options', {}), 'num_gpu': 999}

    def post(payload):
        message = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(message, timeout=900) as response:
            return json.load(response)

    # No prompt is sent until the loaded model is confirmed to reside entirely on GPU.
    loaded = post({'model': request['model'], 'stream': False, 'keep_alive': '5m', 'options': options})
    with urllib.request.urlopen(url.rsplit('/', 1)[0] + '/ps', timeout=30) as response:
        status = json.load(response)
    model = next((m for m in status.get('models', [])
                  if m.get('name') == actual_name or m.get('model') == actual_name), {})
    size, vram = model.get('size', 0), model.get('size_vram', 0)
    if (not loaded or not isinstance(size, (int, float)) or not isinstance(vram, (int, float))
            or size <= 0 or vram < size):
        try:
            post({'model': request['model'], 'keep_alive': 0})
        finally:
            raise RuntimeError('Modellen ryms inte helt på moln-GPU; CPU-reserv är inte tillåten.')
    return {**request, 'options': options}
