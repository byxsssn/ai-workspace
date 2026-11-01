import { useEffect, useState } from "react";
import { createApi, errorMessage } from "./api";
import { SourceLink, sourceTime } from "./SourceLink";
import type { PypiRanking, RankingComparison } from "./types";

// 公共数据请求始终不附带用户凭据，与私人会话独立。
const publicApi = createApi();
const compactNumber = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export function PypiRankings({ onCompare, busy = false }: {
  onCompare: (selection: RankingComparison) => void;
  busy?: boolean;
}) {
  const [data, setData] = useState<PypiRanking | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("downloads-desc");
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    publicApi.pypiRankings(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setSelected((current) => current.filter((project) => result.items.some((item) => item.project === project)));
      })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  const search = query.trim().toLowerCase();
  const items = (data?.items ?? []).filter((item) =>
    (!category || item.category === category) &&
    (!search || `${item.project} ${item.name} ${item.description}`.toLowerCase().includes(search)),
  ).sort((a, b) => {
    if (sort === "name") return a.project.localeCompare(b.project, "en");
    if (a.download_count === null) return b.download_count === null ? a.project.localeCompare(b.project, "en") : 1;
    if (b.download_count === null) return -1;
    const difference = a.download_count - b.download_count;
    return (sort === "downloads-asc" ? difference : -difference) || a.project.localeCompare(b.project, "en");
  });

  function toggle(project: string) {
    setSelected((current) => current.includes(project) ? current.filter((value) => value !== project) : current.length < 3 ? [...current, project] : current);
  }

  return (
    <section className="rankings-page" aria-label="PyPI 排行榜">
      <div className="page-breadcrumb">探索 / Python 技术生态<span className="public-badge">公开数据</span></div>
      <header className="rankings-heading"><div><p className="eyebrow">EXPLORE THE PYTHON ECOSYSTEM</p><h2>精选 Python 包月度下载榜</h2></div><button className="secondary" disabled={loading} onClick={() => setAttempt((value) => value + 1)}>{loading ? "读取中…" : "重新读取"}</button></header>
      <p className="ranking-scope">按精选候选包的下载量排序。下载次数不等于用户数、技术质量或学习优先级。</p>
      {loading && !data ? <div className="ranking-empty" role="status">正在读取真实数据快照…</div> : null}
      {error ? <div className="notice error" role="alert">{error}{data ? " 当前保留上次读取的数据。" : ""}</div> : null}
      {data ? <>
        <div className="ranking-summary">
          <div><span>统计月份</span><strong>{data.period}</strong><small>完整自然月</small></div>
          <div><span>精选范围</span><strong>{data.candidate_count}<small> 个包</small></strong><small>本期匹配 {data.matched_count} 个</small></div>
          <div><span>上游快照覆盖</span><strong>{data.source_count.toLocaleString("zh-CN")}<small> 个包</small></strong><small>{data.upstream} 下载统计</small></div>
          <div className="ranking-source"><span>数据来源</span><SourceLink url={data.source_url}>Top PyPI Packages</SourceLink><small>目录版本 {data.catalog_version}</small></div>
        </div>
        <p className="ranking-snapshot-times">源文件更新：{sourceTime(data.source_updated_at)}<span>本站获取：{sourceTime(data.fetched_at)}</span></p>
        {data.stale || data.warnings.length ? <p className="ranking-warning" role="status">{data.warnings.length ? data.warnings.join(" ") : "当前展示最近一次成功快照，数据可能已过期。"}</p> : null}
        <div className="ranking-controls">
          <label className="ranking-search">搜索包名或用途<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如 FastAPI、Agent、数据库…" /></label>
          <label>排序<select value={sort} onChange={(event) => setSort(event.target.value)}><option value="downloads-desc">下载量从高到低</option><option value="downloads-asc">下载量从低到高</option><option value="name">包名 A–Z</option></select></label>
        </div>
        <div className="ranking-categories" role="group" aria-label="技术类别"><button aria-pressed={!category} onClick={() => setCategory("")}>全部类别</button>{data.categories.map((item) => <button key={item.id} aria-pressed={category === item.id} onClick={() => setCategory(item.id)}>{item.label}</button>)}</div>
        <div className="ranking-results"><p aria-live="polite">显示 {items.length} / {data.candidate_count} 个候选包</p><span>名次按全部候选包计算 · 勾选 1–3 个包交给助手比较</span></div>
        {selected.length ? <div className="ranking-comparison" aria-label="比较所选技术"><div><span className="muted small">已选 {selected.length} / 3：{selected.join("、")}</span></div><div className="comparison-actions"><button className="secondary" disabled={busy} onClick={() => setSelected([])}>清空选择</button><button disabled={busy} onClick={() => onCompare({ projects: selected, period: data.period })}>{busy ? "正在准备…" : "让助手比较"}<span aria-hidden="true"> ↗</span></button></div></div> : null}
        {items.length ? <div className="ranking-table-wrap"><table className="ranking-table"><caption className="visually-hidden">{data.period} 精选 Python 包下载量，选择最多三个包交给助手比较</caption><thead><tr><th scope="col" className="ranking-position">名次</th><th scope="col">技术 / 包</th><th scope="col" className="ranking-number">月下载量 / 次</th><th scope="col" className="ranking-select">选择</th></tr></thead><tbody>{items.map((item) => <tr key={item.project} className={selected.includes(item.project) ? "selected-ranking" : undefined}>
          <td className="ranking-position">{item.rank ?? "—"}</td>
          <td><div className="ranking-package"><span className="package-mark" aria-hidden="true">{item.name.slice(0, 2)}</span><div><h3><SourceLink url={item.pypi_url}>{item.name}</SourceLink></h3><p className="ranking-package-meta">{item.project} · {item.category_label}</p><p className="ranking-description">{item.description}</p></div></div></td>
          <td className="ranking-number">{item.download_count === null ? <span className="not-listed">本期未收录</span> : <><strong>{compactNumber.format(item.download_count)}</strong><small>{item.download_count.toLocaleString("zh-CN")}</small></>}</td>
          <td className="ranking-select"><input type="checkbox" aria-label={`选择 ${item.name}`} checked={selected.includes(item.project)} disabled={busy || (!selected.includes(item.project) && selected.length >= 3)} onChange={() => toggle(item.project)} /></td>
        </tr>)}</tbody></table></div> : <div className="ranking-empty"><h3>没有匹配的候选包</h3><p>试试其他名称，或查看全部类别。</p><button className="secondary" onClick={() => { setQuery(""); setCategory(""); }}>重置筛选</button></div>}
        <details className="ranking-method"><summary>数据来源与统计说明</summary><p>来源：{data.source} · 上游：{data.upstream}。这里只比较目录中的 {data.candidate_count} 个包，分类由本站维护。缺席上游快照表示“本期未收录”，不能当作零下载。</p><p>下载量会受依赖安装、自动化构建和缓存等因素影响。跨类别排名只表示下载次数，不能直接判断技术优劣。当前版本展示单月快照，不推算增长率。</p><dl><div><dt>源文件更新</dt><dd>{sourceTime(data.source_updated_at)}</dd></div><div><dt>快照获取</dt><dd>{sourceTime(data.fetched_at)}</dd></div><div><dt>最近检查</dt><dd>{sourceTime(data.checked_at)}</dd></div><div><dt>快照标识</dt><dd className="snapshot-id">{data.snapshot_id}</dd></div></dl></details>
      </> : null}
    </section>
  );
}
