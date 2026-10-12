"""Catch binary audio-package incompatibilities in the built worker image."""

import io
import unittest
import wave

from faster_whisper.audio import decode_audio


class AudioDecodeDependencyTests(unittest.TestCase):
    def test_faster_whisper_can_open_pcm_wav(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\x00\x00' * 1600)
        data.seek(0)
        samples = decode_audio(data, sampling_rate=16000)
        self.assertEqual(len(samples), 1600)


if __name__ == '__main__':
    unittest.main()
