"""The AI tokens an agent step uses, as its provider reports them.

Per model call (one response of the model): input tokens (everything sent: instructions, documents, the conversation
and the tool results so far), output tokens (its reasoning and its answer, tool calls included), and the prompt
cache's reads and writes. Per file: the output tokens of the responses whose tool calls wrote or edited it, a response
writing several files splitting its output tokens between them by the size of what it wrote to each. That is what
writing the document cost; what was read to write it (the input tokens) belongs to the whole step, not to one document.

Stored on the step attempt (StageExecution.usage) and shown on the step.
"""

import json
import threading
from dataclasses import dataclass, field

WRITE_TOOLS = ("write_file", "edit_file")


def _path(value: object) -> str | None:
    """A file path as the step's documents are stored: relative to the workspace, forward slashes."""
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/") or None


def writes_of(calls: list[tuple[str, object]]) -> list[tuple[str, int]]:
    """(path, size written) of the write_file and edit_file calls among a response's tool calls (name, arguments)."""
    out = []
    for name, args in calls:
        if isinstance(args, str):  # some providers pass the arguments as JSON text
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if name not in WRITE_TOOLS or not isinstance(args, dict):
            continue
        path = _path(args.get("path"))
        if path:
            out.append((path, len(str(args.get("content") if name == "write_file" else args.get("new_text") or ""))))
    return out


@dataclass
class TokenMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    models: list[str] = field(default_factory=list)
    files: dict[str, dict] = field(default_factory=dict)  # path -> {"output_tokens", "writes"}
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def model_call(self, *, input_tokens: int | None = 0, output_tokens: int | None = 0,
                   cache_read_tokens: int | None = 0, cache_write_tokens: int | None = 0,
                   model: str | None = None) -> None:
        """One model call's usage, as the provider reported it."""
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens or 0
            self.output_tokens += output_tokens or 0
            self.cache_read_tokens += cache_read_tokens or 0
            self.cache_write_tokens += cache_write_tokens or 0
            if model and model not in self.models:
                self.models.append(model)

    def wrote(self, output_tokens: int | None, writes: list[tuple[str, int]]) -> None:
        """A response's output tokens, split between the files its tool calls wrote (by the size of each write)."""
        if not writes or not output_tokens:
            return
        total = sum(size for _, size in writes) or len(writes)
        with self._lock:
            for path, size in writes:
                entry = self.files.setdefault(path, {"output_tokens": 0, "writes": 0})
                entry["output_tokens"] += round(output_tokens * ((size or 1) / total if total else 1 / len(writes)))
                entry["writes"] += 1

    def summary(self, documents: dict[str, str] | None = None, docs_folder: str | None = None) -> dict | None:
        """What is stored on the step attempt; None when no model call was made (the stub, a skipped step).
        `documents` (path: content) adds each document's size in tokens (about four characters a token), and lists
        the documents written without a model call's attribution too. Files outside `docs_folder` are code."""
        if not self.calls:
            return None
        files = {path: dict(entry) for path, entry in self.files.items()}
        for path, text in (documents or {}).items():
            files.setdefault(path, {"output_tokens": 0, "writes": 0})["size_tokens"] = max(1, len(text) // 4)
        folder = (docs_folder or "").strip("/") + "/" if docs_folder else None
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens, "cache_write_tokens": self.cache_write_tokens,
                "models": list(self.models),
                "documents": {p: e for p, e in files.items() if folder is None or p.startswith(folder)},
                "code": {p: e for p, e in files.items() if folder is not None and not p.startswith(folder)}}
