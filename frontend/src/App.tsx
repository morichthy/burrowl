import { useOp } from "./api";
import { Home } from "./features/Home";
import { Hub } from "./features/Hub";
import { Maintenance } from "./features/Maintenance";
import { Owners, OwnerView, RecordView } from "./features/Memory";
import { ReadingDetail } from "./features/reading/ReadingDetail";
import { ReadingList } from "./features/reading/ReadingList";
import { Relations } from "./features/Relations";
import { Review } from "./features/Review";
import { Search } from "./features/Search";
import { Settings } from "./features/Settings";
import { href, useRoute } from "./ui";

const NAV = [
  ["", "首页"],
  ["reading", "阅读会话"],
  ["review", "知识复核"],
  ["search", "检索"],
  ["memory", "系统记忆"],
  ["relations", "关系图"],
  ["maintenance", "维护与监测"],
  ["hub", "知识中枢"],
  ["settings", "设置"],
] as const;

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
  const pending = waiting.data?.reduce((sum, s) => sum + s.pending_requests, 0) ?? 0;
  return (
    <div className="app">
      <nav>
        <div className="brand">研发工作台</div>
        {NAV.map(([key, text]) => (
          <a key={key} href={key ? href(key) : "#/"} className={(route[0] ?? "") === key ? "on" : ""}>
            {text}
            {key === "reading" && pending ? <span className="dot">{pending}</span> : null}
          </a>
        ))}
      </nav>
      <main>
        <Page route={route} />
      </main>
    </div>
  );
}
