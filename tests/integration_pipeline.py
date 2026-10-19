"""Real BabelDOC layout pipeline against a local fixture API, not a translation-quality evaluation.

Only the synthetic fixture in examples is sent to the local server. No user PDF or cloud API is used.
"""
import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pymupdf

from paperbridge.core import DEFAULTS, ROOT

TITLE = "Reliable Signal Estimation in Noisy Environments"
BODY = "The proposed method reduces measurement noise while preserving the original signal. Experimental results suggest an improvement in accuracy under the evaluated conditions. These findings do not establish a causal relationship."
TRANSLATION = "所提出的方法在保留原始信号的同时降低了测量噪声。实验结果表明，在所评估的条件下，估计精度有所提高。这些结果并不能确立因果关系。"


def make_fixture(path):
    doc = pymupdf.open()
    for index in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((48, 40), "PAPERBRIDGE / SYNTHETIC TEST DOCUMENT", fontsize=8, color=(.2, .4, .3))
        page.insert_textbox((48, 68, 550, 136), TITLE, fontname="Times-Bold", fontsize=22)
        page.insert_text((48, 148), "A. Researcher  /  Reading Lab", fontsize=10)
        page.insert_text((48, 188), "Abstract", fontname="Times-Bold", fontsize=14)
        page.insert_textbox((48, 204, 548, 270), BODY, fontname="Times-Roman", fontsize=11)
        page.insert_text((48, 301), "1. Method", fontname="Times-Bold", fontsize=14)
        page.insert_textbox((48, 318, 281, 475), BODY + " " + BODY, fontname="Times-Roman", fontsize=11)
        page.insert_text((320, 301), "2. Results", fontname="Times-Bold", fontsize=14)
        page.insert_textbox((320, 318, 550, 475), BODY + " " + BODY, fontname="Times-Roman", fontsize=11)
        # Distinct mathematical line and native vector chart, separated from translated text.
        page.insert_text((150, 526), "E = mc", fontname="Times-Italic", fontsize=19)
        page.insert_text((207, 516), "2", fontname="Times-Roman", fontsize=12)
        page.insert_text((500, 526), "(1)", fontsize=11)
        page.draw_line((85, 698), (510, 698), color=(.2, .3, .3), width=1)
        page.draw_line((85, 698), (85, 568), color=(.2, .3, .3), width=1)
        for i in range(6):
            page.draw_rect((120 + 60*i, 675 - 13*i, 148 + 60*i, 697), color=None, fill=(.15, .5, .4))
        page.insert_text((170, 730), "Figure 1. Signal estimation results", fontsize=10)
        page.insert_text((285, 803), str(index + 1), fontsize=10)
    doc.set_toc([[1, "Abstract", 1], [1, "Method and results", 2]])
    doc.save(path)
    doc.close()


def fixture_translate(text):
    # Fixed human-written fixture translations. Retain placeholders and all unknown content.
    mapping = {TITLE: "噪声环境中的可靠信号估计", BODY: TRANSLATION, "Abstract": "摘要",
               "1. Method": "1. 方法", "2. Results": "2. 结果",
               "Figure 1. Signal estimation results": "图 1. 信号估计结果"}
    for source, target in mapping.items():
        text = text.replace(source, target)
    return text


class FixtureAPI(BaseHTTPRequestHandler):
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = data["messages"][-1]["content"]
        self.server.requests_seen.append(prompt)
        if "## Here is the input:" in prompt:
            items = json.loads(prompt.split("## Here is the input:")[-1].strip())
            answer = json.dumps([{"id": item["id"], "output": fixture_translate(item["input"])} for item in items], ensure_ascii=False)
        elif "Now translate the following text:" in prompt:
            answer = fixture_translate(prompt.split("Now translate the following text:")[-1].strip())
        else:
            answer = "OK"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.path == "/api/chat":
            payload = {"model": "fixture", "message": {"role": "assistant", "content": answer},
                       "done": True, "prompt_eval_count": 1, "eval_count": 1}
        else:
            payload = {"id": "fixture", "object": "chat.completion", "created": 1,
                       "model": "fixture", "choices": [{"index": 0, "message": {
                           "role": "assistant", "content": answer}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


def main():
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    source = examples / "academic-sample.pdf"
    make_fixture(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureAPI)
    server.requests_seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    output = ROOT / ".build-tmp" / "integration-result"
    output.mkdir(parents=True, exist_ok=True)
    settings = dict(DEFAULTS, base_url=f"http://127.0.0.1:{server.server_port}/v1", model="paperbridge-fixture-v1",
                    auto_glossary=False)
    job = {"source": str(source), "output": str(output), "settings": settings, "key": "", "pages": "1",
           "ignore_cache": True}
    try:
        result = subprocess.run([sys.executable, "-u", "-m", "paperbridge.worker"],
                                input=json.dumps(job) + "\n", capture_output=True, encoding="utf-8", timeout=240, cwd=ROOT)
        (output / "worker-events.jsonl").write_text(result.stdout, encoding="utf-8")
        (output / "worker-log.txt").write_text(result.stderr, encoding="utf-8")
        print(result.stdout, flush=True)
        if result.returncode:
            print(result.stderr[-5000:])
            raise RuntimeError(f"Pipeline failed with code {result.returncode}")
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        final = next(event for event in events if event["type"] == "result")
        with pymupdf.open(source) as a, pymupdf.open(final["mono"]) as b:
            assert len(a) == len(b) == 2
            assert all(a[i].rect == b[i].rect for i in range(len(a)))
            assert "摘要" in b[0].get_text(), b[0].get_text()
            assert a[1].get_pixmap().samples == b[1].get_pixmap().samples, "Unselected page changed"
            # Exact raster comparison of protected formula and native vector graphic.
            for name, clip in [("formula", (120, 488, 530, 542)), ("chart", (70, 557, 530, 710))]:
                original = a[0].get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
                translated = b[0].get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
                assert original.samples == translated.samples, name + " changed"
            with pymupdf.open(final["dual"]) as dual:
                assert len(dual) == 2
                assert abs(dual[0].rect.width - a[0].rect.width * 2) < 1
            b[0].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(str(output / "translated-page.png"))
        assert original_hash == hashlib.sha256(source.read_bytes()).hexdigest()
        assert any("Do not add explanations" in prompt for prompt in server.requests_seen)
        print("PASS: real BabelDOC pipeline, Chinese text, unchanged formula/chart pixels, unchanged unselected page, dimensions, dual output, original integrity, academic prompt")
        (examples / "fixture-result.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
