"""组包：必要依据在范围之外时，父材料标为不完整（不能当作完整内容交付）。"""
from burrowl.core.config import Budget
from burrowl.services.memory.reader import Reader, fixed_ref
from burrowl.services.query.assemble import Packet
from burrowl.services.query.budget import Ledger
from tests.service.helpers import goal_draft, narrative_draft, register_source, request, services


def test_missing_prerequisite_marks_parent_incomplete(tmp_path):
    storage, memory, _review = services(tmp_path)
    storage.owners.create("research", "base", "基础")
    storage.owners.create("research", "work", "工作")
    base = memory.commit(request("RES-BASE", None, {"op": "put_record", "client_key": "g",
                                                    "draft": goal_draft("RES-BASE")}))["record_results"][0]
    evidence = register_source(storage, "SRC-1", "research/work/r.txt", "x\n")
    draft = narrative_draft("RES-WORK", evidence)
    draft["sources"].append({**base["fixed_ref"], "relation": "prerequisite"})
    work = memory.commit(request("RES-WORK", None, {"op": "put_record", "client_key": "n",
                                                    "draft": draft}))["record_results"][0]
    reader = Reader(storage)
    packet = Packet(reader, Ledger(Budget()), allowed_owner_ids={"RES-WORK"})
    packet.add(fixed_ref(reader.record(work["fixed_ref"])))
    result = packet.result()
    assert result["incomplete_parts"] == [work["record_id"]]
    assert {g["code"] for g in result["gaps"]} >= {"ACCESS_DENIED", "CONTEXT_MISSING"}
    open_packet = Packet(Reader(storage), Ledger(Budget()), allowed_owner_ids=None)
    open_packet.add(work["fixed_ref"])
    assert open_packet.result()["incomplete_parts"] == []
