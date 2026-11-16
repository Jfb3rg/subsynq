"""Route speech detection to the selected cloud backend without a local model fallback."""
import dataclasses
import json
import math
import urllib.request


def cloud_speech_timestamps(base, audio, options, *, expected_device=None, sampling_rate=16000):
    import numpy as np
    if sampling_rate != 16000:
        raise RuntimeError("Moln-VAD kräver 16 kHz ljud.")
    samples = np.asarray(audio, dtype="<f4")
    if samples.ndim != 1 or not np.isfinite(samples).all() or samples.nbytes > 64 * 1024 * 1024:
        raise RuntimeError("Ogiltigt ljud för moln-VAD.")
    values = dataclasses.asdict(options)
    values = {key: value for key, value in values.items()
              if not (isinstance(value, float) and math.isinf(value))}
    request = urllib.request.Request(base + '/vad', data=samples.tobytes(), method='POST',
                                     headers={'Content-Type': 'application/octet-stream',
                                              'X-SubSynq-Vad-Options': json.dumps(values, allow_nan=False)})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            result = json.load(response)
    except Exception as error:
        raise RuntimeError("Moln-VAD misslyckades; ingen lokal CPU-reserv används.") from error
    if (not isinstance(result, dict) or result.get('status') != 'ok'
            or result.get('device') not in ('cpu', 'cuda')
            or (expected_device and result.get('device') != expected_device)):
        raise RuntimeError("Moln-VAD svarade inte från vald hårdvara.")
    spans = result.get('segments')
    if not isinstance(spans, list):
        raise RuntimeError("Moln-VAD lämnade ogiltiga talintervall.")
    previous = 0
    for span in spans:
        if (not isinstance(span, dict) or set(span) != {'start', 'end'}
                or any(type(span[key]) is not int for key in ('start', 'end'))
                or not previous <= span['start'] < span['end'] <= len(samples)):
            raise RuntimeError("Moln-VAD lämnade ogiltiga talintervall.")
        previous = span['end']
    return spans
