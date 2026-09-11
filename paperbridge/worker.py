"""Isolated translation process. Credentials arrive over stdin, never command arguments."""
from __future__ import annotations

import contextlib
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from paperbridge.core import ACADEMIC_PROMPT, redact, validate_service

PROTOCOL = sys.stdout


def emit(kind: str, **values):
    PROTOCOL.write(json.dumps({"type": kind, **values}, ensure_ascii=False) + "\n")
    PROTOCOL.flush()


def http_error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract a safe, useful message from OpenAI-compatible error responses."""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        detail = payload.get("error", "")
        if isinstance(detail, dict):
            detail = detail.get("message", "")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return ""


def is_local_ollama(url: str) -> bool:
    return urlparse(url).hostname in ("localhost", "127.0.0.1", "::1")


def ollama_chat_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/chat"


def post_json(url: str, payload: dict, timeout: int = 600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def check_service(job):
    url, model, key = validate_service(job["settings"], job.get("key", ""))
    if is_local_ollama(url):
        try:
            data = post_json(ollama_chat_url(url), {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with only the word OK."}],
                "stream": False,
                "think": False,
                "options": {"num_predict": 32, "temperature": 0},
            }, timeout=90)
            content = data["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Ollama 未返回正文，请确认模型支持聊天。")
            return
        except urllib.error.HTTPError as exc:
            detail = http_error_detail(exc)
            if exc.code == 400 and "does not support chat" in detail.lower():
                raise ValueError(
                    f"模型“{model}”不能对话。模型文件可能未下载完整，"
                    f"请在终端运行 ollama pull {model}。Ollama 原文：{detail}"
                ) from None
            raise ValueError(f"Ollama 连接失败（{exc.code}）。{detail}") from None
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Ollama 响应格式异常，请更新 Ollama 后重试。") from None
    body = json.dumps({"model": model, "messages": [
        {"role": "user", "content": "Reply with only the word OK."}
    ], "stream": False}).encode("utf-8")
    req = urllib.request.Request(url + "/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
        content = data["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("服务未返回可用的文本，请检查模型是否支持聊天接口。")
    except urllib.error.HTTPError as exc:
        detail = http_error_detail(exc)
        if exc.code == 400 and "does not support chat" in detail.lower():
            raise ValueError(
                f"模型“{model}”不能对话。Ollama 中的模型文件可能未下载完整，"
                f"请重新拉取模型或改用支持聊天的模型。服务原文：{detail}"
            ) from None
        messages = {401: "密钥无效或已过期", 403: "密钥没有访问权限", 404: "接口地址或模型不存在",
                    429: "请求过于频繁或账户额度不足"}
        summary = messages.get(exc.code, "请检查服务状态")
        suffix = f" 服务原文：{detail}" if detail else ""
        raise ValueError(f"服务连接失败（{exc.code}）：{summary}。{suffix}") from None
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("服务响应格式不兼容，请使用支持 /chat/completions 的接口。") from None


def translate(job):
    from paperbridge.assets import ensure_tokenizer
    ensure_tokenizer()
    from babeldoc import main as backend

    settings = job["settings"]
    url, model, key = validate_service(settings, job.get("key", ""))
    local_ollama = is_local_ollama(url)
    if local_ollama:
        # Ollama's OpenAI-compatible endpoint currently cannot reliably disable
        # Qwen thinking. BabelDOC imports this class into its CLI module, so swap
        # in a native /api/chat adapter before the CLI creates its translators.
        from babeldoc.translator.translator import OpenAITranslator

        class OllamaNativeTranslator(OpenAITranslator):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.native_chat_url = ollama_chat_url(kwargs.get("base_url") or url)

            def native_chat(self, messages, rate_limit_params=None):
                json_mode = bool(
                    self.enable_json_mode_if_requested
                    and (rate_limit_params or {}).get("request_json_mode", False)
                )
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"num_predict": 2048, "temperature": 0},
                }
                if json_mode:
                    payload["format"] = "json"
                response = post_json(self.native_chat_url, payload)
                content = response["message"].get("content", "").strip()
                if not content:
                    raise RuntimeError("Ollama 没有返回译文。请换用支持非思考模式的聊天模型。")
                prompt_tokens = int(response.get("prompt_eval_count", 0))
                completion_tokens = int(response.get("eval_count", 0))
                self.prompt_token_count.inc(prompt_tokens)
                self.completion_token_count.inc(completion_tokens)
                self.token_count.inc(prompt_tokens + completion_tokens)
                return content

            def do_translate(self, text, rate_limit_params=None):
                return self.native_chat(self.prompt(text), rate_limit_params)

            def do_llm_translate(self, text, rate_limit_params=None):
                return self.native_chat(
                    [{"role": "user", "content": text}], rate_limit_params
                )

        backend.OpenAITranslator = OllamaNativeTranslator
    prompt = ACADEMIC_PROMPT
    if settings.get("domain", "").strip():
        prompt += "\nSubject area / terminology guidance: " + settings["domain"].strip()
    argv = ["babeldoc", "--files", job["source"], "--output", job["output"],
            "--working-dir", str(Path(job["output"]) / "work"), "--openai",
            "--openai-base-url", url, "--openai-model", model, "--openai-api-key", key,
            "--lang-in", settings["source"], "--lang-out", settings["target"],
            "--qps", str(settings["qps"]), "--pool-max-workers", str(settings["qps"]),
            "--term-pool-max-workers", str(settings["qps"]),
            "--watermark-output-mode", "no_watermark", "--report-interval", "0.5",
            "--custom-system-prompt", prompt, "--save-auto-extracted-glossary",
            "--add-formula-placehold-hint", "--no-send-temperature"]
    if local_ollama:
        argv += ["--enable-json-mode-if-requested"]
    if job.get("pages"):
        argv += ["--pages", job["pages"]]
    if not settings.get("auto_glossary", True):
        argv += ["--no-auto-extract-glossary"]
    if settings.get("translate_tables"):
        argv += ["--translate-table-text"]
    if settings.get("glossary"):
        argv += ["--glossary-files", settings["glossary"]]
    finished = False

    def handler_factory(*_args, **_kwargs):
        def handler(event):
            nonlocal finished
            kind = event.get("type")
            if kind in ("progress_update", "progress_start", "progress_end"):
                emit("progress", stage=str(event.get("stage", "")),
                     percent=event.get("overall_progress"))
            elif kind == "error":
                raise RuntimeError(str(event.get("error", "翻译引擎发生错误")))
            elif kind == "finish":
                result = event["translate_result"]
                mono = result.no_watermark_mono_pdf_path or result.mono_pdf_path
                dual = result.no_watermark_dual_pdf_path or result.dual_pdf_path
                if not mono or not Path(mono).is_file():
                    raise RuntimeError("翻译引擎没有生成译文 PDF。")
                # Never report success for a PDF that cannot be opened or loses pages.
                import pymupdf
                with pymupdf.open(job["source"]) as original, pymupdf.open(mono) as translated:
                    if len(original) != len(translated):
                        raise RuntimeError("译文页数与原文不一致，已阻止自动加载。")
                    for index in range(len(original)):
                        a, b = original[index].rect, translated[index].rect
                        if abs(a.width - b.width) > 1 or abs(a.height - b.height) > 1:
                            raise RuntimeError(f"第 {index + 1} 页尺寸发生变化，请检查文档兼容性。")
                finished = True
                emit("result", mono=str(mono), dual=str(dual) if dual else "",
                     glossary=str(result.auto_extracted_glossary_path or ""))
        return contextlib.nullcontext(), handler

    backend.create_progress_handler = handler_factory
    sys.argv = argv
    # Keep backend prints/logs separate from our newline-delimited JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        backend.cli()
    if not finished:
        raise RuntimeError("引擎提前结束，没有生成完整译文。")


def main() -> int:
    if hasattr(PROTOCOL, "reconfigure"):
        PROTOCOL.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    job = {}
    try:
        job = json.loads(sys.stdin.readline())
        mode = job.get("mode", "translate")
        if mode == "check":
            check_service(job)
            emit("checked")
        elif mode == "warmup":
            emit("progress", stage="准备版面识别模型和中文字体", percent=None)
            with contextlib.redirect_stdout(sys.stderr):
                from paperbridge.assets import ensure_tokenizer
                ensure_tokenizer()
                from babeldoc.assets.assets import warmup
                warmup()
            emit("warmed")
        else:
            emit("progress", stage="检查翻译服务", percent=None)
            check_service(job)
            emit("progress", stage="加载版面识别模型与字体；首次运行可能需要下载", percent=None)
            translate(job)
        return 0
    except Exception as exc:
        message = redact(str(exc), job.get("key", ""))
        emit("error", message=message or type(exc).__name__)
        logging.error("%s", message)
        return 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
