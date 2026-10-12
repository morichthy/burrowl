"""生成兼容性标本：用当前版本写出一份覆盖各类真源状态的数据目录，冻结在 tests/fixtures/compat/v<版本>/。

每次发布新版本时运行一次并提交；之后不再修改已有标本。tests/service/test_compat_fixtures.py 用新代码
读取历史上的每一份标本（逐条校验指纹、加载 Run 与来源、从零重建索引、在旧数据上继续写入），
读不了就说明真源格式的兼容性被破坏。

    uv run python tools/make_compat_fixture.py [--force]

标本内容（只通过公开操作写入，不手工拼文件）：
    data/   数据目录：以 tests/fixtures/workspace 为底（含早期格式的提交），再写入
            每种可写记录类型各一条、复核、关联决定、检索反馈、修订；knowledge/project 对象；
            采用的文档对象（OBJ-）；对象下与独立的 Run（登记、执行、被引用）、回收站中的 Run；
            数据目录内与工作根下的来源登记；阅读会话；工作台笔记；配置修改
    work/   数据目录外的工作根（已登记原件）。生成时的绝对路径记在 data/.burrowl/sources.json，
            测试时用 sources.relocate_root 指到副本位置（这一步本身也检验了来源迁移）
    FIXTURE.json  生成版本、提交、时间与内容清单

不打包派生数据（索引、关系图投影、中枢映射）：它们按版本重建，正是测试要检验的。
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from importlib.resources import files
from pathlib import Path

import burrowl
from burrowl.app import App
from burrowl.core import records
from burrowl.core.clock import utc_now
from burrowl.core.hashing import canonical_hash, sha256_bytes, sha256_file
from burrowl.interfaces.ops.registry import Ctx, call
from burrowl.portable import bundle
from burrowl.storage import memory_store

REPO = Path(__file__).resolve().parents[1]
BASE = REPO / "tests" / "fixtures" / "workspace"
TARGET = REPO / "tests" / "fixtures" / "compat"
OWNER = "RES-FLOATING-POINT-SUMMATION"
AI = {"kind": "ai", "id": "compat-fixture"}
HUMAN = {"kind": "human", "id": "compat-reviewer"}
DERIVED = (".burrowl/index", ".burrowl/hub", "exports")


class Writer:
    def __init__(self, app: App):
        self.app = app

    def call(self, name: str, actor: dict = AI, **arguments):
        return call(Ctx(app=self.app, actor=actor, channel="cli"), name, arguments)

    def head(self, owner_id: str) -> str | None:
        head = self.call("memory.inspect", owner_id=owner_id)["head"]
        return head["commit_id"] if head else None  # 新对象还没有提交

    def commit(self, owner_id: str, *operations: dict) -> dict:
        return self.call("memory.commit", owner_id=owner_id, operations=list(operations),
                         expected_head=self.head(owner_id), request_id=str(uuid.uuid4()))

    def put(self, draft: dict) -> dict:
        return self.commit(draft["owner_id"], {"op": "put_record", "client_key": "x", "draft": draft})


def owner_ref(app: App, owner_id: str) -> dict:
    owner = app.storage.owners.scan().get(owner_id)
    return {"target_kind": "owner", "target_id": owner.owner_id, "revision": None, "sha256": owner.fingerprint,
            "locator": "", "relation": "references"}


def file_ref(app: App, source_id: str) -> dict:
    return {"target_kind": "file", "target_id": source_id, "revision": None,
            "sha256": sha256_file(app.storage.sources.resolve(source_id).path), "locator": "", "relation": "input"}


def narrative(owner_id: str, sources: list[dict], claim_id: str, statement: str) -> dict:
    return {"owner_id": owner_id, "kind": "narrative", "title": f"标本经过 {claim_id}", "body_markdown": "标本正文",
            "record_reason": "兼容性标本", "sensitivity": "internal", "sources": sources,
            "payload": {"question": "标本问题", "limitations": ["合成数据"],
                        "stages": [{"situation": "标本", "action": "执行", "reason": "覆盖格式",
                                    "outcome": "完成", "evidence_refs": sources[:1]}],
                        "claims": [{"claim_id": claim_id, "statement": statement, "kind": "fact",
                                    "scope": "标本", "evidence_refs": sources[:1]}],
                        "experience_refs": [], "process_refs": [], "technical_refs": []}}


def _docx_with_image() -> bytes:
    """最小的 docx：两个标题章节（第二个带一张 PNG）和一个致谢章节。"""
    import io  # noqa: PLC0415 - 只在生成标本时使用
    import zipfile  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415 - 可选依赖 burrowl[documents]

    image = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 120, 200)).save(image, format="PNG")
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'

    def para(text: str, style: str = "", picture: bool = False) -> str:
        blip = f'<w:r><w:drawing><a:blip {a} r:embed="rId1"/></w:drawing></w:r>' if picture else ""
        props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{props}<w:r><w:t>{text}</w:t></w:r>{blip}</w:p>"

    body = "".join([para("标本算法", "Heading1"), para("定义与步骤。"), para("结果", "Heading2"),
                    para("结果说明。", picture=True), para("致谢", "Heading1"), para("感谢。")])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/document.xml", f"<w:document {w} {r}><w:body>{body}</w:body></w:document>")
        package.writestr("word/_rels/document.xml.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                         'relationships/image" Target="media/image1.png"/></Relationships>')
        package.writestr("word/media/image1.png", image.getvalue())
    return buffer.getvalue()


def build(data: Path, work: Path) -> dict:
    shutil.copytree(BASE, data, ignore=shutil.ignore_patterns("__pycache__"))
    work.mkdir()
    app = App.open(data, fake_vectors=True)
    w = Writer(app)
    known = problems(w)  # 底稿自带的问题（样例故意保留的失效来源）；写入后不能新增
    made: dict[str, list] = {"kinds": [], "owners": [], "runs": [], "sources": []}

    # 来源：工作根下的原件（数据目录外）与数据目录内的文件
    (work / "inputs").mkdir()
    (work / "inputs" / "samples.csv").write_bytes(b"x,y\n1,2\n3,4\n")
    (work / "README.md").write_bytes("# 标本工作目录\n".encode())
    root = w.call("sources.add_root", path=str(work), title="标本工作根")
    for source_id, path in (("SRC-COMPAT-SAMPLES", str(work / "inputs" / "samples.csv")),
                            ("SRC-COMPAT-README", str(work / "README.md"))):
        w.call("sources.register", source_id=source_id, path=path, title=Path(path).name)
        made["sources"].append(source_id)
    inside = data / "research" / "floating-point-summation" / "compat-note.txt"
    inside.write_bytes(b"data-dir source\n")
    script = "research/floating-point-summation/compat_analyze.py"  # 执行只支持数据目录内的 .py 脚本
    (data / script).write_bytes(b"import sys, pathlib\n"
                                b"rows = pathlib.Path(sys.argv[1]).read_text().splitlines()[1:]\n"
                                b"print('rows', len(rows))\n")
    w.call("sources.register", source_id="SRC-COMPAT-INSIDE",
           path="research/floating-point-summation/compat-note.txt", title="数据目录内来源")
    made["sources"].append("SRC-COMPAT-INSIDE")

    # 每种可写记录类型各一条（契约示例，与 memory.contract 返回给 AI 的一致）
    examples = files("burrowl.core.schema") / "examples"
    for path in sorted(examples.iterdir(), key=lambda p: p.name):
        if path.name.endswith(".json"):
            operation = json.loads(path.read_text(encoding="utf-8"))["operation"]
            w.commit(operation["draft"].get("owner_id", OWNER), operation)  # R20 起示例草案不写 owner_id
            made["kinds"].append(path.name.removesuffix(".json"))

    # 其他对象类型；采用的文档对象
    for owner_type, slug, title in (("knowledge", "compat-notes", "标本知识"), ("project", "compat-project", "标本项目")):
        made["owners"].append(w.call("memory.owner_create", owner_type=owner_type, slug=slug, title=title,
                                     description="兼容性标本")["owner_id"])
    doc = data / "knowledge" / "compat-adopted.md"
    doc.parent.mkdir(exist_ok=True)
    doc.write_bytes("# 采用的文档\n\n标本正文。\n".encode())
    made["owners"].append(w.call("memory.owner_adopt", path="knowledge/compat-adopted.md",
                                 expected_sha256=sha256_file(doc))["owner_id"])
    knowledge = made["owners"][0]

    # Run：对象下（登记 + 执行 + 被引用）、独立（被知识对象引用）、回收站
    owned = w.call("runs.create", title="标本对象 Run", owner_id=OWNER, question="样本有几行")["run_id"]
    w.call("runs.register", run_id=owned, inputs=["SRC-COMPAT-SAMPLES"])
    w.call("runs.execute", run_id=owned, script=script, inputs=["SRC-COMPAT-SAMPLES"], args=["{input0}"])
    w.call("runs.update", run_id=owned, fields={"conclusion": "执行成功，输出行数", "limitations": ["合成数据"]})
    standalone = w.call("runs.create", title="标本独立 Run", question="临时测试")["run_id"]
    w.call("runs.register", run_id=standalone, artifacts=["research/floating-point-summation/compat-note.txt"])
    trashed = w.call("runs.create", title="标本丢弃 Run")["run_id"]
    w.call("runs.discard", run_id=trashed, reason="标本：沉淀检查无可复用内容")
    made["runs"] = [owned, standalone, trashed]
    w.put(narrative(OWNER, [file_ref(app, "SRC-COMPAT-SAMPLES"), owner_ref(app, owned)],
                    "CLM-COMPAT-OWNED", "标本样本共两行"))
    w.put(narrative(knowledge, [file_ref(app, "SRC-COMPAT-INSIDE"), owner_ref(app, standalone)],
                    "CLM-COMPAT-STANDALONE", "独立 Run 产生的标本知识"))

    # 导入转写（R21，v0.8.0 起）：原文快照、图片附件、L1 的 origin 与块 locators、L0 的 omitted
    import_doc = work / "inputs" / "algorithm.docx"
    import_doc.write_bytes(_docx_with_image())
    snapshot = w.call("sources.register", source_id="SRC-COMPAT-DOCX", path=str(import_doc), title="标本算法说明",
                      snapshot=True)["snapshot"]
    made["sources"].append("SRC-COMPAT-DOCX")
    keys = [s["key"] for s in snapshot["segments"]]
    attached = w.call("sources.attach", source_id="SRC-COMPAT-DOCX", asset_label=snapshot["assets"][0]["label"])
    made["sources"].append(attached["source_id"])
    docx_ref = file_ref(app, "SRC-COMPAT-DOCX")
    w.put({"owner_id": knowledge, "kind": "source", "title": "标本算法说明（登记）", "body_markdown": "标本原件。",
           "record_reason": "兼容性标本", "sources": [docx_ref],
           "payload": {"source_ref": docx_ref, "acquisition": "original_link", "completeness": "complete",
                       "acquired_at": None, "omitted": [{"locators": [keys[-1]], "reason": "标本：致谢"}]}})
    w.put({"owner_id": knowledge, "kind": "detail", "title": "标本算法的转写", "body_markdown": "",
           "record_reason": "兼容性标本", "sources": [docx_ref],
           "payload": {"unit_type": "experiment", "run_ref": None, "evidence_refs": [], "missing_refs": [],
                       "origin": {"source_ref": docx_ref, "locators": keys[:-1]},
                       "figures": [{"caption": "标本图", "ref": attached["ref"]}],
                       "retrieval_description": {"question": "标本", "method": "转写", "key_findings": ["标本"],
                                                 "applicable": ["标本"], "not_applicable": ["未知"],
                                                 "limitations": ["未知"]},
                       "blocks": [{"block_id": "body", "role": "results", "requires_block_ids": [],
                                   "locators": keys[:1], "markdown": "标本正文。\n\n![标本图](figure:0)"},
                                  {"block_id": "general", "role": "discussion", "requires_block_ids": [],
                                   "markdown": "标本观点原话。"}]}})
    made["transcription"] = {"owner_id": knowledge, "source_id": "SRC-COMPAT-DOCX"}

    # 修订、复核、关联决定、检索反馈
    records = w.call("memory.records", owner_id=OWNER, kinds=["narrative"])["items"]
    target = next(r for r in records if r["title"] == "标本经过 CLM-COMPAT-OWNED")
    draft = narrative(OWNER, [file_ref(app, "SRC-COMPAT-SAMPLES"), owner_ref(app, owned)],
                      "CLM-COMPAT-OWNED", "标本样本共两行（修订）")
    w.commit(OWNER, {"op": "put_record", "record_id": target["record_id"], "expected_revision": target["revision"],
                     "draft": draft})
    w.call("review.submit", actor=HUMAN, target_claim_id="CLM-COMPAT-OWNED", state="accepted",
           reason="标本：人工确认", scope="标本", evidence_refs=[file_ref(app, "SRC-COMPAT-SAMPLES")])
    w.call("relations.decide", owner_id=knowledge, reason="标本关联",
           payload={"from": owner_ref(app, knowledge), "to": owner_ref(app, OWNER), "explanation": "标本关联"})
    page = w.call("query.search", question="求和顺序")
    hit = page["items"][0]
    w.call("memory.feedback", owner_id=hit["owner_id"], query_id=page["query_id"],
           target=hit["ref"], label="adopted", note="标本反馈")

    # 阅读会话、工作台笔记、配置
    session = w.call("reading.start", question="求和顺序怎样影响误差", strategy="quick")
    w.call("reading.finish", session_id=session["session_id"], outcome="标本：结束")
    w.call("relations.note_add", actor=HUMAN, kind="relation-idea", title="标本笔记", text="标本")
    w.call("config.update", actor=HUMAN, patch={"reading": {"strategy": "quick"}},
           expected_revision=w.call("config.get")["revision"])

    new = sorted(set(problems(w)) - set(known))
    if new:
        raise SystemExit(f"生成的标本自检出现新问题：{new}")
    return made | {"work_root_id": root["root_id"], "original_work_path": str(work), "known_problems": known}


def problems(w: Writer) -> list[str]:
    """system.validate 的问题（不含索引待建：派生数据不入标本），记为 "区域:代码:对象或来源"。"""
    return sorted({f"{p['area']}:{p['code']}:{p.get('owner_id') or p.get('source_id') or ''}"
                   for p in w.call("system.validate")["problems"] if p["area"] != "index"})


def format_fingerprint() -> str:
    """真源格式指纹：记录契约全文、内容与服务端字段表、提交清单字段、规范哈希算法（用固定样本探测）、备份包格式。

    与最新标本记录的值不同，说明这次改动可能影响已有数据的读取，需要生成新标本（测试会提示）。
    覆盖不到的格式（业务卡、Run 卡、来源登记的字段）改动时须人工判断，见 docs/TESTING。"""
    schema = files("burrowl.core.schema") / "memory.schema.json"
    return canonical_hash({
        "memory_schema": sha256_bytes(schema.read_bytes()),
        "content_fields": list(records.CONTENT_FIELDS), "optional": sorted(records.OPTIONAL_CONTENT_FIELDS),
        "server_fields": sorted(records.SERVER_FIELDS), "manifest_fields": sorted(memory_store.MANIFEST_FIELDS),
        "hash_probe": canonical_hash({"b": [1, 2.5, None, True], "a": "中文\n"}),
        "bundle": bundle.FORMAT})


def git_commit() -> tuple[str, bool]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    return run("rev-parse", "HEAD"), bool(run("status", "--porcelain", "--", "packages"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="覆盖同版本已有标本（只在尚未发布时使用）")
    options = parser.parse_args()
    target = TARGET / f"v{burrowl.__version__}"
    if target.exists() and not options.force:
        print(f"{target} 已存在；已发布的标本不能修改（尚未发布时可加 --force）", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="burrowl-compat-") as tmp:
        data, work = Path(tmp) / "data", Path(tmp) / "work"
        made = build(data, work)
        for rel in DERIVED:
            shutil.rmtree(data / rel, ignore_errors=True)
        commit, dirty = git_commit()
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copytree(data, target / "data")
        shutil.copytree(work, target / "work")
    manifest = {"burrowl_version": burrowl.__version__, "git_commit": commit, "git_dirty": dirty,
                "created_at": utc_now(), "format_fingerprint": format_fingerprint(), **made}
    (target / "FIXTURE.json").write_bytes(json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b"\n")
    print(f"已生成 {target.relative_to(REPO)}：{len(made['kinds'])} 种记录、{len(made['runs'])} 个 Run、"
          f"{len(made['owners'])} 个新对象")
    return 0


if __name__ == "__main__":
    sys.exit(main())
