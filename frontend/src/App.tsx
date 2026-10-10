import { useEffect } from "react";
import { useOp } from "./api";
import { Home } from "./features/Home";
import { Hub } from "./features/Hub";
import { Maintenance } from "./features/Maintenance";
import { useNoteCounts } from "./features/Notes";
import { Owners, OwnerView, RecordView } from "./features/Memory";
import { ReadingDetail } from "./features/reading/ReadingDetail";
import { ReadingList } from "./features/reading/ReadingList";
import { Relations } from "./features/Relations";
import { Review } from "./features/Review";
import { Search } from "./features/Search";
import { Settings } from "./features/Settings";
import { href, useRoute } from "./ui";

// 线条图标（24×24 视口，描边继承文字颜色），只为导航辨识，不承载信息
const ICONS: Record<string, string> = {
  "": "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  reading: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 19V5M8 7h6M8 11h6",
  search: "M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM20 20l-4-4",
  memory: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  relations: "M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M18 8m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M10 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M8.3 7l7.3.7M7 8.3l2 7.4M16.3 9.8l-4.6 6.4",
  review: "M9 12l2 2 4-4M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z",
  maintenance: "M3 12h4l3-8 4 16 3-8h4",
  hub: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5",
  settings: "M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
};

// 导航分组：日常工作 / 知识治理 / 设置
const NAV = [
  [
    ["", "首页"],
    ["reading", "阅读会话"],
    ["search", "检索"],
    ["memory", "系统记忆"],
    ["relations", "关系图"],
  ],
  [
    ["review", "知识复核"],
    ["maintenance", "维护与监测"],
    ["hub", "知识中枢"],
  ],
  [["settings", "设置"]],
] as const;
const GROUP_TITLES = ["工作", "治理", ""];

export function Icon({ name }: { name: string }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name] ?? ""} />
    </svg>
  );
}

function Page({ route }: { route: string[] }) {
  const [page, a, b] = route;
  switch (page) {
    case "reading":
      return a ? <ReadingDetail key={a} sessionId={a} /> : <ReadingList />;
    case "review":
      return <Review />;
    case "search":
      return <Search />;
    case "memory":
      if (a && b) return <RecordView key={b} ownerId={a} recordId={b} />;
      return a ? <OwnerView key={a} ownerId={a} /> : <Owners />;
    case "relations":
      return <Relations />;
    case "maintenance":
      return <Maintenance />;
    case "hub":
      return <Hub />;
    case "settings":
      return <Settings />;
    default:
      return <Home />;
  }
}

export function App() {
  const route = useRoute();
  const waiting = useOp<{ pending_requests: number }[]>("reading.list", { status: "waiting_user", limit: 50 });
  // 待处理数字要跟上 AI 新发起或用户刚处理完的请求：与阅读列表同样每 5 秒、以及窗口回到前台时刷新
  // R15：关系图旁显示等你处理的工作台笔记（待你确认 + 需要你回答）
  const notes = useNoteCounts();
  useEffect(() => {
    const refresh = () => {
      waiting.reload();
      notes.reload();
    };
    const timer = window.setInterval(refresh, 5_000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [waiting.reload, notes.reload]);
  const pending = waiting.data?.reduce((sum, s) => sum + s.pending_requests, 0) ?? 0;
  const badges: Record<string, number> = { reading: pending, relations: notes.proposed + notes.needsUser };
  const current = route[0] ?? "";
  return (
    <div className="app">
      <nav>
        <a className="brand" href="#/">
          <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="9" />
            <path d="M8 22c0-5 3.6-9 8-9s8 4 8 9M13 22v-3.5a3 3 0 0 1 6 0V22" />
          </svg>
          <span>
            研发工作台
            <small>burrowl</small>
          </span>
        </a>
        {NAV.map((group, i) => (
          <div className="nav-group" key={i}>
            {GROUP_TITLES[i] ? <div className="nav-title">{GROUP_TITLES[i]}</div> : null}
            {group.map(([key, text]) => (
              <a key={key} href={key ? href(key) : "#/"} className={current === key ? "on" : ""} aria-current={current === key ? "page" : undefined}>
                <Icon name={key} />
                <span>{text}</span>
                {badges[key] ? <span className="dot">{badges[key]}</span> : null}
              </a>
            ))}
          </div>
        ))}
      </nav>
      <main key={current}>
        <Page route={route} />
      </main>
    </div>
  );
}
