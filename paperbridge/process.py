from __future__ import annotations

import json

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from paperbridge.core import ROOT, redact, worker_command


class EngineProcess(QObject):
    event = Signal(dict)
    log = Signal(str)
    ended = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._output)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.started.connect(self._started)
        self.buffer = b""
        self.job = {}
        self.cancelled = False
        self._ended = False

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, job: dict):
        program, args = worker_command()
        self.job = job
        self.cancelled = False
        self._ended = False
        self.buffer = b""
        self.process.start(program, args)

    def _started(self):
        self.process.write((json.dumps(self.job, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.closeWriteChannel()

    def _output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n", 1)
            line = redact(raw.decode("utf-8", "replace"), self.job.get("key", ""))
            try:
                event = json.loads(line)
                if isinstance(event, dict) and "type" in event:
                    self.event.emit(event)
            except ValueError:
                self.log.emit(line)

    def _stderr(self):
        text = bytes(self.process.readAllStandardError()).decode("utf-8", "replace")
        self.log.emit(redact(text, self.job.get("key", "")))

    def _finished(self, code, _status):
        self._output()
        self._stderr()
        if not self._ended:
            self._ended = True
            self.ended.emit(code, self.cancelled)
        self.job = {}

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.event.emit({"type": "error", "message": "无法启动翻译引擎，请运行「安装依赖.cmd」。"})
            self._finished(-1, None)

    def cancel(self):
        self.cancelled = True
        self.process.kill()
