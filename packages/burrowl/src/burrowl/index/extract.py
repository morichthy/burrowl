"""把已登记来源文件转成"可定位的分段文本"（页 / 幻灯片 / 表格行段），供 source 投影检索。

只读原件字节。图片转存到可重建的缓存目录，正文里只放 OCR 文字、替代文字与位置。
OCR 不是图表理解：没有可读文字的图片明确标注"需人工查看"。
PDF/图片/OCR 需要 burrowl[documents]；缺依赖时该文件报告无法解析，其他文件照常。
"""
import csv
import hashlib
import io
import posixpath
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree as ET

from ..core.errors import BurrowlError

BINARY_SUFFIXES = {".pptx", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".xlsx", ".csv"}
MAX_MEMBER = 30_000_000
DOCX_CHUNK = 60  # 一个章节内超过这么多段落（表格算一段）时再切分，同一标题路径
_NS_A = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_NS_S = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_NS_W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_REL_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_OCR = None


def _ocr(raw: bytes, enabled: bool) -> tuple[str, str]:
    global _OCR
    if not enabled:
        return "", "OCR disabled"
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 - 可选依赖
        if _OCR is None:
            _OCR = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
        result, _ = _OCR(raw)
        return "\n".join(row[1] for row in (result or [])), "OCR text; verify against image"
    except Exception as exc:  # noqa: BLE001 - OCR 失败只标注，不中断整份抽取
        return "", f"OCR failed: {type(exc).__name__}"


def _member(package: zipfile.ZipFile, name: str) -> bytes:
    if package.getinfo(name).file_size > MAX_MEMBER:
        raise BurrowlError("INVALID_ARGUMENT", "压缩包成员过大", {"member": name})
    return package.read(name)


class _Extraction:
    def __init__(self, cache_dir: Path, ocr_enabled: bool, max_assets: int | None, preserve_assets: bool = False):
        self.cache_dir, self.ocr_enabled, self.max_assets = cache_dir, ocr_enabled, max_assets
        self.units: list[tuple[str, ...]] = []  # (标签, 文本[, 标题路径])
        self.assets: list[dict] = []
        self.warnings: list[str] = []
        self.preserve_assets = preserve_assets

    def asset(self, data: bytes, label: str, unit: str) -> str:
        if self.max_assets is not None and len(self.assets) >= self.max_assets:
            self.warnings.append(f"图片数量达到上限：{label}")
            return "[visual omitted by asset limit]"
        try:
            from PIL import Image  # noqa: PLC0415 - 可选依赖
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > 40_000_000:
                    raise ValueError("图片超过 4000 万像素")
                img = img.convert("RGB")
                img.thumbnail((2000, 2000))
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
                converted = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"{label}: 图片无法解码（{type(exc).__name__}）")
            return f"[visual unreadable: {label}]"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / (hashlib.sha256(converted).hexdigest()[:20] + ".png")
        if not target.exists():
            target.write_bytes(converted)
        text, state = _ocr(converted, self.ocr_enabled)
        asset = {"path": str(target), "unit": unit, "label": label, "ocr_status": state,
                 "has_text": bool(text.strip())}
        if self.preserve_assets:
            # OCR 与屏幕预览使用缩略图；永久附件另取原始字节，避免分辨率和透明度丢失。
            original = self.cache_dir / (hashlib.sha256(data).hexdigest() + ".original")
            # 缓存可被清理或损坏；不可把同名缓存当成保全原图的证据。
            if not original.exists() or original.read_bytes() != data:
                original.write_bytes(data)
            asset["original_path"] = str(original)
        self.assets.append(asset)
        if not text.strip():
            self.warnings.append(f"{unit}/{label}: 图中没有可检索文字，需按缓存路径人工查看")
        return f"[Image: {label}; {state}; visual semantics not interpreted]\n{text}"

    def pptx(self, raw: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            names = set(package.namelist())
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+.xml", n)),
                            key=lambda n: int(re.search(r"slide(\d+)", n)[1]))
            if {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"} <= names:  # 按实际播放顺序
                mapping = {r.attrib["Id"]: posixpath.normpath(posixpath.join("ppt", r.attrib["Target"]))
                           for r in ET.fromstring(_member(package, "ppt/_rels/presentation.xml.rels"))
                           if r.attrib.get("TargetMode") != "External"}
                order = [mapping.get(n.attrib.get(_REL_ID)) for n in
                         ET.fromstring(_member(package, "ppt/presentation.xml")).iter() if n.tag.endswith("}sldId")]
                slides = [n for n in order if n in names]
            for number, name in enumerate(slides, 1):
                unit = f"Slide {number}"
                tree = ET.fromstring(_member(package, name))
                parts = ["\n".join(t.text or "" for t in tree.findall(".//a:t", _NS_A)),
                         *[n.attrib["descr"] for n in tree.iter() if n.attrib.get("descr")]]
                rels = posixpath.join(posixpath.dirname(name), "_rels", posixpath.basename(name) + ".rels")
                for rel in ET.fromstring(_member(package, rels)) if rels in names else []:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), rel.attrib.get("Target", "")))
                    if rel.attrib.get("TargetMode") == "External" or target not in names:
                        continue
                    kind = rel.attrib.get("Type", "").rsplit("/", 1)[-1]
                    if kind == "image":
                        parts.append(self.asset(_member(package, target), target, unit))
                    elif kind in {"notesSlide", "chart"}:
                        values = [n.text for n in ET.fromstring(_member(package, target)).iter()
                                  if n.tag.rsplit("}", 1)[-1] in {"t", "v"} and n.text]
                        parts.append(f"[{kind}]\n" + "\n".join(values))
                self.units.append((unit, "\n".join(parts)))

    def pdf(self, raw: bytes) -> None:
        try:
            from pypdf import PdfReader  # noqa: PLC0415 - 可选依赖
        except ImportError as exc:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 pypdf（burrowl[documents]）") from exc
        for number, page in enumerate(PdfReader(io.BytesIO(raw)).pages, 1):
            unit = f"Page {number}"
            parts = [page.extract_text() or ""]
            try:
                parts.extend(self.asset(img.data, img.name, unit) for img in page.images)
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"{unit}: 图片抽取失败（{type(exc).__name__}）")
            if not any(p.strip() for p in parts):
                self.warnings.append(f"{unit}: 未提取到文字或图片，可能是矢量图或特殊字体，需查看原页")
            self.units.append((unit, "\n".join(parts)))
        self.warnings.append("PDF 仅抽取文字与内嵌图片，未渲染完整页面；矢量图、公式和版面关系需对照原页核对")

    def docx(self, raw: bytes) -> None:
        """按标题切成章节（标签 Section n，路径为标题路径）；段落保留列表符号，表格转为 Markdown 表格，
        内嵌图片进缓存，公式（OMML）按线性文字抽取。修订痕迹中被删除的文字（w:delText）不抽取。"""
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            names = set(package.namelist())
            if "word/document.xml" not in names:
                raise BurrowlError("INVALID_ARGUMENT", "不是有效的 docx（缺少 word/document.xml）")
            levels = _docx_heading_levels(package, names)
            rels = {}
            if "word/_rels/document.xml.rels" in names:
                rels = {r.attrib["Id"]: posixpath.normpath(posixpath.join("word", r.attrib["Target"]))
                        for r in ET.fromstring(_member(package, "word/_rels/document.xml.rels"))
                        if r.attrib.get("TargetMode") != "External"}
            body = ET.fromstring(_member(package, "word/document.xml")).find("w:body", _NS_W)
            unhandled = sorted(n for n in names if re.fullmatch(r"word/(?:footnotes|endnotes|comments|header\d+|footer\d+)\.xml", n))
            if unhandled:
                self.warnings.append("DOCX 未抽取脚注、尾注、批注或页眉页脚中的内容，需对照原件：" + "、".join(unhandled))
            stack: list[tuple[int, str]] = []
            lines: list[str] = []
            has_math = False

            def flush() -> None:
                if any(line.strip() for line in lines):
                    path = " › ".join(t for _d, t in stack)
                    self.units.append((f"Section {len(self.units) + 1}", "\n\n".join(lines), path))
                lines.clear()

            for child in body if body is not None else []:
                has_math = has_math or child.find(f".//{_M}oMath") is not None
                if child.tag == _W + "tbl":
                    # 表格内的图片同样属于原文；按单元格顺序提取并保留所在表格的位置。
                    unit = f"Section {len(self.units) + 1}"
                    def table_image(paragraph: ET.Element, *, unit_label: str = unit) -> str:
                        parts = []
                        for blip in (n for n in paragraph.iter() if n.tag.endswith("}blip")):
                            target = rels.get(blip.attrib.get(_REL_EMBED, ""))
                            if target in names:
                                parts.append(self.asset(_member(package, target), target, unit_label))
                        return "\n".join(parts)
                    lines.append(_docx_table(child, image_text=table_image))
                elif child.tag == _W + "p":
                    text = _docx_text(child)
                    unit = f"Section {len(self.units) + 1}"
                    for blip in (n for n in child.iter() if n.tag.endswith("}blip")):
                        target = rels.get(blip.attrib.get(_REL_EMBED, ""))
                        if target in names:
                            text += "\n" + self.asset(_member(package, target), target, unit)
                    level = _docx_level(child, levels)
                    if level and text.strip():
                        flush()
                        stack = [(d, t) for d, t in stack if d < level] + [(level, text.strip())]
                        lines.append("#" * min(level, 6) + " " + text.strip())
                        continue
                    if child.find("w:pPr/w:numPr", _NS_W) is not None and text.strip():
                        text = "- " + text
                    lines.append(text)
                else:
                    if _docx_text(child).strip():
                        self.warnings.append("DOCX 含未抽取的非标准正文容器，需对照原件：" + child.tag.rsplit("}", 1)[-1])
                    continue
                if len(lines) >= DOCX_CHUNK:
                    flush()
            flush()
            if has_math:
                self.warnings.append("文中有公式（OMML），已按线性文字抽取，转写时需对照原文核对")

    def xlsx(self, raw: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            names = set(package.namelist())
            shared = ["".join(n.itertext()) for n in ET.fromstring(_member(package, "xl/sharedStrings.xml"))
                      .findall("s:si", _NS_S)] if "xl/sharedStrings.xml" in names else []
            sheet_names = {}
            if {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"} <= names:
                mapping = {r.attrib["Id"]: posixpath.normpath(posixpath.join("xl", r.attrib["Target"])).lstrip("/")
                           for r in ET.fromstring(_member(package, "xl/_rels/workbook.xml.rels"))
                           if r.attrib.get("TargetMode") != "External"}
                for sheet in ET.fromstring(_member(package, "xl/workbook.xml")).findall(".//s:sheet", _NS_S):
                    if target := mapping.get(sheet.attrib.get(_REL_ID)):
                        sheet_names[target] = sheet.attrib.get("name", target)
            for name in sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+.xml", n)):
                rows = []
                for row in ET.fromstring(_member(package, name)).findall(".//s:row", _NS_S):
                    cells = []
                    for cell in row.findall("s:c", _NS_S):
                        value = cell.findtext("s:v", default="", namespaces=_NS_S)
                        if cell.attrib.get("t") == "s":
                            value = shared[int(value)]
                        elif cell.attrib.get("t") == "inlineStr":
                            value = "".join(cell.itertext())
                        formula = cell.findtext("s:f", default="", namespaces=_NS_S)
                        cells.append(f"{cell.attrib.get('r')}: {value}" + (f" [formula={formula}]" if formula else ""))
                    rows.append((row.attrib.get("r", "?"), " | ".join(cells)))  # 用原始行号（工作表可稀疏）
                for i in range(0, len(rows), 40):
                    batch = rows[i:i + 40]
                    self.units.append((f"{sheet_names.get(name, Path(name).stem)} rows {batch[0][0]}-{batch[-1][0]}",
                                       "\n".join(text for _, text in batch)))
        self.warnings.append("XLSX 未执行公式；值是文件中的缓存值，日期可能是序列数")

    def csv(self, raw: bytes) -> None:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        header = rows[0] if rows else []
        for i in range(1, len(rows), 40):
            self.units.append((f"Rows {i + 1}-{min(i + 40, len(rows))}",
                               "\n".join(" | ".join(r) for r in [header, *rows[i:i + 40]])))
        if len(rows) == 1:
            self.units.append(("Header", " | ".join(header)))

    def text(self, raw: bytes, *, markdown: bool = False) -> None:
        """文本按 80 行分段；Markdown 先按标题切成章节（R12 T-P1.6），章节过长再按 80 行分，
        每段带"标题路径"（进入向量的上下文信封）。标签仍是行号区间，作为稳定的定位符。"""
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise BurrowlError("INVALID_ARGUMENT", "不是文本文件")
        if markdown and re.search(r"!\[[^\]]*\]|<img\b", text, flags=re.IGNORECASE):
            self.warnings.append("Markdown 图片仅保留链接文字，未读取或保存图片；需在授权范围内单独登记图片并附加")
        lines = text.splitlines()
        sections = _markdown_sections(lines) if markdown else [(0, len(lines), "")]
        for begin, end, path in sections or [(0, 0, "")]:
            for i in range(begin, max(begin + 1, end), 80):
                chunk = lines[i:min(end, i + 80)]
                self.units.append((f"Lines {i + 1}-{i + len(chunk)}", "\n".join(chunk), path))


def _docx_heading_levels(package: zipfile.ZipFile, names: set[str]) -> dict[str, int]:
    """样式 ID → 标题级别：样式名 heading N / Title，或样式的大纲级别。"""
    levels: dict[str, int] = {}
    if "word/styles.xml" not in names:
        return levels
    for style in ET.fromstring(_member(package, "word/styles.xml")).findall("w:style", _NS_W):
        style_id = style.attrib.get(_W + "styleId", "")
        name_node = style.find("w:name", _NS_W)
        name = (name_node.attrib.get(_W + "val", "") if name_node is not None else "").strip().lower()
        outline = style.find("w:pPr/w:outlineLvl", _NS_W)
        if match := re.fullmatch(r"heading (\d)", name):
            levels[style_id] = int(match[1])
        elif name == "title":
            levels[style_id] = 1
        elif outline is not None and outline.attrib.get(_W + "val", "").isdigit() and int(outline.attrib[_W + "val"]) < 9:
            levels[style_id] = int(outline.attrib[_W + "val"]) + 1
    return levels


def _docx_level(paragraph: ET.Element, levels: dict[str, int]) -> int:
    """段落的标题级别（0 = 正文）：段落自带的大纲级别优先，其次样式表，最后按常见样式 ID 猜测。"""
    outline = paragraph.find("w:pPr/w:outlineLvl", _NS_W)
    if outline is not None and outline.attrib.get(_W + "val", "").isdigit() and int(outline.attrib[_W + "val"]) < 9:
        return int(outline.attrib[_W + "val"]) + 1
    style = paragraph.find("w:pPr/w:pStyle", _NS_W)
    style_id = style.attrib.get(_W + "val", "") if style is not None else ""
    if style_id in levels:
        return levels[style_id]
    match = re.fullmatch(r"(?i)(?:heading|标题)\s*(\d)", style_id)
    return int(match[1]) if match else 0


def _docx_text(node: ET.Element) -> str:
    parts = []
    for item in node.iter():
        if item.tag in {_W + "t", _M + "t"}:
            parts.append(item.text or "")
        elif item.tag == _W + "tab":
            parts.append("\t")
        elif item.tag in {_W + "br", _W + "cr"}:
            parts.append("\n")
    return "".join(parts)


def _docx_table(table: ET.Element, *, image_text: Callable[[ET.Element], str] | None = None) -> str:
    rows = []
    for row in table.findall("w:tr", _NS_W):
        cells = [" ".join((_docx_text(p) + ("\n" + image_text(p) if image_text else "")).strip()
                          .replace("\n", "<br>") for p in cell.findall(".//w:p", _NS_W)).replace("|", "\\|")
                 for cell in row.findall("w:tc", _NS_W)]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    width = rows[0].count(" | ") + 1
    return "\n".join([rows[0], "|" + "---|" * width, *rows[1:]])


_MD_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def _markdown_sections(lines: list[str]) -> list[tuple[int, int, str]]:
    """按 ATX 标题切分：返回 [(起始行, 结束行(不含), "一级 › 二级 › …")]；代码围栏内的 # 不算标题。"""
    starts: list[tuple[int, str]] = [(0, "")]
    stack: list[tuple[int, str]] = []
    fence = None
    for number, line in enumerate(lines):
        if match := _MD_FENCE.match(line):
            fence = None if fence == match.group(1) else (fence or match.group(1))
            continue
        if fence or not (match := _MD_HEADING.match(line)):
            continue
        depth = len(match.group(1))
        stack = [(d, t) for d, t in stack if d < depth] + [(depth, match.group(2))]
        starts.append((number, " › ".join(t for _d, t in stack)))
    bounds = [(start, (starts[i + 1][0] if i + 1 < len(starts) else len(lines)), path)
              for i, (start, path) in enumerate(starts)]
    return [b for b in bounds if b[1] > b[0] and any(line.strip() for line in lines[b[0]:b[1]])]


def extract(name: str, raw: bytes, *, cache_dir: Path, ocr_enabled: bool = True,
            max_assets: int | None = 40, preserve_assets: bool = False) -> dict:
    """返回 {"units": [{label, text, path}], "assets": [...], "warnings": [...]}；不支持的格式抛 INVALID_ARGUMENT。
    path 是 Markdown / docx 的标题路径（其他格式为空），用于上下文信封和原文快照的分段（R21）。"""
    suffix = Path(name).suffix.lower()
    job = _Extraction(cache_dir / hashlib.sha256(raw).hexdigest()[:24], ocr_enabled, max_assets, preserve_assets)
    if suffix == ".pptx":
        job.pptx(raw)
    elif suffix == ".docx":
        job.docx(raw)
    elif suffix == ".pdf":
        job.pdf(raw)
    elif suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        job.units.append(("Image 1", Path(name).stem + "\n" + job.asset(raw, name, "Image 1")))
    elif suffix == ".csv":
        job.csv(raw)
    elif suffix == ".xlsx":
        job.xlsx(raw)
    else:
        try:  # 其他后缀（如 .snapshot、无后缀）只要是 UTF-8 文本就按文本处理
            job.text(raw, markdown=suffix in {".md", ".markdown"})
        except (UnicodeError, BurrowlError) as exc:
            raise BurrowlError("INVALID_ARGUMENT", "不支持的来源格式（不是文本也不是已知文档格式）",
                           {"suffix": suffix}) from exc
    if not job.units:
        job.warnings.append("没有可抽取的内容，需要转换格式或补充说明")
    return {"units": [{"label": unit[0], "text": unit[1].strip(), "path": unit[2] if len(unit) > 2 else ""}
                      for unit in job.units],
            "assets": job.assets, "warnings": job.warnings}
