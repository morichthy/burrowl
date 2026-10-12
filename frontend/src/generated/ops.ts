// 自动生成，勿手改（npm run ops）。

export declare namespace HubConnectors {
  /**
   * 已配置的连接器、发布策略与已发布条目数。
   */
  export interface Args {}
}

export declare namespace HubHealth {
  /**
   * 测试连接：服务版本、兼容性、能力、密钥是否已设置（不返回密钥）。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
  }
}

export declare namespace HubPlan {
  /**
   * 预览发布：按策略（默认只发已复核的 L4/L3 与成稿文档）列出新增/更新/删除，返回 plan_hash。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
  }
}

export declare namespace HubApply {
  /**
   * 执行发布。逐条记录结果，单条失败不影响其他条目。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
    /**
     * hub_plan 返回的 plan_hash；数据变化后需重新预览
     */
    plan_hash: string;
    /**
     * 覆盖外部已被修改的 Wiki 页面
     */
    force_wiki?: boolean;
  }
}

export declare namespace HubStatus {
  /**
   * 发布状态：各状态计数、失败/漂移条目、最近几次执行；refresh 时轮询外部解析进度。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
    /**
     * true 时先轮询外部解析进度再返回
     */
    refresh?: boolean;
  }
}

export declare namespace HubSearch {
  /**
   * 在外部知识中枢检索；命中能对上本地条目时附 fixed_ref 与按本地当前状态计算的复核类别。
   */
  export interface Args {
    /**
     * 检索文本
     */
    query: string;
    /**
     * 只查这些连接器；不给时查全部
     */
    connector_ids?: string[] | null;
    limit?: number;
    /**
     * all（默认，带复核徽标）/ reviewed_only / exclude_unsafe / unreviewed_only
     */
    review_filter?: "all" | "reviewed_only" | "exclude_unsafe" | "unreviewed_only";
  }
}

export declare namespace WikiList {
  /**
   * 列出外部 Wiki 页面（authored_here 表示由本项目发布）。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
    query?: string;
    limit?: number;
  }
}

export declare namespace WikiRead {
  /**
   * 读取一页外部 Wiki（含外部自动生成的页面）。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
    slug: string;
  }
}

export declare namespace WikiConfigure {
  /**
   * 按配置 wiki.mode 开启/关闭外部自动 Wiki（auto/both 开启）。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
  }
}

export declare namespace HubCandidatesPull {
  /**
   * 把外部工具发现的关系线索拉回为关联候选（origin=tool:<连接器>，只写派生的候选表，不写记录）。
   * 之后用 relations_candidates 查看并判断。
   */
  export interface Args {
    /**
     * 连接器 ID（hub_connectors 列出）
     */
    connector_id: string;
    limit?: number;
  }
}

export declare namespace ExchangeExport {
  /**
   * 把记录导出为交换包（Markdown 库 + 图 JSONL + manifest）到数据目录的 exports/<名称>/。
   * 派生数据，可删除重建；增量写入，内容没变的文件不改写。可以用 Obsidian 直接打开其中的 vault 目录。
   */
  export interface Args {
    /**
     * 导出名称：字母、数字、点、下划线、连字符
     */
    name: string;
    owner_ids?: string[] | null;
    owner_types?: string[] | null;
    /**
     * 不导出的敏感级别；默认 [confidential]，restricted 永远不导出
     */
    exclude_sensitivity?: string[] | null;
  }
}

export declare namespace MemoryRender {
  /**
   * 按交换格式把一条记录渲染为 Markdown（YAML 头 + 正文 + 自动生成的反链与关联）。只读。
   */
  export interface Args {
    owner_id: string;
    record_id: string;
  }
}

export declare namespace ReviewQueue {
  /**
   * 待复核队列：默认列出未复核、已失效、有争议、被上游阻断、有升级提议的结论；
   * 每项带有效成熟度与派生统计（支持数按原始来源去重，"用得多"不等于"对"）。
   */
  export interface Args {
    /**
     * 只看这些状态；不给时列出未复核、已失效、有争议、被上游阻断、有升级提议的
     */
    states?:
      | (
          | "not-reviewed"
          | "stale"
          | "disputed"
          | "upstream-blocked"
          | "maturity-requested"
          | "accepted"
          | "retracted"
          | "superseded"
        )[]
      | null;
    /**
     * 只看该对象的结论
     */
    owner_id?: string | null;
    /**
     * 按结论陈述或 ID 匹配
     */
    query?: string;
    limit?: number;
    offset?: number;
  }
}

export declare namespace ReviewSubmit {
  /**
   * 复核一条结论。accepted 需要适用范围与支持性证据；superseded 需要替代结论。dry_run 只预检。
   * 成熟度与复核状态分开：可以在 not-reviewed 时登记 conjecture/preliminary；established 必须 accepted。
   */
  export interface Args {
    /**
     * 要复核的结论 ID（CLM-…）
     */
    target_claim_id: string;
    /**
     * accepted 在 scope 内成立；disputed 有争议；retracted 撤回；superseded 被替代；not-reviewed 未复核
     */
    state: "accepted" | "disputed" | "retracted" | "superseded" | "not-reviewed";
    /**
     * 复核理由；AI 代用户登记时写明“用户在对话中确认：…（时间）”
     */
    reason: string;
    /**
     * 在什么范围内成立（accepted 必填）
     */
    scope?: string | null;
    /**
     * 支持或反驳的固定引用列表（accepted 必填）。固定引用 {target_kind, target_id, revision, sha256, locator, relation}，不能只写 ID。记录：revision 与 sha256 取 memory_inspect 的 revision 与 record_hash，或提交回执的 fixed_ref；文件：revision 写 null，sha256 取 sources_list 的指纹
     */
    evidence_refs?:
      | {
          target_kind: "owner" | "record" | "claim" | "file";
          target_id: string;
          revision: number | null;
          sha256: string | null;
          locator: string;
          relation:
            | "supports"
            | "input"
            | "contradicts"
            | "background"
            | "derived_from"
            | "refines"
            | "same_problem"
            | "same_failure_mode"
            | "prerequisite"
            | "analogous_to"
            | "used_in"
            | "references"
            | "same_entity"
            | "mentions"
            | "depends_on"
            | "supersedes"
            | "contains"
            | "precedes"
            | "member_of"
            | "similar_structure"
            | "related_to"
            | "subject_of"
            | "object_of";
        }[]
      | null;
    /**
     * superseded 必填：替代它的结论 ID
     */
    replacement_claim_id?: string | null;
    /**
     * 成熟度：conjecture 有推理无数据；preliminary 至少一次 Run 或小样本支持；replicated 多份独立依据都成立；established 只能由人或授权流程登记（AI 改用 requested_maturity）
     */
    maturity?: ("conjecture" | "preliminary" | "replicated" | "established") | null;
    /**
     * established 的证据类型
     */
    evidence_basis?: ("engineering_case" | "proof" | "authoritative_source" | "replication") | null;
    /**
     * AI 提议升级为已确立，由用户在复核队列中确认
     */
    requested_maturity?: "established" | null;
    /**
     * true 只预检、不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace ReviewBatch {
  /**
   * 批量复核（逐条串行；单条失败不影响其他）。每项字段同 review_submit。
   */
  export interface Args {
    /**
     * 每项字段同 review_submit：{target_claim_id, state, reason, scope?, evidence_refs?, replacement_claim_id?, maturity?, evidence_basis?, requested_maturity?}
     *
     * @minItems 1
     */
    items: [
      {
        target_claim_id: string;
        state: "accepted" | "disputed" | "retracted" | "superseded" | "not-reviewed";
        reason: string;
        scope?: string | null;
        evidence_refs?: {
          target_kind: "owner" | "record" | "claim" | "file";
          target_id: string;
          revision: number | null;
          sha256: string | null;
          locator: string;
          relation:
            | "supports"
            | "input"
            | "contradicts"
            | "background"
            | "derived_from"
            | "refines"
            | "same_problem"
            | "same_failure_mode"
            | "prerequisite"
            | "analogous_to"
            | "used_in"
            | "references"
            | "same_entity"
            | "mentions"
            | "depends_on"
            | "supersedes"
            | "contains"
            | "precedes"
            | "member_of"
            | "similar_structure"
            | "related_to"
            | "subject_of"
            | "object_of";
        }[];
        replacement_claim_id?: string | null;
        maturity?: "conjecture" | "preliminary" | "replicated" | "established" | null;
        evidence_basis?: "engineering_case" | "proof" | "authoritative_source" | "replication" | null;
        requested_maturity?: "established" | null;
      },
      ...{
        target_claim_id: string;
        state: "accepted" | "disputed" | "retracted" | "superseded" | "not-reviewed";
        reason: string;
        scope?: string | null;
        evidence_refs?: {
          target_kind: "owner" | "record" | "claim" | "file";
          target_id: string;
          revision: number | null;
          sha256: string | null;
          locator: string;
          relation:
            | "supports"
            | "input"
            | "contradicts"
            | "background"
            | "derived_from"
            | "refines"
            | "same_problem"
            | "same_failure_mode"
            | "prerequisite"
            | "analogous_to"
            | "used_in"
            | "references"
            | "same_entity"
            | "mentions"
            | "depends_on"
            | "supersedes"
            | "contains"
            | "precedes"
            | "member_of"
            | "similar_structure"
            | "related_to"
            | "subject_of"
            | "object_of";
        }[];
        replacement_claim_id?: string | null;
        maturity?: "conjecture" | "preliminary" | "replicated" | "established" | null;
        evidence_basis?: "engineering_case" | "proof" | "authoritative_source" | "replication" | null;
        requested_maturity?: "established" | null;
      }[]
    ];
    /**
     * true 只预检、不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace ReviewHistory {
  /**
   * 一条结论的复核历史。
   */
  export interface Args {
    /**
     * 结论 ID（CLM-…）
     */
    claim_id: string;
  }
}

export declare namespace ProvenanceImpact {
  /**
   * 这些内容变化后，哪些下游需要重新审阅（导航边只提示，supports/input 才是正式依赖）。
   */
  export interface Args {
    /**
     * 变化了的来源（SRC-…）、记录（MEM-…）或结论（CLM-…）
     */
    changed_ids: string[];
  }
}

export declare namespace ProvenanceLineage {
  /**
   * 沿依据追到可访问的原件身份（来源数不等于独立支持数）。
   */
  export interface Args {
    /**
     * 要追溯的记录（MEM-…）或结论（CLM-…）
     */
    target_ids: string[];
  }
}

export declare namespace ProvenanceQuestionValidity {
  /**
   * 已解决问题的答案依据现在是否仍成立。
   */
  export interface Args {
    question_id: string;
    scope?: string | null;
  }
}

export declare namespace ProvenanceFormal {
  /**
   * 正式使用：只返回在该范围内已接受、有效、来源可见的结论原文，其余列出原因。
   */
  export interface Args {
    /**
     * 结论（CLM-…）或包含结论的记录（MEM-…）
     */
    target_ids: string[];
    /**
     * 正式使用的范围，只返回在该范围内已接受的结论
     */
    scope: string;
  }
}

export declare namespace RelationsGraph {
  /**
   * 材料关系图（子图）：对象、记录、块、结论、概念、来源，以及按类别区分的边（来自图投影）。
   * categories 只看某几类边；domains / excluded_domains 按领域筛选或屏蔽概念。
   */
  export interface Args {
    center?: string | null;
    hops?: number;
    query?: string;
    kinds?: string[] | null;
    levels?: string[] | null;
    excluded?: string[] | null;
    limit?: number;
    categories?: ("structure" | "evidence" | "reference" | "navigation" | "concept" | "claim_relation")[] | null;
    domains?: string[] | null;
    excluded_domains?: string[] | null;
    node_types?: ("owner" | "record" | "block" | "claim" | "concept" | "source")[] | null;
  }
}

export declare namespace RelationsSimilar {
  /**
   * 为种子记录找关联候选（关键词重合 / 语义近邻）；只是待判断的线索。
   */
  export interface Args {
    /**
     * 种子记录 ID（MEM-…）
     */
    seeds: string[];
    /**
     * keyword=关键词重合；semantic=向量近邻（需要嵌入模型）
     */
    method?: "keyword" | "semantic";
    limit?: number;
  }
}

export declare namespace RelationsAssociations {
  /**
   * 已保存的关联记录。
   */
  export interface Args {
    /**
     * 只看该状态
     */
    status?: ("candidate" | "accepted" | "rejected" | "withdrawn") | null;
    /**
     * 只看一端是该记录的关联
     */
    record_id?: string | null;
  }
}

export declare namespace RelationsDecide {
  /**
   * 保存关联判断（candidate/accepted/rejected/withdrawn）；只能是导航关系。
   * "A 会影响 B"这类可以验证的判断写成结论（inference），不要写成关联。
   */
  export interface Args {
    /**
     * 关联记录存放的对象 ID
     */
    owner_id: string;
    /**
     * {from, to, relation, explanation, shared_structure, transfer_limits[], basis_refs[], status, certainty?(speculative|plausible|supported), origin?, observed_in?}；快速添加只给 {from, to, explanation}：自动设为 related_to + speculative + accepted。from/to/basis_refs 都是固定引用对象 {target_kind, target_id, revision, sha256, locator, relation}，不能只写 ID
     */
    payload: {
      from: {
        target_kind: "owner" | "record" | "claim" | "file";
        target_id: string;
        revision: number | null;
        sha256: string | null;
        locator: string;
        relation:
          | "supports"
          | "input"
          | "contradicts"
          | "background"
          | "derived_from"
          | "refines"
          | "same_problem"
          | "same_failure_mode"
          | "prerequisite"
          | "analogous_to"
          | "used_in"
          | "references"
          | "same_entity"
          | "mentions"
          | "depends_on"
          | "supersedes"
          | "contains"
          | "precedes"
          | "member_of"
          | "similar_structure"
          | "related_to"
          | "subject_of"
          | "object_of";
      };
      to: {
        target_kind: "owner" | "record" | "claim" | "file";
        target_id: string;
        revision: number | null;
        sha256: string | null;
        locator: string;
        relation:
          | "supports"
          | "input"
          | "contradicts"
          | "background"
          | "derived_from"
          | "refines"
          | "same_problem"
          | "same_failure_mode"
          | "prerequisite"
          | "analogous_to"
          | "used_in"
          | "references"
          | "same_entity"
          | "mentions"
          | "depends_on"
          | "supersedes"
          | "contains"
          | "precedes"
          | "member_of"
          | "similar_structure"
          | "related_to"
          | "subject_of"
          | "object_of";
      };
      relation?:
        | "supports"
        | "input"
        | "contradicts"
        | "background"
        | "derived_from"
        | "refines"
        | "same_problem"
        | "same_failure_mode"
        | "prerequisite"
        | "analogous_to"
        | "used_in"
        | "references"
        | "related_to";
      explanation: string;
      shared_structure?: string | null;
      /**
       * @minItems 0
       */
      transfer_limits?: string[];
      differences?: string[];
      /**
       * @minItems 0
       */
      basis_refs?: {
        target_kind: "owner" | "record" | "claim" | "file";
        target_id: string;
        revision: number | null;
        sha256: string | null;
        locator: string;
        relation:
          | "supports"
          | "input"
          | "contradicts"
          | "background"
          | "derived_from"
          | "refines"
          | "same_problem"
          | "same_failure_mode"
          | "prerequisite"
          | "analogous_to"
          | "used_in"
          | "references"
          | "same_entity"
          | "mentions"
          | "depends_on"
          | "supersedes"
          | "contains"
          | "precedes"
          | "member_of"
          | "similar_structure"
          | "related_to"
          | "subject_of"
          | "object_of";
      }[];
      status?: "candidate" | "accepted" | "rejected" | "withdrawn";
      /**
       * @minItems 0
       */
      missing_refs?: {
        requested_target: string;
        reason: string;
        observed_at: string;
      }[];
      certainty?: "speculative" | "plausible" | "supported";
      origin?: string;
      observed_in?: {
        target_kind: "owner" | "record" | "claim" | "file";
        target_id: string;
        revision: number | null;
        sha256: string | null;
        locator: string;
        relation:
          | "supports"
          | "input"
          | "contradicts"
          | "background"
          | "derived_from"
          | "refines"
          | "same_problem"
          | "same_failure_mode"
          | "prerequisite"
          | "analogous_to"
          | "used_in"
          | "references"
          | "same_entity"
          | "mentions"
          | "depends_on"
          | "supersedes"
          | "contains"
          | "precedes"
          | "member_of"
          | "similar_structure"
          | "related_to"
          | "subject_of"
          | "object_of";
      };
    };
    /**
     * 为什么做这个判断（写进记录的 record_reason）
     */
    reason: string;
    /**
     * true 只预检、不写入
     */
    dry_run?: boolean;
    /**
     * 可见范围；不给时新关联默认全库可见、修订沿用原值。只有需要保密时才写 owner_only
     */
    discovery?: ("workspace_summary" | "owner_only") | null;
  }
}

export declare namespace RelationsCandidates {
  /**
   * 待判断的线索：正文提到概念却没链接（concept_mention）、向量近邻（similar）、外部工具回流（external:*）。
   * 已判断过的端点对会被过滤。只读。
   */
  export interface Args {
    /**
     * 只看涉及该记录的线索
     */
    record_id?: string | null;
    /**
     * 只看该对象内的线索
     */
    owner_id?: string | null;
    /**
     * 线索类型：concept_mention、similar、external:*
     */
    kinds?: string[] | null;
    limit?: number;
  }
}

export declare namespace RelationsView {
  /**
   * 工作台关系图的本机视图偏好。
   */
  export interface Args {}
}

export declare namespace RelationsViewSave {
  /**
   * 保存关系图视图偏好（布局、分组、中心、排除项）。
   */
  export interface Args {
    view: {
      [k: string]: unknown;
    };
  }
}

export declare namespace RelationsNotes {
  /**
   * 工作台笔记（R15）：用户在关系图旁写下的分组名、桥接问题、关系设想。queued 表示用户交给 AI 处理，
   * 用 relations_note_reply 回写方案、提问或说明不处理。笔记不是研究记录。
   */
  export interface Args {
    /**
     * 只看这些状态；AI 处理用户交给你的笔记时用 ['queued']
     */
    status?: ("open" | "queued" | "proposed" | "needs_user" | "handled" | "done" | "dismissed")[] | null;
    /**
     * 只看引用了该对象或其记录的笔记
     */
    owner_id?: string | null;
  }
}

export declare namespace RelationsNoteAdd {
  /**
   * （仅用户）新建工作台笔记（草稿）。
   */
  export interface Args {
    kind: "cluster-name" | "bridge-question" | "relation-idea";
    title: string;
    text?: string;
    refs?: string[] | null;
  }
}

export declare namespace RelationsNoteResolve {
  /**
   * （仅用户）交给 AI（queued）、收回或重新打开（open）、完成（无需 AI）、放弃。
   */
  export interface Args {
    note_id: string;
    status: "open" | "queued" | "done" | "dismissed";
  }
}

export declare namespace RelationsNoteEdit {
  /**
   * （仅用户）修改笔记内容。已处理的先重新打开；待你确认的方案会作废，笔记退回 AI。
   */
  export interface Args {
    note_id: string;
    kind?: ("cluster-name" | "bridge-question" | "relation-idea") | null;
    title?: string | null;
    text?: string | null;
  }
}

export declare namespace RelationsNoteAnswer {
  /**
   * （仅用户）回答 AI 在笔记上的提问；笔记回到 queued。
   */
  export interface Args {
    note_id: string;
    text: string;
  }
}

export declare namespace RelationsNoteReply {
  /**
   * 回写用户交给你的工作台笔记（只能作用于 queued 的笔记）。写入方案须由用户在工作台执行，不能替用户确认或执行。
   */
  export interface Args {
    /**
     * queued 状态的笔记 ID
     */
    note_id: string;
    /**
     * proposal：写入方案（先预演，用户在工作台执行后才写入）；question：向用户提问；declined：说明不需要或不能写入
     */
    outcome: "proposal" | "question" | "declined";
    /**
     * 给用户看的一句说明、问题或理由
     */
    message: string;
    /**
     * 仅 proposal：最多 10 步，每步 {op:'memory_commit', owner_id, operations, expected_head} 或 {op:'relations_decide', owner_id, payload, reason?}；参数与对应工具相同。同一对象只提交一次；服务端逐步 dry_run 预检并生成预览，不会写入
     */
    steps?:
      | [
          (
            | {
                op: "memory_commit" | "memory.commit";
                owner_id: string;
                /**
                 * @minItems 1
                 */
                operations: [
                  {
                    op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
                    client_key?: string;
                    record_id?: string;
                    expected_revision?: number;
                    /**
                     * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
                     */
                    draft: {};
                    repin?: boolean;
                  },
                  ...{
                    op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
                    client_key?: string;
                    record_id?: string;
                    expected_revision?: number;
                    /**
                     * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
                     */
                    draft: {};
                    repin?: boolean;
                  }[]
                ];
                /**
                 * 该对象当前 HEAD；新对象为 null
                 */
                expected_head: string | null;
              }
            | {
                op: "relations_decide" | "relations.decide";
                owner_id: string;
                payload: {
                  from: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                  to: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                  relation?:
                    | "supports"
                    | "input"
                    | "contradicts"
                    | "background"
                    | "derived_from"
                    | "refines"
                    | "same_problem"
                    | "same_failure_mode"
                    | "prerequisite"
                    | "analogous_to"
                    | "used_in"
                    | "references"
                    | "related_to";
                  explanation: string;
                  shared_structure?: string | null;
                  /**
                   * @minItems 0
                   */
                  transfer_limits?: string[];
                  differences?: string[];
                  /**
                   * @minItems 0
                   */
                  basis_refs?: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  }[];
                  status?: "candidate" | "accepted" | "rejected" | "withdrawn";
                  /**
                   * @minItems 0
                   */
                  missing_refs?: {
                    requested_target: string;
                    reason: string;
                    observed_at: string;
                  }[];
                  certainty?: "speculative" | "plausible" | "supported";
                  origin?: string;
                  observed_in?: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                };
                reason?: string;
              }
          ),
          ...(
            | {
                op: "memory_commit" | "memory.commit";
                owner_id: string;
                /**
                 * @minItems 1
                 */
                operations: [
                  {
                    op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
                    client_key?: string;
                    record_id?: string;
                    expected_revision?: number;
                    /**
                     * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
                     */
                    draft: {};
                    repin?: boolean;
                  },
                  ...{
                    op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
                    client_key?: string;
                    record_id?: string;
                    expected_revision?: number;
                    /**
                     * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
                     */
                    draft: {};
                    repin?: boolean;
                  }[]
                ];
                /**
                 * 该对象当前 HEAD；新对象为 null
                 */
                expected_head: string | null;
              }
            | {
                op: "relations_decide" | "relations.decide";
                owner_id: string;
                payload: {
                  from: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                  to: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                  relation?:
                    | "supports"
                    | "input"
                    | "contradicts"
                    | "background"
                    | "derived_from"
                    | "refines"
                    | "same_problem"
                    | "same_failure_mode"
                    | "prerequisite"
                    | "analogous_to"
                    | "used_in"
                    | "references"
                    | "related_to";
                  explanation: string;
                  shared_structure?: string | null;
                  /**
                   * @minItems 0
                   */
                  transfer_limits?: string[];
                  differences?: string[];
                  /**
                   * @minItems 0
                   */
                  basis_refs?: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  }[];
                  status?: "candidate" | "accepted" | "rejected" | "withdrawn";
                  /**
                   * @minItems 0
                   */
                  missing_refs?: {
                    requested_target: string;
                    reason: string;
                    observed_at: string;
                  }[];
                  certainty?: "speculative" | "plausible" | "supported";
                  origin?: string;
                  observed_in?: {
                    target_kind: "owner" | "record" | "claim" | "file";
                    target_id: string;
                    revision: number | null;
                    sha256: string | null;
                    locator: string;
                    relation:
                      | "supports"
                      | "input"
                      | "contradicts"
                      | "background"
                      | "derived_from"
                      | "refines"
                      | "same_problem"
                      | "same_failure_mode"
                      | "prerequisite"
                      | "analogous_to"
                      | "used_in"
                      | "references"
                      | "same_entity"
                      | "mentions"
                      | "depends_on"
                      | "supersedes"
                      | "contains"
                      | "precedes"
                      | "member_of"
                      | "similar_structure"
                      | "related_to"
                      | "subject_of"
                      | "object_of";
                  };
                };
                reason?: string;
              }
          )[]
        ]
      | null;
  }
}

export declare namespace RelationsNoteApply {
  /**
   * （仅用户）执行笔记上的 AI 方案：核对依据未变后按预览原样写入；依据变化或某步失败时退回 AI。
   */
  export interface Args {
    note_id: string;
    proposal_id: string;
  }
}

export declare namespace RelationsNoteReject {
  /**
   * （仅用户）退回 AI 方案并附意见；笔记回到 queued。
   */
  export interface Args {
    note_id: string;
    proposal_id: string;
    comment?: string;
  }
}

export declare namespace RelationsNoteClear {
  /**
   * （仅用户）删除已处理、已完成和已放弃的笔记。编号不复用。
   */
  export interface Args {}
}

export declare namespace MaintenancePlan {
  /**
   * 来源变化或新反证后：列出受影响记录及其完整正文，生成维护计划。
   */
  export interface Args {
    /**
     * 变化了的来源（SRC-…）、记录（MEM-…）或结论（CLM-…）
     */
    changed_ids: string[];
    /**
     * 给用户看的一句说明（为什么要维护）
     */
    note?: string;
    /**
     * 只把这些受影响的记录放进计划；省略时放入全部
     */
    targets?: string[] | null;
  }
}

export declare namespace MaintenancePlanFromCandidates {
  /**
   * （仅用户）为所选维护候选生成计划：变化来源取自候选记下的影响路径，计划只含所选记录；候选标为 planned。
   */
  export interface Args {
    target_ids: string[];
    note?: string;
  }
}

export declare namespace MaintenanceVisibilityCandidates {
  /**
   * R19 之前按旧默认存成 owner_only 的知识记录（L1–L4、已采纳的关联、概念页）。对象策略显式要求保密的对象不列出。
   */
  export interface Args {}
}

export declare namespace MaintenanceRefRewriteCandidates {
  /**
   * 正文（L1–L4、文稿）里写了长编号（如 MEM-<uuid>）或 64 位指纹的记录，逐处给出可替换的短名链接（replacement）；
   * replacement 为空的需要 AI 或人工改写。新提交会直接拒绝这类写法（UNREADABLE_REF），这里处理已有记录。
   */
  export interface Args {}
}

export declare namespace MaintenancePlanRefRewrite {
  /**
   * （仅用户）为勾选的记录生成引用改写计划：长编号机械替换为短名链接，其余不变，预览后执行。
   */
  export interface Args {
    target_ids: string[];
    note?: string;
  }
}

export declare namespace MaintenancePlanVisibility {
  /**
   * （仅用户）为勾选的记录生成可见范围补齐计划：处置已写好（修订为全库可见，正文不变），预览后执行。
   */
  export interface Args {
    target_ids: string[];
    note?: string;
  }
}

export declare namespace MaintenancePlans {
  /**
   * 维护计划概况（最近有变化的在前）。queued 表示用户交给 AI 起草处置。
   */
  export interface Args {
    /**
     * 只看这些状态；AI 处理用户交给你的计划时用 ['queued']
     */
    status?: ("planned" | "queued" | "reviewed" | "applied" | "partial")[] | null;
  }
}

export declare namespace MaintenanceHandoff {
  /**
   * （仅用户）把计划交给 AI 起草处置，或把待确认 / 部分完成的计划退回 AI（附意见）。
   */
  export interface Args {
    plan_id: string;
    note?: string;
  }
}

export declare namespace MaintenanceReview {
  /**
   * 记录逐项处置与修订草案，并逐对象预检（不写记忆）。之后由用户在工作台预览并执行。
   */
  export interface Args {
    /**
     * 维护计划 ID
     */
    plan_id: string;
    /**
     * [{target_id, action: retain|revise|resynthesize|retract|defer, reason, draft?}]；revise / resynthesize 必须附完整的新草案 draft（owner_id 可省略，change_reason 由计划补上）
     *
     * @minItems 1
     */
    decisions: [
      {
        /**
         * 计划条目的目标 ID
         */
        target_id: string;
        action: "retain" | "revise" | "resynthesize" | "retract" | "defer";
        reason: string;
        /**
         * revise / resynthesize 时必填：完整的新草案
         */
        draft?: {};
      },
      ...{
        /**
         * 计划条目的目标 ID
         */
        target_id: string;
        action: "retain" | "revise" | "resynthesize" | "retract" | "defer";
        reason: string;
        /**
         * revise / resynthesize 时必填：完整的新草案
         */
        draft?: {};
      }[]
    ];
    /**
     * maintenance_status 返回的计划修订号
     */
    expected_revision?: number | null;
    /**
     * 给用户看的一句说明（改了什么、为什么）
     */
    message?: string;
  }
}

export declare namespace MaintenanceApply {
  /**
   * （仅用户）执行已写好处置的计划：按对象逐个提交修订与撤回；可重试，部分完成会如实报告。
   */
  export interface Args {
    plan_id: string;
    expected_revision: number;
  }
}

export declare namespace MaintenanceStatus {
  /**
   * 维护计划的当前状态。
   */
  export interface Args {
    /**
     * 维护计划 ID
     */
    plan_id: string;
  }
}

export declare namespace MonitorRun {
  /**
   * 扫描一次：对象 HEAD 与来源文件指纹的变化，并给出维护候选（观察不改变任何证据或复核状态）。
   */
  export interface Args {
    /**
     * 只比较，不写观察记录、不更新基线
     */
    dry_run?: boolean;
  }
}

export declare namespace MonitorCandidates {
  /**
   * 来源变化影响到的维护候选；用 maintenance_plan 把它们纳入语义维护。
   * 每项附 kind、title、record_id（结论落到包含它的记录）与 editable（能否放进计划修订）。
   */
  export interface Args {
    /**
     * open 待处理（默认）；planned 已放进计划；dismissed 已确认无需处理；null 列出全部
     */
    status?: ("open" | "planned" | "dismissed") | null;
  }
}

export declare namespace MonitorResolve {
  /**
   * （仅用户）处置维护候选。
   */
  export interface Args {
    target_id: string;
    status: "open" | "planned" | "dismissed";
    note?: string;
  }
}

export declare namespace MonitorObservations {
  /**
   * 最近的监测观察记录。
   */
  export interface Args {
    limit?: number;
  }
}

export declare namespace RunsCreate {
  /**
   * 新建 Run（一次实际执行的证据：输入、程序、输出与失败）。给 owner_id 时放在该对象目录下，否则是独立 Run。
   */
  export interface Args {
    /**
     * 这次执行做什么，一句话
     */
    title: string;
    /**
     * 上级对象 ID；不给时是独立 Run
     */
    owner_id?: string | null;
    /**
     * 这次执行要回答的问题
     */
    question?: string;
    /**
     * 查找 Run 用的关键词
     */
    keywords?: string[] | null;
  }
}

export declare namespace RunsRegister {
  /**
   * 登记已有文件为 Run 的输入/产物（固定字节指纹，可回滚）。
   */
  export interface Args {
    /**
     * Run ID（RUN-…）
     */
    run_id: string;
    /**
     * 输入文件：数据目录内相对路径或 SRC- ID
     */
    inputs?: string[] | null;
    /**
     * 产物文件：数据目录内相对路径或 SRC- ID
     */
    artifacts?: string[] | null;
    /**
     * true 只预览登记结果、不写入
     */
    preview?: boolean;
  }
}

export declare namespace RunsExecute {
  /**
   * 前台运行 Python 脚本并登记输入、输出与日志（失败也登记）。参数可用 {input0}、{output_dir} 占位。
   */
  export interface Args {
    /**
     * Run ID（RUN-…）
     */
    run_id: string;
    /**
     * 要运行的 Python 脚本路径
     */
    script: string;
    /**
     * 输入文件：数据目录内相对路径或 SRC- ID
     */
    inputs?: string[] | null;
    /**
     * 脚本参数，可用 {input0}、{output_dir} 占位
     */
    args?: string[] | null;
    /**
     * 超时秒数
     */
    timeout_seconds?: number;
    /**
     * true 只预览命令与登记、不执行
     */
    preview?: boolean;
  }
}

export declare namespace RunsUpdate {
  /**
   * 修改 Run 的执行摘要、限制、状态等（不改输入/产物登记）。
   */
  export interface Args {
    /**
     * Run ID（RUN-…）
     */
    run_id: string;
    /**
     * 只写要改的字段：title, question, conclusion, limitations, parameters, status, keywords, parent_run_ids。conclusion 只写执行摘要（成功或失败、产物、异常），知识写成记忆记录并引用本 Run
     */
    fields: {
      title?: string;
      question?: string;
      /**
       * 只写执行摘要（成功或失败、产物、异常）
       */
      conclusion?: string;
      limitations?: string[];
      /**
       * 本次执行的参数（键值）
       */
      parameters?: {};
      status?: "planned" | "running" | "succeeded" | "failed" | "abandoned";
      keywords?: string[];
      parent_run_ids?: string[];
    };
    /**
     * 读取时 run.json 的指纹（上次修改返回的 sha256）；不一致说明别人改过，拒绝覆盖
     */
    expected_sha256?: string | null;
  }
}

export declare namespace RunsRollback {
  /**
   * 按登记回执恢复 run.json（不删除任何文件）。
   */
  export interface Args {
    receipt: string;
    preview?: boolean;
  }
}

export declare namespace RunsSearch {
  /**
   * 按标题/问题/关键词查找 Run。Run 不进检索与阅读，这是显式查找 Run 的入口；给 owner_id 时只列该对象目录下的 Run。
   */
  export interface Args {
    /**
     * 按标题、问题、关键词匹配；空串列出全部
     */
    query?: string;
    /**
     * 只列该对象目录下的 Run
     */
    owner_id?: string | null;
    limit?: number;
    /**
     * 只列没有被任何 L1–L4 记录引用的 Run（执行知识尚未写成记录）
     */
    unfixed_only?: boolean;
  }
}

export declare namespace RunsDiscard {
  /**
   * 丢弃没有知识的 Run：当前没有任何记录引用时，把整个 Run 目录移入回收站（可用 runs_restore 恢复）。
   */
  export interface Args {
    /**
     * Run ID（RUN-…）
     */
    run_id: string;
    /**
     * 为什么没有可写成记录的内容，例如：临时测试，沉淀检查无可复用的方法、边界或失败教训
     */
    reason: string;
  }
}

export declare namespace RunsTrash {
  /**
   * 回收站中已丢弃的 Run（原位置、理由、时间、执行者）。
   */
  export interface Args {}
}

export declare namespace RunsRestore {
  /**
   * 把回收站中的 Run 移回原位置（原位置已被占用时拒绝）。
   */
  export interface Args {
    run_id: string;
  }
}

export declare namespace RunsEmptyTrash {
  /**
   * 永久删除回收站中的全部 Run。用户专属，只在工作台与 CLI 提供。
   */
  export interface Args {}
}

export declare namespace SourcesList {
  /**
   * 已登记的来源文件（file 引用的唯一授权入口），附当前解析出的位置 location。
   */
  export interface Args {}
}

export declare namespace SourcesRoots {
  /**
   * 已登记的工作根（工作目录）与本次会话的工作目录。工作根下的来源记为"根 + 相对路径"，目录搬家只改根。
   */
  export interface Args {}
}

export declare namespace SourcesAddRoot {
  /**
   * 登记一个工作根。通常不必手动调用：登记本次工作目录里的文件时会自动建根。
   */
  export interface Args {
    /**
     * 工作目录的绝对路径（不能在数据目录内）
     */
    path: string;
    /**
     * 给用户看的名称
     */
    title?: string;
  }
}

export declare namespace SourcesRelocateRoot {
  /**
   * 工作目录整体搬家：逐个核对新位置下已登记文件的指纹，全部一致才改根的位置；已有固定引用不变。
   */
  export interface Args {
    /**
     * sources_roots 返回的根 ID
     */
    root_id: string;
    /**
     * 新位置的绝对路径
     */
    new_path: string;
    /**
     * true 时允许新位置缺少部分已登记文件（缺的会列出）
     */
    allow_missing?: boolean;
  }
}

export declare namespace SourcesRegister {
  /**
   * 登记一个来源文件并记录当前字节指纹：L0 原件的唯一授权入口。原件留在原处，不复制、不修改。
   * 工作目录里的文件自动记为"工作根 + 相对路径"，迁移时随根重定向。
   * 同一 ID 重复登记同一文件是幂等的；内容变了请用新 ID；文件搬家用 sources_relocate / sources_relocate_root。
   */
  export interface Args {
    /**
     * 自定的来源 ID，SRC- 开头，如 SRC-SUMMATION-R01；内容变了用新 ID
     */
    source_id: string;
    /**
     * 工作目录等外部位置用绝对路径；数据目录内可用相对路径
     */
    path: string;
    /**
     * 给人看的名称
     */
    title?: string;
    /**
     * restricted 的来源不进索引、不能被引用
     */
    sensitivity?: "public" | "internal" | "confidential" | "restricted";
    /**
     * 导入转写时设为 true：同时保存原文快照（分段文本与指纹），供 L1 的 origin.locators、覆盖报告和原件变化后的逐段对比使用
     */
    snapshot?: boolean;
  }
}

export declare namespace SourcesRegisterMany {
  /**
   * 批量登记来源（导入已有资料时使用）。
   */
  export interface Args {
    /**
     * [{source_id, path, title?, sensitivity?}]，逐条登记；单条失败不影响其他
     *
     * @minItems 1
     */
    items: [
      {
        /**
         * SRC- 开头的来源 ID
         */
        source_id: string;
        /**
         * 工作目录等外部位置用绝对路径；数据目录内可用相对路径
         */
        path: string;
        title?: string;
        sensitivity?: "public" | "internal" | "confidential" | "restricted";
      },
      ...{
        /**
         * SRC- 开头的来源 ID
         */
        source_id: string;
        /**
         * 工作目录等外部位置用绝对路径；数据目录内可用相对路径
         */
        path: string;
        title?: string;
        sensitivity?: "public" | "internal" | "confidential" | "restricted";
      }[]
    ];
    /**
     * 导入转写时设为 true：为每个登记成功的原件保存原文快照；结果中给出分段数，分段列表用 sources_snapshot 读取
     */
    snapshot?: boolean;
  }
}

export declare namespace SourcesSnapshot {
  /**
   * 原文快照：没有就先生成（只读原件、核对登记指纹）。返回分段目录（key 与字数）、提取出的图片（assets 的 label）
   * 与抽取警告；给 keys 时附这些分段的全文。转写时 L1 的 origin.locators 与块的 locators 写这里的 key；
   * 图片用 sources_attach 复制为附件后再引用。
   */
  export interface Args {
    /**
     * 已登记的来源 ID（SRC-…）
     */
    source_id: string;
    /**
     * 要读全文的分段 key（取自返回的 segments）；不给时只返回分段目录
     */
    keys?: string[] | null;
    /**
     * 返回全文的字符上限
     */
    max_chars?: number;
  }
}

export declare namespace SourcesAttach {
  /**
   * 把原件中的图片复制为数据目录内的受控附件并登记为来源，返回固定引用 ref。
   * L1 的 payload.figures 写 {caption: 照录原文图注, ref}，块正文用 ![图注](figure:序号) 在原位置引用。
   * 附件随数据目录备份和迁移；同一张图重复附加是幂等的。
   */
  export interface Args {
    /**
     * 原件的来源 ID（SRC-…）
     */
    source_id: string;
    /**
     * 原件中图片的标签（sources_snapshot 返回的 assets[].label）；原件本身就是图片时不写
     */
    asset_label?: string | null;
    /**
     * 附件名称；不写时用原件名称 + 图片标签
     */
    title?: string;
  }
}

export declare namespace SourcesRelocate {
  /**
   * 原件搬家：新位置的字节必须与登记时一致，旧路径记入 relocated_from，已有固定引用继续有效。
   */
  export interface Args {
    /**
     * 已登记的来源 ID（SRC-…）
     */
    source_id: string;
    /**
     * 新位置的绝对路径
     */
    new_path: string;
  }
}

export declare namespace SourcesUpdate {
  /**
   * （仅用户）启用/停用来源或调整敏感级别。停用或设为 restricted 后，读取被拒的记录在检索和关系图中也同时隐去。
   */
  export interface Args {
    source_id: string;
    enabled?: boolean | null;
    sensitivity?: ("public" | "internal" | "confidential" | "restricted") | null;
  }
}

export declare namespace MemoryOwners {
  /**
   * 列出可读对象（按类型分组，带各层记录数）。
   */
  export interface Args {
    /**
     * 只列这些类型：research、project、core-algorithm、data、tool、report、knowledge、run
     */
    owner_types?: string[] | null;
    /**
     * 按 ID、标题、关键词匹配；空串列出全部
     */
    query?: string;
  }
}

export declare namespace MemoryOwnerRoute {
  /**
   * 归属判定（只读）：与 reading_start 第一轮相同的召回和筛选包，附上候选的当前目标、最新检查点与来源重叠证据。
   */
  export interface Args {
    /**
     * 一两句话写清本次任务的目标和交付物，保留型号与数值原文
     */
    task: string;
    /**
     * 最多 2 条等义改写 [{language: zh|en|other, question, lexical_terms?}]；技术问题建议补一条英文；型号、数值必须原样保留
     */
    variants?:
      | {
          /**
           * 可选的改写编号
           */
          id?: string;
          language: "zh" | "en" | "other";
          /**
           * 等义改写的问句；型号、数值必须原样保留
           */
          question: string;
          /**
           * 可选：词法检索用的关键词，不写按问句切词
           */
          lexical_terms?: string[];
        }[]
      | null;
    /**
     * 必须原样出现的词（型号、编号）
     */
    protected_terms?: string[] | null;
    /**
     * 本次任务涉及的文件：绝对路径或 SRC- ID
     */
    paths?: string[] | null;
    /**
     * 用户指定的对象 ID
     */
    owner_hint?: string | null;
    limit?: number;
  }
}

export declare namespace MemoryOwnerCreate {
  /**
   * 新建对象（写入业务卡；首次提交记忆时才建立记忆目录）。Run 用 runs_create 建立。
   */
  export interface Args {
    /**
     * research 研究；project 项目；core-algorithm 公司文档定义的关键算法；data 数据集；tool 工具；report 报告；knowledge 通用知识或概念库
     */
    owner_type: "research" | "project" | "core-algorithm" | "data" | "tool" | "report" | "knowledge";
    /**
     * 小写字母/数字/连字符
     */
    slug: string;
    /**
     * 能单独看懂的标题
     */
    title: string;
    /**
     * 一两句话的目标或范围说明
     */
    description?: string;
    /**
     * 检索关键词；技术名词中英文都给
     */
    keywords?: string[] | null;
    /**
     * core-algorithm 必填：定义该算法的公司算法/模块文档（路径或文档编号）
     */
    source_document?: string | null;
    /**
     * 可选的可读短名，供 [[@短名/handle]] 链接其他对象的记录
     */
    short_name?: string | null;
    /**
     * 对象的敏感级别；restricted 不进索引、不发布
     */
    sensitivity?: "public" | "internal" | "confidential" | "restricted";
  }
}

export declare namespace MemoryOwnerAdopt {
  /**
   * 把内容树中已有的文档登记为对象（原件不修改；须给出你看到的字节指纹）。
   */
  export interface Args {
    /**
     * 内容树中已有业务卡的相对路径
     */
    path: string;
    /**
     * 你读到的该文件的 SHA-256；不一致时拒绝
     */
    expected_sha256: string;
  }
}

export declare namespace MemoryRecords {
  /**
   * 对象内的记录目录（按层级，分页；不含正文）。kinds 按类型筛选，query 匹配标题或 ID；kind_counts 是类型筛选前的计数。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 只列这些层级：L0–L4
     */
    levels?: string[] | null;
    /**
     * 只列这些记录类型
     */
    kinds?: string[] | null;
    /**
     * 按标题或 ID 匹配
     */
    query?: string;
    /**
     * true 时不列关联、复核等辅助记录
     */
    main_only?: boolean;
    offset?: number;
    limit?: number;
  }
}

export declare namespace MemoryActivity {
  /**
   * 关键操作记录（只读，按时间倒序）：建立对象、归属确认、保存、复核、Run 结束、发布、删除与恢复，每条一行，带可追溯的引用。
   * 检索、阅读、来源登记等不改变资产的操作不在其中。
   */
  export interface Args {
    /**
     * 只看这个对象（含其 Run）
     */
    owner_id?: string | null;
    /**
     * 只看这些类型；默认全部
     */
    kinds?: ("owner" | "attribution" | "save" | "review" | "run" | "publish" | "remove")[] | null;
    limit?: number;
    /**
     * 上一页返回的 next_before
     */
    before?: string | null;
  }
}

export declare namespace MemoryKinds {
  /**
   * 全部记录类型：层级、版本、由哪个操作写入、一句话用途。
   */
  export interface Args {}
}

export declare namespace MemoryContract {
  /**
   * 某类记录的草案契约：每个字段是 required / optional / conditional（以及省略时的默认值）、不要写的服务端字段、
   * payload 结构、写作要点和一份经校验的示例草案。写 memory_commit / memory_summary_save 的草案前先调用。
   */
  export interface Args {
    /**
     * 记录类型，如 detail、narrative、experience、overview、checkpoint
     */
    kind: string;
  }
}

export declare namespace MemoryInspect {
  /**
   * 读取对象的当前快照，或某条记录的当前/指定修订（含完整正文与固定引用）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 记录 ID（MEM-…）；不给时返回整个对象的当前快照
     */
    record_id?: string | null;
    /**
     * 指定修订号；不给时取当前修订
     */
    revision?: number | null;
  }
}

export declare namespace MemoryCommit {
  /**
   * 保存一批记录到一个对象（同一对象内原子提交，要么全写入要么都不写）。dry_run=true 只预检不写入。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 操作列表。新建 {op:'put_record', client_key, draft}；修订 {op:'put_record', record_id, expected_revision（当前修订号）, draft（完整的新内容）}；client_key 与 record_id 二选一。同批新记录之间用 {client_key: 'x'} 引用
     *
     * @minItems 1
     */
    operations: [
      {
        op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
        client_key?: string;
        record_id?: string;
        expected_revision?: number;
        /**
         * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
         */
        draft: {};
        repin?: boolean;
      },
      ...{
        op: "put_record" | "transition_question" | "set_policy" | "decide_association" | "save_checkpoint";
        client_key?: string;
        record_id?: string;
        expected_revision?: number;
        /**
         * 记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
         */
        draft: {};
        repin?: boolean;
      }[]
    ];
    /**
     * 提交前看到的对象 HEAD commit_id（memory_inspect / memory_resume 返回）；新对象为 null
     */
    expected_head: string | null;
    /**
     * UUID；重试时用同一个保证幂等
     */
    request_id?: string | null;
    /**
     * true 只预检、不写入；回执与正式提交相同
     */
    dry_run?: boolean;
    /**
     * 归属确认点（本任务第一次往该对象保存）时必填：{action: continue|create, decided_by: user|ai, reason, rejected?: [{owner_id, reason}]}；rejected 写完整阅读后放弃的对象。之后的保存不再填写
     */
    attribution?: {
      action: "continue" | "create";
      decided_by: "user" | "ai";
      reason: string;
      rejected?: {
        owner_id: string;
        reason: string;
      }[];
    } | null;
  }
}

export declare namespace MemoryDocument {
  /**
   * 读取研究文稿：完整文稿 / 目录 / 单章上下文包（改写某章用）/ 依据变化影响；
   * 以及转写外部原件时的覆盖报告（哪些原文分段没被 L1 认领或明显偏短）与原文展开（R21）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * full 完整文稿；outline 目录；section 单章上下文包（改写某章用）；impact 依据变化的影响；coverage 转写原件的覆盖报告（需 source_id）；expansion 按原文顺序展开转写它的 L1 块（需 source_id）
     */
    view?: "full" | "outline" | "section" | "impact" | "coverage" | "expansion";
    /**
     * research_process 完整过程稿；research_report 精简报告
     */
    document_type?: "research_process" | "research_report";
    /**
     * 文稿根记录 ID；对象有多份同类文稿时指定
     */
    document_id?: string | null;
    /**
     * view=section 时必填：章节记录 ID
     */
    section_id?: string | null;
    /**
     * 返回正文的字符上限
     */
    max_chars?: number;
    /**
     * view=coverage / expansion 时必填：被转写的原件（SRC-…，需已有快照）
     */
    source_id?: string | null;
  }
}

export declare namespace MemoryFigure {
  /**
   * 技术单元登记的图片（data URL）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    record_id: string;
    revision: number;
    index: number;
  }
}

export declare namespace UiRefTargets {
  /**
   * 工作台显示记录时用：把来源、正文链接与其他固定引用解析为标题与跳转位置（R21 D-11）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    record_id: string;
    revision?: number | null;
  }
}

export declare namespace UiTranscriptions {
  /**
   * 工作台"原文展开"页用：本对象转写或登记过的原件，附是否有快照与覆盖计数（R21 D-07）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryTimeline {
  /**
   * 研究时间线：全部记录的全部修订与关联 Run（不含正文）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    offset?: number;
    limit?: number;
  }
}

export declare namespace MemoryRawMaterials {
  /**
   * L0 原始材料清单（不读原件正文）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryRawMaterial {
  /**
   * 回读一个原始材料（按清单中的 ID；核对固定字节）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    material_id: string;
    max_chars?: number;
  }
}

export declare namespace MemoryResume {
  /**
   * 续接对象的工作上下文：目标→检查点→最新概览/经过/技术→路线/问题/经验（只读）。
   * 不建立阅读会话；用户查询主题相关知识、找已有经验时默认先 reading_start。
   * workbench_notes 列出与该对象相关、尚未结束的工作台笔记；queued 的由你用 relations_note_reply 回写。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryExport {
  /**
   * 导出对象的记忆（不产生新记录、不复制原件）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    fmt?: "markdown" | "json" | "html";
    record_ids?: string[] | null;
    exclude_ids?: string[] | null;
    include_raw_materials?: boolean;
  }
}

export declare namespace MemoryConsolidatePrepare {
  /**
   * 阶段巩固的待处理清单：自上次巩固以来的变化、受影响下游与合并/冲突候选；
   * unfixed_evidence 是没被任何 L1–L4 引用的 Run 与 L0，unlinked_details 是没被任何 L2–L4 或文稿指向的本对象 L1
   * （逐项补就近链接，或写明不需要的理由）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 从这个提交之后算变化；不给时从上次巩固算起
     */
    since_commit?: string | null;
  }
}

export declare namespace MemoryConsolidateApply {
  /**
   * 保存巩固处置（retain/revise/retract/defer…），可与本批实际修订一起提交。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    basis: {
      [k: string]: unknown;
    };
    decisions: {
      [k: string]: unknown;
    }[];
    reason: string;
    operations?:
      | {
          [k: string]: unknown;
        }[]
      | null;
    dry_run?: boolean;
  }
}

export declare namespace MemorySummaryMaterials {
  /**
   * 跨对象总结的固定材料包（不生成总结）。
   */
  export interface Args {
    /**
     * 要总结的对象 ID
     */
    owner_ids: string[];
    /**
     * 不放进材料包的对象或记录 ID
     */
    exclude_ids?: string[] | null;
  }
}

export declare namespace MemorySummarySave {
  /**
   * 把写好的跨对象总结保存为本对象的 L3（experience）或 L4（overview）。依据对象在准备材料后有新提交则拒绝，
   * 不用旧材料覆盖新结果。修订已有总结时给 record_id、expected_revision，并在草案中写 change_reason。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * kind 为 experience（L3）或 overview（L4）的完整草案；记录草案（对象）。至少写 kind、title、body_markdown、payload、sources、record_reason；owner_id、sensitivity、discovery 可省略（由服务补上）；修订已有记录时写 change_reason。payload 的结构随 kind 不同，写之前用 memory_contract(kind) 查字段和示例
     */
    draft: {
      [k: string]: unknown;
    };
    /**
     * memory_summary_materials 返回的 basis_heads（各依据对象当时的 HEAD），原样传回；任何一个对象之后有新提交都会拒绝（STALE_BASIS），需要重新准备材料
     */
    basis_heads: {
      [k: string]: string | null;
    };
    /**
     * 修订已有总结时填它的记录 ID；新建不填
     */
    record_id?: string | null;
    /**
     * 修订时填当前修订号；新建不填
     */
    expected_revision?: number | null;
    /**
     * true 只预检、不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace MemoryFeedback {
  /**
   * 记录检索结果的使用反馈（采用次数不是复核）。label=outcome 时 adopted_in 须指向实际应用它的 Run 或记录。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    query_id: string;
    /**
     * 被反馈的固定引用对象（检索命中的 ref），不能只写 ID
     */
    target: {
      [k: string]: unknown;
    };
    label: "missing" | "irrelevant" | "invalid_analogy" | "lost_boundary" | "stale" | "adopted" | "outcome";
    note?: string;
    adopted_in?: {
      [k: string]: unknown;
    } | null;
    /**
     * UUID；重试时沿用同一个，不会重复记录
     */
    request_id?: string | null;
  }
}

export declare namespace MemoryRecovery {
  /**
   * 检查记忆目录健康状况（写锁、孤立提交、暂存残留）；只报告不修改。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryRecordsDiscard {
  /**
   * 删除本来就不该记的记录（测试数据、重复、写错对象）：从当前记录中移除，旧修订留在历史里，可用 records_restore 恢复。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 记录 ID（MEM-…）；一次可以删除或恢复多条，它们之间互相引用不算阻碍
     *
     * @minItems 1
     */
    record_ids: [string, ...string[]];
    /**
     * 为什么删除，例如：测试数据、重复记录、写错对象
     */
    reason: string;
    /**
     * 预检时看到的对象 HEAD（dry_run 返回的 head）；给出时不一致就拒绝
     */
    expected_head?: string | null;
    /**
     * true 只报告能否删除与阻碍，不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace MemoryRecordsRestore {
  /**
   * 恢复已删除的记录（与删除前逐字节相同）。它引用的记录或对象已不存在时拒绝。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 记录 ID（MEM-…）；一次可以删除或恢复多条，它们之间互相引用不算阻碍
     *
     * @minItems 1
     */
    record_ids: [string, ...string[]];
    /**
     * 预检时看到的对象 HEAD（dry_run 返回的 head）；给出时不一致就拒绝
     */
    expected_head?: string | null;
    /**
     * true 只报告能否恢复，不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace MemoryRecordsDiscarded {
  /**
   * 对象内已删除的记录（标题、层级、理由、时间、执行者），可用 records_restore 恢复。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryOwnerDiscard {
  /**
   * 删除对象到回收站（可用 owner_restore 恢复）：业务卡对象移走整个对象目录（含记录与其下的 Run），
   * 被采用的文档只移走记忆目录、原件留在原处。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
    /**
     * 为什么删除，例如：误建、测试对象、与另一对象重复
     */
    reason: string;
    /**
     * 预检时看到的对象 HEAD（dry_run 返回的 head）；给出时不一致就拒绝
     */
    expected_head?: string | null;
    /**
     * true 只报告影响与阻碍，不移动任何文件
     */
    dry_run?: boolean;
  }
}

export declare namespace MemoryOwnerTrash {
  /**
   * 回收站中的对象（原位置、方式、理由、时间、执行者）。
   */
  export interface Args {}
}

export declare namespace MemoryOwnerRestore {
  /**
   * 把回收站中的对象移回原位置并重建检索投影（原位置已被占用时拒绝）。
   */
  export interface Args {
    /**
     * 对象 ID，如 RES-…、PRJ-…、RUN-…
     */
    owner_id: string;
  }
}

export declare namespace MemoryOwnerEmptyTrash {
  /**
   * 永久删除回收站中的全部对象。用户专属，只在工作台与 CLI 提供。
   */
  export interface Args {}
}

export declare namespace QuerySearch {
  /**
   * 独立检索材料并核对固定版本，返回第一页候选；不建立阅读会话、不进入工作台阅读页。
   * 用户查询主题相关知识、找已有经验时默认先 reading_start（简查 quick、完整阅读 standard）；
   * 只有明确只要片段/候选列表或低层查询时直接使用本工具。候选不是已读正文。
   */
  export interface Args {
    /**
     * 要查的问题，一句话，保留型号与数值原文
     */
    question: string;
    /**
     * 词法检索用的关键词；不给时按问题切词
     */
    keywords?: string[] | null;
    /**
     * overview_experience=L4/L3（默认）；process=L2 研究经过；technical=L1 技术内容；all=全部含原始来源
     */
    content_source?: "overview_experience" | "process" | "technical" | "all";
    /**
     * 限定检索范围（对象、层级、类型等）；不给时全库
     */
    scope?: Scope | null;
    /**
     * all（默认，带复核徽标）/ reviewed_only / exclude_unsafe / unreviewed_only
     */
    review_filter?: "all" | "reviewed_only" | "exclude_unsafe" | "unreviewed_only";
    /**
     * exploration 探索；formal 正式使用（只返回在 applicability 范围内已接受的结论）
     */
    purpose?: "exploration" | "formal";
    /**
     * purpose=formal 时必填：结论的适用范围
     */
    applicability?: string;
    /**
     * current 只用与当前版本一致的结果；allow_stale 允许索引落后时的结果（会标注）
     */
    freshness?: "current" | "allow_stale";
    /**
     * 检索通道，默认 identity + lexical + dense
     */
    channels?: ("identity" | "lexical" | "dense")[] | null;
    /**
     * 组包时依据可以来自任何可读对象，或只来自同一范围
     */
    context_reach?: "any_readable" | "same_scope";
    /**
     * 每页候选数上限
     */
    result_limit?: number | null;
    /**
     * 本次查询的预算（字符、节点等）；不给时用配置默认
     */
    budget?: Budget | null;
    /**
     * 可选：自定查询 ID（重试时沿用）
     */
    query_id?: string | null;
  }
  /**
   * 直接候选的范围。不同维度同时满足；同一维度内任一值即可。None 表示不限。
   */
  export interface Scope {
    /**
     * 限定对象；显式列出的对象才能看到其 owner_only 记录
     */
    owner_ids?: string[] | null;
    /**
     * research / project / core-algorithm / run / knowledge …
     */
    owner_types?: string[] | null;
    kinds?: string[] | null;
    /**
     * 排除的对象/记录/结论 ID；排除始终生效
     */
    exclude_ids?: string[];
    updated_from?: string | null;
    updated_before?: string | null;
  }
  /**
   * 累计硬上限。wall_ms 计活动执行时间（不含用户阅读停留）；0 表示禁止该类操作。
   */
  export interface Budget {
    wall_ms?: number;
    read_bytes?: number;
    output_chars?: number;
    candidates?: number;
    graph_nodes?: number;
    graph_edges?: number;
    graph_hops?: number;
    model_tokens?: number;
    model_calls?: number;
    model_input_tokens?: number;
    model_output_tokens?: number;
    rerank_items?: number;
  }
}

export declare namespace QueryPage {
  /**
   * 下一页候选（沿用原范围与累计预算）。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
    cursor?: string | null;
  }
}

export declare namespace QueryCancel {
  /**
   * 取消查询（已完成部分保留）。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
  }
}

export declare namespace QueryAssemble {
  /**
   * 把所选候选组合成阅读包：正文 + 必要依据（+ 已采纳关联补充）；预算不足整块省略并报告。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
    /**
     * 候选 ID（C-…），来自本次查询已返回的结果
     */
    candidate_ids: string[];
    /**
     * true 时补充已采纳关联指向的内容
     */
    associations?: boolean;
  }
}

export declare namespace QueryExpand {
  /**
   * 沿作者保存的固定引用展开：经验/概览 → 研究经过或技术依据。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
    /**
     * 候选 ID（C-…），来自本次查询已返回的结果
     */
    candidate_ids: string[];
    /**
     * process 展开到研究经过（L2）；technical 展开到技术依据（L1）
     */
    target: "process" | "technical";
  }
}

export declare namespace QueryDeepen {
  /**
   * 沿已保存关系有界深化（节点/边/跳数计入预算）；可达不等于科学支持。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
    /**
     * 候选 ID（C-…），来自本次查询已返回的结果
     */
    candidate_ids: string[];
    hops?: number;
    relations?: string[] | null;
    include_associations?: boolean;
  }
}

export declare namespace QueryDocuments {
  /**
   * 返回候选所属对象的完整文稿（按文稿去重，保持章节顺序）。
   */
  export interface Args {
    /**
     * query_search 返回的 query_id
     */
    query_id: string;
    /**
     * 候选 ID（C-…），来自本次查询已返回的结果
     */
    candidate_ids: string[];
    /**
     * research_process 完整过程稿；research_report 精简报告
     */
    document_type?: "research_process" | "research_report";
  }
}

export declare namespace ReadingStart {
  /**
   * 查询主题相关知识、找已有经验的默认入口：创建工作台可见阅读会话并立即召回。
   * 简查选 quick（采纳片段、写笔记）；完整阅读选 standard。返回第一批对象筛选包
   * （每个对象 2–4 个命中窗口，含 digest），继续 assess → read（quick 跳过）→ note → finish。
   */
  export interface Args {
    /**
     * 要回答的问题，一句话，保留型号与数值原文
     */
    question: string;
    /**
     * 问题的前提条件（自然语言），会显示给用户
     */
    conditions?: string[] | null;
    /**
     * standard=筛选后完整阅读（默认）；associative=读完写笔记后可做联想（从笔记出处出发、只走跨对象边的一跳图扩展）；quick=只依据片段、不读全文
     */
    strategy?: ("standard" | "associative" | "quick") | null;
    /**
     * all（默认，带复核徽标）/ reviewed_only / exclude_unsafe / unreviewed_only
     */
    review_filter?: ("all" | "reviewed_only" | "exclude_unsafe" | "unreviewed_only") | null;
    /**
     * 最多 2 条等义改写 [{language: zh|en|other, question, lexical_terms?}]；技术问题建议补一条英文；型号、数值必须原样保留
     */
    variants?:
      | {
          /**
           * 可选的改写编号
           */
          id?: string;
          language: "zh" | "en" | "other";
          /**
           * 等义改写的问句；型号、数值必须原样保留
           */
          question: string;
          /**
           * 可选：词法检索用的关键词，不写按问句切词
           */
          lexical_terms?: string[];
        }[]
      | null;
    /**
     * 必须原样出现的词（型号、编号）
     */
    protected_terms?: string[] | null;
    /**
     * 术语库领域，用于术语扩展
     */
    domains?: string[] | null;
    /**
     * 本次阅读服务于哪个对象（续接用）
     */
    owner_id?: string | null;
    /**
     * 只在这些对象中召回
     */
    scope_owner_ids?: string[] | null;
    /**
     * 可选的显式字段条件，最多 16 条：[{id, kind: literal|numeric, field, operator: eq|ne|lt|le|gt|ge, expected, unit?}]。只在原文的“字段：值”中核对，冲突的对象排后、不删除
     */
    field_conditions?:
      | {
          id: string;
          kind: "literal" | "numeric";
          /**
           * 原文“字段：值”中的字段名
           */
          field: string;
          /**
           * literal 只能用 eq/ne
           */
          operator: "eq" | "ne" | "lt" | "le" | "gt" | "ge";
          /**
           * literal 写文本，numeric 写数值
           */
          expected: string | number;
          /**
           * 仅 numeric 可写，如 °C、kPa
           */
          unit?: string;
        }[]
      | null;
  }
}

export declare namespace ReadingAssess {
  /**
   * 三态判断一批对象；返回下一批或“可以开始读”的提示。用户的改判优先，不会被覆盖。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * [{owner_id, verdict, reason, digest, accepted_windows?}]。verdict：relevant=窗口显示它能回答问题或提供必要依据，值得完整阅读；uncertain=信息不足以判断，先 reading_peek 或交给用户；irrelevant=明确无关。reason 写依据窗口里的什么内容；digest 原样抄筛选包的 digest；quick 策略在 accepted_windows 列出可作为笔记依据的窗口 entry_id
     *
     * @minItems 1
     */
    judgments: [
      {
        owner_id: string;
        verdict: "relevant" | "uncertain" | "irrelevant";
        /**
         * 依据窗口里的什么内容做出判断
         */
        reason: string;
        /**
         * 原样抄筛选包的 digest
         */
        digest: string;
        /**
         * 仅 quick 策略：可作为笔记依据的窗口 entry_id
         */
        accepted_windows?: string[];
      },
      ...{
        owner_id: string;
        verdict: "relevant" | "uncertain" | "irrelevant";
        /**
         * 依据窗口里的什么内容做出判断
         */
        reason: string;
        /**
         * 原样抄筛选包的 digest
         */
        digest: string;
        /**
         * 仅 quick 策略：可作为笔记依据的窗口 entry_id
         */
        accepted_windows?: string[];
      }[]
    ];
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingPeek {
  /**
   * 查看某对象更多的命中窗口（用于 uncertain 的对象）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 本会话筛选结果中的对象 ID
     */
    owner_id: string;
  }
}

export declare namespace ReadingRead {
  /**
   * 阅读判为相关的对象：第 1 页附目录，按 unread_pages 读完全部页。quick 策略不可用。
   * 对象在阅读期间有新提交时要求从第 1 页重读（STALE_BASIS）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 本会话筛选结果中的对象 ID
     */
    owner_id: string;
    /**
     * 页码，从 1 起；按上次返回的 unread_pages 依次读完
     */
    page?: number;
    /**
     * 不填即可：第一轮召回的对象整读；图扩展带来的对象默认 targets（定点阅读，只交付被关系指到的节点及必要上下文）。只有定点内容不足以判断、确有必要了解整个对象时才填 full 升级为整读，并在笔记中说明原因；整读计入会话的整读对象上限
     */
    scope?: ("targets" | "full") | null;
  }
}

export declare namespace ReadingNote {
  /**
   * 保存某对象的研究笔记（standard/associative 须先读完全部页；定点阅读读完定点页即可）；出处补全为固定引用。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 本会话筛选结果中的对象 ID
     */
    owner_id: string;
    /**
     * 研究笔记（Markdown）：只写材料中的知识——问题如何演进、方法/公式（定义变量与单位）/条件、关键数据、反例与局限；关键句旁就近标出处 ID；不重复本次问题，不写自己的操作计划
     */
    note: string;
    /**
     * 出处 ID：必须是本会话交付过的。要细：引用到真正支撑内容的 L1 块（MEM-…#块ID）或结论（CLM-…），不要只写整条记录（MEM-…）；也可以是 SRC-…。quick 下只能是已采纳的窗口。出处也是联想（图扩展）的种子
     */
    sources: string[];
    /**
     * 可选的探索线索（材料提到但未核实的方向、可能相关的对象）；只作导航，不会成为结论
     */
    clues?: string[] | null;
    /**
     * 可选的扩展锚点 [{ref, why}]，最多 3 个：ref 是本笔记 sources 中值得顺着关系去追的节点（例如该块引用了另一项目的实测、该结论有待查的反例），why 写原因。没有值得追的就不写；写了锚点时，联想优先从锚点出发
     */
    expand_from?:
      | {
          /**
           * 本笔记 sources 中的一项
           */
          ref: string;
          /**
           * 为什么值得顺着关系去追
           */
          why: string;
        }[]
      | null;
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingSynthesize {
  /**
   * 跨对象综合笔记；所依据的任何单篇笔记改变或陈旧后，综合稿自动失效。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 跨对象综合笔记（Markdown）：比较、归纳各对象的知识，关键句旁就近标出处 ID；不重复问题，不写操作计划
     */
    note: string;
    /**
     * 出处 ID：必须是本会话交付过的 L1 块（MEM-…#块ID）、结论（CLM-…）、记录或 SRC-…
     */
    sources: string[];
    /**
     * 综合了哪些对象的笔记（这些对象必须已有单篇笔记）
     */
    owner_ids: string[];
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingAskUser {
  /**
   * 向用户提问。fulltext=申请全文补偿（仅当发现召回不可用、不完整或不足，见 fulltext_available；批准后得到
   * approval_id）；broaden=申请扩大检索：放宽条件、去掉限定词或型号、改问更宽的问题都算，prompt 写清新问法、
   * 理由与预计多带来哪类材料（等义改写、补保护词、改错别字不算扩大，不用申请）；direction=请用户给联想方向；
   * clarify=澄清问题。用户可在对话或工作台回答。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 请求类型，含义见工具说明
     */
    kind: "fulltext" | "broaden" | "direction" | "clarify";
    /**
     * 给用户看的问题：写清要什么、为什么、批准后会怎样
     */
    prompt: string;
    /**
     * 可选的候选答案
     */
    options?: string[] | null;
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingRecall {
  /**
   * 补充召回。fulltext=全文补偿；broaden=按用户批准的新问法再召回一轮；两者都需要用户批准。
   * association=联想（仅 associative 策略，受轮数上限）：本轮相关对象读完、笔记写好后，从笔记出处（扩展锚点优先）
   * 出发只走跨对象的边一跳，把与 focus 相关的新候选加入下一批筛选包；扩展来的对象判为相关后默认定点阅读。
   * 不要通过新开 reading_start 换更宽的问题来绕过扩大检索的审批。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * fulltext 全文补偿、broaden 扩大检索（两者都要用户批准）；association 联想（仅 associative 策略）
     */
    mode: "fulltext" | "broaden" | "association";
    /**
     * fulltext、broaden 模式必填：用户批准后的 approval_id（只能用一次）
     */
    approval_id?: string | null;
    /**
     * association 模式：一句话写当前还缺什么（例如“缺少长记录上 late 与 windows 的实测对比”），不要重复原问题；只用于给图邻居排序，不召回新范围，不需要审批。不填时用用户补充的方向或会话问题
     */
    focus?: string | null;
    /**
     * broaden 模式必填：用户批准的新问法
     */
    question?: string | null;
    /**
     * 最多 2 条等义改写 [{language: zh|en|other, question, lexical_terms?}]；技术问题建议补一条英文；型号、数值必须原样保留
     */
    variants?:
      | {
          /**
           * 可选的改写编号
           */
          id?: string;
          language: "zh" | "en" | "other";
          /**
           * 等义改写的问句；型号、数值必须原样保留
           */
          question: string;
          /**
           * 可选：词法检索用的关键词，不写按问句切词
           */
          lexical_terms?: string[];
        }[]
      | null;
    /**
     * broaden 模式可选：必须原样出现的词
     */
    protected_terms?: string[] | null;
    /**
     * 已停用：联想改为图扩展；要换更宽的问法先 reading_ask_user(kind=broaden)。传入会返回参数错误
     */
    association_text?: string | null;
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingFinish {
  /**
   * 结束会话并返回交接文本（综合稿优先；陈旧笔记不交付；超出上限的笔记整篇省略并列出缺口）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 一句话说明结束原因或结论去向，如“已回答，交给 work 保存”
     */
    outcome?: string;
    /**
     * 可选 UUID；网络重试时沿用同一个，不会重复执行
     */
    request_id?: string | null;
  }
}

export declare namespace ReadingStatus {
  /**
   * 会话概况、当前批次、交接预览、预算消耗，以及用户在工作台上的新输入（每条只交付一次）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
  }
}

export declare namespace ReadingDelegate {
  /**
   * 子 agent 任务包（只描述任务；实际派工由宿主完成）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    /**
     * 宿主能否派子 agent；不能时返回由你自己执行的步骤
     */
    host_supports_subagents: boolean;
  }
}

export declare namespace ReadingList {
  /**
   * 阅读会话列表（最新在前）。
   */
  export interface Args {
    /**
     * 只看该状态的会话
     */
    status?: string | null;
    /**
     * 只看服务于该对象的会话
     */
    owner_id?: string | null;
    limit?: number;
  }
}

export declare namespace ReadingView {
  /**
   * 会话完整状态（时间线、筛选包、笔记、请求）；只读，不消费收件箱。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
  }
}

export declare namespace ReadingRespond {
  /**
   * 回答 agent 的请求；批准全文补偿会生成 approval_id。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    request_id: string;
    decision: "approve" | "deny" | "answer";
    answer?: string;
  }
}

export declare namespace ReadingOverride {
  /**
   * 用户改判某个对象（agent 不能再覆盖）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    owner_id: string;
    verdict: "relevant" | "uncertain" | "irrelevant";
    reason?: string;
  }
}

export declare namespace ReadingDirect {
  /**
   * 补充联想方向（下一轮联想补查使用）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    text: string;
  }
}

export declare namespace ReadingConfigure {
  /**
   * 切换阅读策略。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    strategy: "standard" | "associative" | "quick";
  }
}

export declare namespace ReadingAbort {
  /**
   * 中止会话（agent 之后的写操作会被拒绝）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    reason?: string;
  }
}

export declare namespace ReadingArchive {
  /**
   * 归档或恢复会话（历史保留）。
   */
  export interface Args {
    /**
     * 阅读会话 ID（RS-…）
     */
    session_id: string;
    archived?: boolean;
  }
}

export declare namespace ConfigGet {
  /**
   * 当前配置、revision 与各连接器密钥是否已设置（不返回密钥内容）。
   */
  export interface Args {}
}

export declare namespace ConfigSchema {
  /**
   * 配置的 JSON Schema（字段、默认值与说明）。
   */
  export interface Args {}
}

export declare namespace ConfigUpdate {
  /**
   * 修改配置（如数据库/索引位置、嵌入模型、向量后端、默认阅读策略、连接器）。返回需要的后续动作。
   */
  export interface Args {
    /**
     * JSON Merge Patch：只写要改的字段；null 恢复默认
     */
    patch: {
      [k: string]: unknown;
    };
    /**
     * config_get 返回的 revision
     */
    expected_revision: string;
    /**
     * true 只校验并返回将要生效的配置、不写入
     */
    dry_run?: boolean;
  }
}

export declare namespace ConfigSecretSet {
  /**
   * 把密钥写入本机凭据管理器（只在工作台/CLI；不暴露给 agent）。
   */
  export interface Args {
    secret_ref: string;
    value: string;
  }
}

export declare namespace IndexStatus {
  /**
   * 某对象各投影/通道的就绪状态（stale 表示落后于当前 HEAD）。
   */
  export interface Args {
    /**
     * 对象 ID
     */
    owner_id: string;
  }
}

export declare namespace IndexReconcile {
  /**
   * 按当前 HEAD 补齐检索投影（不改记忆）；sources=true 同时更新来源文件投影。
   */
  export interface Args {
    /**
     * 只补这个对象；不给时补全部
     */
    owner_id?: string | null;
    /**
     * true 时即使水位已是最新也重建
     */
    force?: boolean;
    /**
     * 补全部对象时同时更新来源文件投影
     */
    sources?: boolean;
  }
}

export declare namespace IndexGaps {
  /**
   * 确定性缺口：缺 L4 概览、技术单元缺检索说明、L1 块过长、结果块没声明依赖、未解析的概念链接、
   * 没有入边的概念页、有未处理反例的结论、待确认的成熟度升级。内容是否充分仍需阅读判断。
   */
  export interface Args {}
}

export declare namespace IndexSearch {
  /**
   * 直接检索投影（调试/诊断用；正式查询请用 query_search，它会回到固定版本核对）。
   */
  export interface Args {
    /**
     * 检索文本
     */
    query: string;
    corpus?: "fulltext" | "discovery" | "source";
    owner_ids?: string[] | null;
    levels?: string[] | null;
    review_filter?: "all" | "reviewed_only" | "exclude_unsafe" | "unreviewed_only";
    group?: "record" | "owner";
    limit?: number;
  }
}

export declare namespace SystemCapabilities {
  /**
   * 当前能力与降级情况（向量模型、重排模型、连接器）。
   */
  export interface Args {}
}

export declare namespace SystemGuide {
  /**
   * 读取 burrowl 工作流说明全文（与 Skill 文件相同）。客户端没有加载 burrowl Skill 时，用它代替 Skill。
   */
  export interface Args {
    /**
     * rules 公共规则；recording 记忆记录写作标准；burrowl-work / burrowl-read / burrowl-consolidate 工作流；burrowl-dev-check 框架开发
     */
    name: "rules" | "recording" | "burrowl-work" | "burrowl-read" | "burrowl-consolidate" | "burrowl-dev-check";
  }
}

export declare namespace SystemValidate {
  /**
   * 全工作区只读校验：提交链与指纹、写锁与残留、来源文件是否变化或缺失、索引是否落后。不修复。
   */
  export interface Args {}
}

export declare namespace SystemOps {
  /**
   * 全部操作的目录（名称、分组、读写类型、说明、参数 schema）。
   */
  export interface Args {
    /**
     * 只列这些分组，如 memory、reading
     */
    groups?: string[] | null;
  }
}

export declare namespace SystemToolErrors {
  /**
   * 最近被拒绝的工具调用与内部错误，按工具汇总（R20 D-10）：参数错误（input）、其他拒绝（other）、内部错误（internal）
   * 的次数和最常出错的参数路径，以及最近的内部错误 incident_id。参数错误多的工具，说明说明或示例需要改；不含参数内容。
   */
  export interface Args {
    /**
     * 最多统计最近多少条
     */
    limit?: number;
  }
}

export declare namespace ServerInfo {
  /**
   * 工作台进程：运行中的版本、已安装的版本（不同表示需要重启才能用上新版本）、进程号、启动时间、网址、日志。
   */
  export interface Args {}
}

export declare namespace ServerStop {
  /**
   * 停止工作台（处理完当前请求后退出）。AI 客户端里经 stdio 运行的 MCP 是另外的进程，不受影响。
   */
  export interface Args {}
}

export declare namespace ServerRestart {
  /**
   * 重启工作台：新进程加载当前安装的版本，接管同一网址；页面稍后自动恢复。AI 客户端的 MCP 需在客户端里重启。
   */
  export interface Args {}
}

export interface OpArgs {
  "hub.connectors": HubConnectors.Args;
  "hub.health": HubHealth.Args;
  "hub.plan": HubPlan.Args;
  "hub.apply": HubApply.Args;
  "hub.status": HubStatus.Args;
  "hub.search": HubSearch.Args;
  "wiki.list": WikiList.Args;
  "wiki.read": WikiRead.Args;
  "wiki.configure": WikiConfigure.Args;
  "hub.candidates_pull": HubCandidatesPull.Args;
  "exchange.export": ExchangeExport.Args;
  "memory.render": MemoryRender.Args;
  "review.queue": ReviewQueue.Args;
  "review.submit": ReviewSubmit.Args;
  "review.batch": ReviewBatch.Args;
  "review.history": ReviewHistory.Args;
  "provenance.impact": ProvenanceImpact.Args;
  "provenance.lineage": ProvenanceLineage.Args;
  "provenance.question_validity": ProvenanceQuestionValidity.Args;
  "provenance.formal": ProvenanceFormal.Args;
  "relations.graph": RelationsGraph.Args;
  "relations.similar": RelationsSimilar.Args;
  "relations.associations": RelationsAssociations.Args;
  "relations.decide": RelationsDecide.Args;
  "relations.candidates": RelationsCandidates.Args;
  "relations.view": RelationsView.Args;
  "relations.view_save": RelationsViewSave.Args;
  "relations.notes": RelationsNotes.Args;
  "relations.note_add": RelationsNoteAdd.Args;
  "relations.note_resolve": RelationsNoteResolve.Args;
  "relations.note_edit": RelationsNoteEdit.Args;
  "relations.note_answer": RelationsNoteAnswer.Args;
  "relations.note_reply": RelationsNoteReply.Args;
  "relations.note_apply": RelationsNoteApply.Args;
  "relations.note_reject": RelationsNoteReject.Args;
  "relations.note_clear": RelationsNoteClear.Args;
  "maintenance.plan": MaintenancePlan.Args;
  "maintenance.plan_from_candidates": MaintenancePlanFromCandidates.Args;
  "maintenance.visibility_candidates": MaintenanceVisibilityCandidates.Args;
  "maintenance.ref_rewrite_candidates": MaintenanceRefRewriteCandidates.Args;
  "maintenance.plan_ref_rewrite": MaintenancePlanRefRewrite.Args;
  "maintenance.plan_visibility": MaintenancePlanVisibility.Args;
  "maintenance.plans": MaintenancePlans.Args;
  "maintenance.handoff": MaintenanceHandoff.Args;
  "maintenance.review": MaintenanceReview.Args;
  "maintenance.apply": MaintenanceApply.Args;
  "maintenance.status": MaintenanceStatus.Args;
  "monitor.run": MonitorRun.Args;
  "monitor.candidates": MonitorCandidates.Args;
  "monitor.resolve": MonitorResolve.Args;
  "monitor.observations": MonitorObservations.Args;
  "runs.create": RunsCreate.Args;
  "runs.register": RunsRegister.Args;
  "runs.execute": RunsExecute.Args;
  "runs.update": RunsUpdate.Args;
  "runs.rollback": RunsRollback.Args;
  "runs.search": RunsSearch.Args;
  "runs.discard": RunsDiscard.Args;
  "runs.trash": RunsTrash.Args;
  "runs.restore": RunsRestore.Args;
  "runs.empty_trash": RunsEmptyTrash.Args;
  "sources.list": SourcesList.Args;
  "sources.roots": SourcesRoots.Args;
  "sources.add_root": SourcesAddRoot.Args;
  "sources.relocate_root": SourcesRelocateRoot.Args;
  "sources.register": SourcesRegister.Args;
  "sources.register_many": SourcesRegisterMany.Args;
  "sources.snapshot": SourcesSnapshot.Args;
  "sources.attach": SourcesAttach.Args;
  "sources.relocate": SourcesRelocate.Args;
  "sources.update": SourcesUpdate.Args;
  "memory.owners": MemoryOwners.Args;
  "memory.owner_route": MemoryOwnerRoute.Args;
  "memory.owner_create": MemoryOwnerCreate.Args;
  "memory.owner_adopt": MemoryOwnerAdopt.Args;
  "memory.records": MemoryRecords.Args;
  "memory.activity": MemoryActivity.Args;
  "memory.kinds": MemoryKinds.Args;
  "memory.contract": MemoryContract.Args;
  "memory.inspect": MemoryInspect.Args;
  "memory.commit": MemoryCommit.Args;
  "memory.document": MemoryDocument.Args;
  "memory.figure": MemoryFigure.Args;
  "ui.ref_targets": UiRefTargets.Args;
  "ui.transcriptions": UiTranscriptions.Args;
  "memory.timeline": MemoryTimeline.Args;
  "memory.raw_materials": MemoryRawMaterials.Args;
  "memory.raw_material": MemoryRawMaterial.Args;
  "memory.resume": MemoryResume.Args;
  "memory.export": MemoryExport.Args;
  "memory.consolidate_prepare": MemoryConsolidatePrepare.Args;
  "memory.consolidate_apply": MemoryConsolidateApply.Args;
  "memory.summary_materials": MemorySummaryMaterials.Args;
  "memory.summary_save": MemorySummarySave.Args;
  "memory.feedback": MemoryFeedback.Args;
  "memory.recovery": MemoryRecovery.Args;
  "memory.records_discard": MemoryRecordsDiscard.Args;
  "memory.records_restore": MemoryRecordsRestore.Args;
  "memory.records_discarded": MemoryRecordsDiscarded.Args;
  "memory.owner_discard": MemoryOwnerDiscard.Args;
  "memory.owner_trash": MemoryOwnerTrash.Args;
  "memory.owner_restore": MemoryOwnerRestore.Args;
  "memory.owner_empty_trash": MemoryOwnerEmptyTrash.Args;
  "query.search": QuerySearch.Args;
  "query.page": QueryPage.Args;
  "query.cancel": QueryCancel.Args;
  "query.assemble": QueryAssemble.Args;
  "query.expand": QueryExpand.Args;
  "query.deepen": QueryDeepen.Args;
  "query.documents": QueryDocuments.Args;
  "reading.start": ReadingStart.Args;
  "reading.assess": ReadingAssess.Args;
  "reading.peek": ReadingPeek.Args;
  "reading.read": ReadingRead.Args;
  "reading.note": ReadingNote.Args;
  "reading.synthesize": ReadingSynthesize.Args;
  "reading.ask_user": ReadingAskUser.Args;
  "reading.recall": ReadingRecall.Args;
  "reading.finish": ReadingFinish.Args;
  "reading.status": ReadingStatus.Args;
  "reading.delegate": ReadingDelegate.Args;
  "reading.list": ReadingList.Args;
  "reading.view": ReadingView.Args;
  "reading.respond": ReadingRespond.Args;
  "reading.override": ReadingOverride.Args;
  "reading.direct": ReadingDirect.Args;
  "reading.configure": ReadingConfigure.Args;
  "reading.abort": ReadingAbort.Args;
  "reading.archive": ReadingArchive.Args;
  "config.get": ConfigGet.Args;
  "config.schema": ConfigSchema.Args;
  "config.update": ConfigUpdate.Args;
  "config.secret_set": ConfigSecretSet.Args;
  "index.status": IndexStatus.Args;
  "index.reconcile": IndexReconcile.Args;
  "index.gaps": IndexGaps.Args;
  "index.search": IndexSearch.Args;
  "system.capabilities": SystemCapabilities.Args;
  "system.guide": SystemGuide.Args;
  "system.validate": SystemValidate.Args;
  "system.ops": SystemOps.Args;
  "system.tool_errors": SystemToolErrors.Args;
  "server.info": ServerInfo.Args;
  "server.stop": ServerStop.Args;
  "server.restart": ServerRestart.Args;
}

export type OpName = keyof OpArgs;

export const OP_EFFECTS: Record<OpName, string> = {
  "hub.connectors": "read",
  "hub.health": "read",
  "hub.plan": "read",
  "hub.apply": "admin",
  "hub.status": "read",
  "hub.search": "read",
  "wiki.list": "read",
  "wiki.read": "read",
  "wiki.configure": "admin",
  "hub.candidates_pull": "write",
  "exchange.export": "write",
  "memory.render": "read",
  "review.queue": "read",
  "review.submit": "write",
  "review.batch": "write",
  "review.history": "read",
  "provenance.impact": "read",
  "provenance.lineage": "read",
  "provenance.question_validity": "read",
  "provenance.formal": "read",
  "relations.graph": "read",
  "relations.similar": "read",
  "relations.associations": "read",
  "relations.decide": "write",
  "relations.candidates": "read",
  "relations.view": "read",
  "relations.view_save": "user",
  "relations.notes": "read",
  "relations.note_add": "user",
  "relations.note_resolve": "user",
  "relations.note_edit": "user",
  "relations.note_answer": "user",
  "relations.note_reply": "write",
  "relations.note_apply": "user",
  "relations.note_reject": "user",
  "relations.note_clear": "user",
  "maintenance.plan": "write",
  "maintenance.plan_from_candidates": "user",
  "maintenance.visibility_candidates": "read",
  "maintenance.ref_rewrite_candidates": "read",
  "maintenance.plan_ref_rewrite": "user",
  "maintenance.plan_visibility": "user",
  "maintenance.plans": "read",
  "maintenance.handoff": "user",
  "maintenance.review": "write",
  "maintenance.apply": "user",
  "maintenance.status": "read",
  "monitor.run": "write",
  "monitor.candidates": "read",
  "monitor.resolve": "user",
  "monitor.observations": "read",
  "runs.create": "write",
  "runs.register": "write",
  "runs.execute": "write",
  "runs.update": "write",
  "runs.rollback": "write",
  "runs.search": "read",
  "runs.discard": "write",
  "runs.trash": "read",
  "runs.restore": "write",
  "runs.empty_trash": "user",
  "sources.list": "read",
  "sources.roots": "read",
  "sources.add_root": "write",
  "sources.relocate_root": "write",
  "sources.register": "write",
  "sources.register_many": "write",
  "sources.snapshot": "write",
  "sources.attach": "write",
  "sources.relocate": "write",
  "sources.update": "user",
  "memory.owners": "read",
  "memory.owner_route": "read",
  "memory.owner_create": "write",
  "memory.owner_adopt": "write",
  "memory.records": "read",
  "memory.activity": "read",
  "memory.kinds": "read",
  "memory.contract": "read",
  "memory.inspect": "read",
  "memory.commit": "write",
  "memory.document": "read",
  "memory.figure": "read",
  "ui.ref_targets": "read",
  "ui.transcriptions": "read",
  "memory.timeline": "read",
  "memory.raw_materials": "read",
  "memory.raw_material": "read",
  "memory.resume": "read",
  "memory.export": "read",
  "memory.consolidate_prepare": "read",
  "memory.consolidate_apply": "write",
  "memory.summary_materials": "read",
  "memory.summary_save": "write",
  "memory.feedback": "write",
  "memory.recovery": "admin",
  "memory.records_discard": "write",
  "memory.records_restore": "write",
  "memory.records_discarded": "read",
  "memory.owner_discard": "write",
  "memory.owner_trash": "read",
  "memory.owner_restore": "write",
  "memory.owner_empty_trash": "user",
  "query.search": "read",
  "query.page": "read",
  "query.cancel": "read",
  "query.assemble": "read",
  "query.expand": "read",
  "query.deepen": "read",
  "query.documents": "read",
  "reading.start": "write",
  "reading.assess": "write",
  "reading.peek": "write",
  "reading.read": "write",
  "reading.note": "write",
  "reading.synthesize": "write",
  "reading.ask_user": "write",
  "reading.recall": "write",
  "reading.finish": "write",
  "reading.status": "read",
  "reading.delegate": "read",
  "reading.list": "read",
  "reading.view": "read",
  "reading.respond": "user",
  "reading.override": "user",
  "reading.direct": "user",
  "reading.configure": "user",
  "reading.abort": "user",
  "reading.archive": "user",
  "config.get": "read",
  "config.schema": "read",
  "config.update": "admin",
  "config.secret_set": "user",
  "index.status": "read",
  "index.reconcile": "admin",
  "index.gaps": "read",
  "index.search": "read",
  "system.capabilities": "read",
  "system.guide": "read",
  "system.validate": "read",
  "system.ops": "read",
  "system.tool_errors": "read",
  "server.info": "user",
  "server.stop": "user",
  "server.restart": "user",
};
