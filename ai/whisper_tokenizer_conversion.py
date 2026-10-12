"""Complete only standard Whisper timestamp tokens for offline CT2 diagnostics."""
import argparse
import hashlib
import json
from pathlib import Path


def missing_timestamp_tokens(source_vocab, converted_vocab):
    if len(set(converted_vocab)) != len(converted_vocab):
        raise ValueError('Duplicate converted tokens')
    target = {token: index for index, token in enumerate(converted_vocab)}
    if any(target.get(token) != index for token, index in source_vocab.items()):
        raise ValueError('Existing source token IDs differ from the converted model')
    missing = [token for token in converted_vocab if token not in source_vocab]
    if not missing:
        return []
    expected = [f'<|{index / 50:.2f}|>' for index in range(1501)]
    if missing != expected or converted_vocab[-1501:] != expected:
        raise ValueError('Only the complete standard 0–30 s timestamp suffix may be added')
    if sorted(source_vocab.values()) != list(range(len(source_vocab))):
        raise ValueError('Source token IDs must be contiguous')
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--converted', type=Path, required=True)
    args = parser.parse_args()
    output = args.converted / 'tokenizer.json'
    if output.exists():
        raise FileExistsError(output)
    from transformers import WhisperTokenizerFast
    tokenizer = WhisperTokenizerFast.from_pretrained(str(args.source), local_files_only=True)
    vocabulary = json.loads((args.converted / 'vocabulary.json').read_text(encoding='utf-8'))
    missing = missing_timestamp_tokens(tokenizer.get_vocab(), vocabulary)
    if missing:
        tokenizer.add_tokens(missing, special_tokens=True)
    if tokenizer.get_vocab() != {token: index for index, token in enumerate(vocabulary)}:
        raise ValueError('Full tokenizer/model ID comparison failed')
    tokenizer.backend_tokenizer.save(str(output))
    print(json.dumps({'added_timestamp_tokens': len(missing), 'vocabulary_tokens': len(vocabulary),
                      'all_ids_match': True, 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()
