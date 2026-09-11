"""Prepare the tokenizer with an integrity-checked official mirror fallback."""
from __future__ import annotations

import hashlib
from urllib.request import urlopen


def ensure_tokenizer():
    from babeldoc.assets.embedding_assets_metadata import TIKTOKEN_CACHES
    from babeldoc.const import TIKTOKEN_CACHE_FOLDER

    name = "fb374d419588a4632f3f557e76b4b70aebbca790"
    expected = TIKTOKEN_CACHES[name]
    path = TIKTOKEN_CACHE_FOLDER / name
    if path.is_file() and hashlib.sha3_256(path.read_bytes()).hexdigest() == expected:
        return
    urls = [
        "https://raw.githubusercontent.com/dotnet/machinelearning/main/src/Microsoft.ML.Tokenizers.Data.O200kBase/Data/o200k_base.tiktoken",
        "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
    ]
    errors = []
    for url in urls:
        try:
            with urlopen(url, timeout=30) as response:
                data = response.read()
            if hashlib.sha3_256(data).hexdigest() != expected:
                raise ValueError("分词资源完整性校验失败")
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".download")
            temp.write_bytes(data)
            temp.replace(path)
            return
        except (OSError, ValueError) as exc:
            errors.append(type(exc).__name__)
    raise RuntimeError("无法下载分词资源，请检查网络后重试。" + ", ".join(errors))


if __name__ == "__main__":
    ensure_tokenizer()
    from babeldoc.assets.assets import warmup
    warmup()
    print("PDF models and fonts are ready.")
