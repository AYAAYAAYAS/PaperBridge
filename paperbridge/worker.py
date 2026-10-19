"""Isolated translation process. Credentials arrive over stdin, never command arguments."""
from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from string import Template
from urllib.parse import urlparse

import httpx

from paperbridge.core import ACADEMIC_PROMPT, redact, validate_service
from paperbridge.network import force_direct_httpx_for_babeldoc, ssl_context, urlopen

PROTOCOL = sys.stdout

DEEPSEEK_PROMPT_TEMPLATE = Template(
    """$role_block

Translate every input item into $lang_out. Keep each item independent. Preserve every id, tag,
formula placeholder and code token exactly and in the same order. Never return an empty output.
Return only a valid JSON array with exactly one {"id": N, "output": "translation"} object for
each input item. Do not add Markdown or explanations.

$glossary_usage_rules_block$contextual_hints_block$glossary_tables_block

Input JSON:
$json_input_str"""
)


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


def is_deepseek(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "deepseek.com" or host.endswith(".deepseek.com")


def usable_translation(output, structured: bool = False) -> bool:
    if not isinstance(output, str) or not output.strip():
        return False
    if not structured:
        return True
    value = output.strip()
    if value.startswith("<json>") and value.endswith("</json>"):
        value = value[6:-7].strip()
    if value.startswith("```json"):
        value = value[7:]
    elif value.startswith("```"):
        value = value[3:]
    if value.endswith("```"):
        value = value[:-3]
    try:
        parsed = json.loads(value.strip())
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, (list, dict)) and bool(parsed)


def missing_translation_excerpts(work_dir: Path) -> list[str]:
    """Return source excerpts whose tracked paragraph output is blank."""

    missing = []
    for path in work_dir.glob("**/translate_tracking.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        def visit(value):
            if isinstance(value, dict):
                if "llm_translate_trackers" in value and "input" in value and "output" in value:
                    source = value.get("input")
                    output = value.get("output")
                    if isinstance(source, str) and source.strip() and not (
                        isinstance(output, str) and output.strip()
                    ):
                        missing.append(" ".join(source.split())[:100])
                    return
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(data)
    return missing


def ollama_chat_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/chat"


def post_json(url: str, payload: dict, timeout: int = 600) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
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
    payload = {"model": model, "messages": [
        {"role": "user", "content": "Reply with only the word OK."}
    ], "stream": False}
    if is_deepseek(url):
        payload.update(max_tokens=16, temperature=0, thinking={"type": "disabled"})
    try:
        with httpx.Client(trust_env=False, verify=ssl_context(), timeout=30) as client:
            response = client.post(
                url + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + key},
            )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("服务未返回可用的文本，请检查模型是否支持聊天接口。")
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("error", "")
            if isinstance(detail, dict):
                detail = detail.get("message", "")
            detail = detail.strip() if isinstance(detail, str) else ""
        except (ValueError, AttributeError):
            detail = ""
        code = exc.response.status_code
        if code == 400 and "does not support chat" in detail.lower():
            raise ValueError(
                f"模型“{model}”不能对话。Ollama 中的模型文件可能未下载完整，"
                f"请重新拉取模型或改用支持聊天的模型。服务原文：{detail}"
            ) from None
        messages = {401: "密钥无效或已过期", 403: "密钥没有访问权限", 404: "接口地址或模型不存在",
                    429: "请求过于频繁或账户额度不足"}
        summary = messages.get(code, "请检查服务状态")
        suffix = f" 服务原文：{detail}" if detail else ""
        raise ValueError(f"服务连接失败（{code}）：{summary}。{suffix}") from None
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("服务响应格式不兼容，请使用支持 /chat/completions 的接口。") from None
    except httpx.RequestError as exc:
        raise ValueError(
            "无法建立 HTTPS 连接。请检查系统代理是否可用，或暂时关闭代理后重试。"
            f"网络原文：{exc}"
        ) from None


def translate(job):
    from paperbridge.assets import ensure_tokenizer
    ensure_tokenizer()
    from babeldoc import main as backend

    settings = job["settings"]
    url, model, key = validate_service(settings, job.get("key", ""))
    local_ollama = is_local_ollama(url)
    deepseek = is_deepseek(url)
    translator_instances = []
    if not local_ollama:
        force_direct_httpx_for_babeldoc()
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
                if text is None:
                    return None
                return self.native_chat(
                    [{"role": "user", "content": text}], rate_limit_params
                )

        backend.OpenAITranslator = OllamaNativeTranslator
    elif deepseek:
        from babeldoc.format.pdf.document_il.midend import il_translator_llm_only
        from babeldoc.translator.translator import OpenAITranslator

        il_translator_llm_only.PROMPT_TEMPLATE = DEEPSEEK_PROMPT_TEMPLATE

        class ReliableDeepSeekTranslator(OpenAITranslator):
            """Disable costly reasoning and reject cached or fresh blank responses."""

            def __init__(self, *args, **kwargs):
                kwargs["thinking"] = "disabled"
                super().__init__(*args, **kwargs)
                self.empty_retry_count = 0
                translator_instances.append(self)

            def do_llm_translate(self, text, rate_limit_params=None):
                if text is None:
                    return None
                params = rate_limit_params or {}
                structured = bool(params.get("request_json_mode", False))
                for attempt in range(2):
                    request_text = text
                    if attempt:
                        self.empty_retry_count += 1
                        request_text += (
                            "\n\nRetry: return a non-empty valid JSON array for every input id."
                            if structured
                            else "\n\nRetry: return the complete non-empty translation only."
                        )
                    try:
                        output = super().do_llm_translate(request_text, params)
                    except (AttributeError, TypeError):
                        output = ""
                    if usable_translation(output, structured):
                        return output
                kind = "有效 JSON" if structured else "译文"
                raise RuntimeError(f"DeepSeek 连续两次没有返回{kind}。")

            def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
                params = rate_limit_params or {}
                structured = bool(params.get("request_json_mode", False))
                output = super().llm_translate(text, ignore_cache, params)
                if text is None or usable_translation(output, structured):
                    return output
                # BabelDOC may already contain a historical empty/invalid cache entry.
                output = self.do_llm_translate(text, params)
                if not (self.ignore_cache or ignore_cache):
                    self.cache.set(text, output)
                return output

        backend.OpenAITranslator = ReliableDeepSeekTranslator
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
            "--custom-system-prompt", prompt, "--save-auto-extracted-glossary"]
    if settings.get("layout_compatibility"):
        argv += ["--disable-rich-text-translate"]
    if job.get("ignore_cache"):
        argv += ["--ignore-cache"]
    if local_ollama:
        argv += ["--enable-json-mode-if-requested", "--no-send-temperature"]
    elif deepseek:
        argv += ["--enable-json-mode-if-requested", "--openai-thinking", "disabled"]
        emit("progress", stage="DeepSeek 省 token 模式：关闭思考并校验空译文", percent=None)
    else:
        argv += ["--no-send-temperature"]
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
                missing = missing_translation_excerpts(Path(job["output"]) / "work")
                if missing:
                    example = missing[0]
                    raise RuntimeError(
                        f"检测到 {len(missing)} 个段落没有得到译文，已阻止加载含空白的 PDF。"
                        f"首个漏译段落：{example}"
                    )
                prompt_tokens = sum(item.prompt_token_count.value for item in translator_instances)
                completion_tokens = sum(item.completion_token_count.value for item in translator_instances)
                cached_tokens = sum(item.cache_hit_prompt_token_count.value for item in translator_instances)
                retries = sum(item.empty_retry_count for item in translator_instances)
                finished = True
                emit("result", mono=str(mono), dual=str(dual) if dual else "",
                     glossary=str(result.auto_extracted_glossary_path or ""),
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                     cached_tokens=cached_tokens, empty_retries=retries)
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
        return 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
