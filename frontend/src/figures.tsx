// 记录中登记的图（R21 附件、R24 锚点）：按固定修订读取；记录页、文稿、原文展开与引用别的记录的图共用。
import type { ReactNode } from "react";
import { useOp } from "./api";
import { ErrorBox, Loading } from "./ui";

export interface FigureEntry { label?: string; caption: string }

/** 与服务端 core/technical.figure_anchors 一致：显式 label 优先，没有 label 的图按序号推导为 fig-N。 */
export function figureAnchors(figures: FigureEntry[] | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  (figures ?? []).forEach((f, i) => { if (!f.label) out[`fig-${i + 1}`] = i; });
  (figures ?? []).forEach((f, i) => { if (f.label) out[f.label] = i; });
  return out;
}

/** 与服务端 figure_display_name 一致：fig-1 显示为 Figure 1，其余 label 原样，没有 label 显示"图 N"。 */
export function figureName(figures: FigureEntry[] | undefined, index: number): string {
  const label = figures?.[index]?.label;
  if (!label) return `图 ${index + 1}`;
  const match = /^fig-(.+)$/.exec(label);
  return match ? `Figure ${match[1]}` : label;
}

interface FigureData { data_url: string; caption: string; name: string; basis_issues?: { target_id: string; code: string }[] }

/** key 为序号（旧写法、本记录图列表）或锚点名（![[#fig-1]]、引用别的记录的图）；
 * 图注取服务端返回的显示名与原文图注，读取完成前用 fallback。marker 放在图注后（引用编号）。 */
export function RecordFigure({ ownerId, recordId, revision, figureKey, fallback, marker }: {
  ownerId: string; recordId: string; revision: number; figureKey: number | string; fallback?: string; marker?: ReactNode;
}) {
  const figure = useOp<FigureData>("memory.figure", {
    owner_id: ownerId, record_id: recordId, revision,
    ...(typeof figureKey === "number" ? { index: figureKey } : { label: figureKey }),
  });
  const issues = figure.data?.basis_issues ?? [];
  const caption = figure.data ? `${figure.data.name}：${figure.data.caption}`
    : fallback ?? (typeof figureKey === "number" ? `图 ${figureKey + 1}` : figureKey);
  return (
    <figure className="figure">
      <ErrorBox error={figure.error} />
      {figure.data ? <img src={figure.data.data_url} alt={figure.data.caption} /> : <Loading show={!figure.error} />}
      {issues.length ? (
        <p className="hint">
          图片与登记时一致；本记录的其他依据有变化，需复核：
          {issues.map((i) => <code key={i.target_id}>{i.target_id}（{i.code}）</code>)}
        </p>
      ) : null}
      <figcaption>{caption}{marker}</figcaption>
    </figure>
  );
}
