"""Local library records and category folders."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from paperbridge.core import ROOT, atomic_json


class PaperLibrary:
    def __init__(self, root=None):
        self.root = Path(root) if root else ROOT / "文献库"
        self.file = self.root / "library.json"
        try:
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {"categories": [], "papers": {}}

    def save(self):
        atomic_json(self.file, self.data)

    def add_category(self, name):
        name = name.strip()
        if (not name or name in (".", "..") or len(name) > 80
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(".")
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)):
            raise ValueError("分类名称不可包含路径符号或 Windows 保留名称。")
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        if not any(x.casefold() == name.casefold() for x in self.data["categories"]):
            self.data["categories"].append(name)
        self.save()
        return folder

    def register(self, source):
        source = str(Path(source).resolve())
        for item in self.data["papers"].values():
            if source in (item["source"], item.get("managed")):
                return item
        item = {"source": source, "title": Path(source).stem, "journal": "",
                "partition": "", "category": "", "managed": ""}
        self.data["papers"][source] = item
        self.save()
        return item

    def update(self, item, journal, partition, category):
        item = self.data["papers"][item["source"]]
        managed = item.get("managed", "")
        if category:
            folder = self.add_category(category)
            source = Path(item["source"])
            if not source.is_file():
                source = Path(managed) if managed else source
            if not source.is_file():
                raise ValueError("文献文件已不存在，无法收录到分类文件夹。")
            target = folder / source.name
            if target.exists() and target.resolve() != source.resolve():
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    target = folder / f"{source.stem}_{digest[:12]}{source.suffix}"
            if target.resolve() != source.resolve() and not target.exists():
                shutil.copy2(source, target)
            managed = str(target.resolve())
        item.update(journal=journal.strip(), partition=partition.strip(), category=category, managed=managed)
        self.save()
