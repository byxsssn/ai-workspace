import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import type { Api } from "./api";
import { SourceLink, sourceTime } from "./SourceLink";
import type { LearningRecord, LearningRecordDetail, LearningRecordList, LearningSource } from "./types";

interface Props {
  api: Api;
  busy: boolean;
  entryError: string;
  onContinue: (record: LearningRecord) => void;
  onSourceChange: () => void;
}

export function LearningMap({ api, busy, entryError, onContinue, onSourceChange }: Props) {
  const [source, setSource] = useState<LearningSource>("notion");
  return (
    <section className="learning-map" aria-labelledby="map-title">
      <div className="map-heading">
        <div>
          <p className="eyebrow">记录 · 理解 · 实践</p>
          <h2 id="map-title">学习地图</h2>
          <p className="muted">看清学到哪里，再从一个具体问题继续。</p>
        </div>
        <label className="source-picker">
          记录来源
          <select value={source} disabled={busy} onChange={(event) => {
            setSource(event.target.value as LearningSource);
            onSourceChange();
          }}>
            <option value="notion">我的 Notion</option>
            <option value="demo">演示数据</option>
          </select>
        </label>
      </div>
      {source === "demo" ? (
        <p className="notice demo-notice" role="status">正在查看虚构演示数据，不代表你的实际进度。学习对话也会使用这份演示记录。</p>
      ) : (
        <p className="map-source-note"><span className="source-dot" aria-hidden="true" />只读连接 Notion · 学习记录与笔记不会被自动修改</p>
      )}
      {entryError ? <p className="error notice" role="alert">{entryError}</p> : null}
      <RecordCollection key={source} api={api} source={source} busy={busy} onContinue={onContinue} />
    </section>
  );
}

function RecordCollection({ api, source, busy, onContinue }: Pick<Props, "api" | "busy" | "onContinue"> & { source: LearningSource }) {
  const [data, setData] = useState<LearningRecordList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.learningRecords(source, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setData(result); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, source, attempt]);

  const items = data?.items ?? [];
  const categories = [...new Set(items.map((item) => item.category || "未分类"))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const statuses = [...new Set(items.map((item) => item.learning_status || "未记录"))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const filtered = items.filter((item) =>
    (!category || (item.category || "未分类") === category) &&
    (!status || (item.learning_status || "未记录") === status) &&
    (!query.trim() || `${item.topic_name} ${item.current_progress ?? ""} ${item.next_step ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())),
  );
  const groups = categories.map((name) => ({ name, items: filtered.filter((item) => (item.category || "未分类") === name) })).filter((group) => group.items.length > 0);

  function refresh() {
    setSelectedId(null);
    setAttempt((value) => value + 1);
  }

  function closeDetail() {
    const id = selectedId;
    setSelectedId(null);
    if (id) document.getElementById(`record-${id}`)?.focus();
  }

  return (
    <>
      <div className="map-toolbar">
        <p className="muted small">{data ? <>本次读取 <time dateTime={data.fetched_at}>{sourceTime(data.fetched_at)}</time></> : "学习记录按类型分组，状态来自原始记录。"}</p>
        <button className="secondary" disabled={loading || busy} onClick={refresh}>刷新记录</button>
      </div>
      {error ? (
        <div className="map-empty map-error">
          <span className="empty-symbol" aria-hidden="true">!</span>
          <h3>暂时无法读取{source === "notion" ? "我的 Notion" : "演示记录"}</h3>
          <p className="error" role="alert">{error}</p>
          {source === "notion" ? <p className="muted small">可在上方明确选择「演示数据」先体验地图；不会替换为演示记录。</p> : null}
          <button className="secondary" disabled={loading || busy} onClick={refresh}>重新读取</button>
        </div>
      ) : loading && !data ? (
        <div className="map-empty" role="status"><span className="loading-dot" aria-hidden="true" />正在读取{source === "notion" ? "Notion 学习记录" : "演示记录"}…</div>
      ) : data ? (
        <>
          <div className="map-stats" aria-label="本次记录概览">
            <div><strong>{items.length}</strong><span>已读取主题</span></div>
            <div><strong>{items.filter((item) => item.project_practice === true).length}</strong><span>有实践记录</span></div>
            <div><strong>{items.filter((item) => !!item.next_step?.trim()).length}</strong><span>已记录下一步</span></div>
            <p>进度来自你的记录。<br />看过、理解与独立实践分别记录。</p>
          </div>
          {data.has_more ? <p className="notice" role="status">本次只读取了部分主题，当前列表不是完整学习地图。</p> : null}
          {data.warnings.some((warning) => warning !== "demo_data_not_personal_progress") ? <p className="notice small">来源返回了读取提示，部分记录或字段可能不完整。请结合原文核对。</p> : null}
          <div className="map-filters">
            <label className="map-search">搜索主题<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="主题、当前进度或下一步" /></label>
            <label>类型<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">全部类型</option>{categories.map((name) => <option key={name}>{name}</option>)}</select></label>
            <label>学习状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{statuses.map((name) => <option key={name}>{name}</option>)}</select></label>
          </div>
          <div className="map-results-heading">
            <p className="muted small">显示 {filtered.length} / {items.length} 个主题{loading ? " · 正在刷新…" : ""}</p>
            <p className="muted small">学习助手目前支持 FastAPI，其余主题可查看记录与原文。</p>
          </div>
          <div className={`map-board ${selectedId ? "with-detail" : ""}`}>
            <div className="map-groups" aria-busy={loading}>
              {groups.map((group) => (
                <section className="topic-group" key={group.name} aria-label={`${group.name}主题`}>
                  <div className="group-heading"><h3>{group.name}</h3><span>{group.items.length}</span></div>
                  <div className="topic-grid">
                    {group.items.map((item) => (
                      <article className={`topic-card ${selectedId === item.id ? "is-selected" : ""}`} key={item.id}>
                        <div className="card-topline"><span className={`status-chip ${item.learning_status === "学习中" ? "active" : ""}`}>{item.learning_status || "状态未记录"}</span>{item.topic_id === "fastapi" ? <span className="assistant-chip">可继续学习</span> : null}</div>
                        <h4>{item.topic_name || "未命名主题"}</h4>
                        <p className="card-progress">{item.current_progress?.trim() || "还没有记录当前进度。"}</p>
                        <div className="card-next"><span>下一步</span><p>{item.next_step?.trim() || "尚未记录，先从原文梳理一个小目标。"}</p></div>
                        <div className="card-facts"><span>掌握记录 · {item.proficiency || "未知"}</span><span>{item.project_practice === true ? "已有实践记录" : item.project_practice === false ? "未记录实践" : "实践情况未知"}</span></div>
                        <div className="card-actions">
                          <button id={`record-${item.id}`} className="text-button" aria-expanded={selectedId === item.id} aria-controls="learning-record-detail" aria-label={`查看 ${item.topic_name} 详情`} onClick={() => setSelectedId(item.id)}>查看详情 <span aria-hidden="true">→</span></button>
                          {item.topic_id === "fastapi" ? <button className="continue-button" disabled={busy} onClick={() => onContinue(item)} aria-label={`继续学习 ${item.topic_name}`}>{busy ? "正在打开…" : "继续学习"}</button> : null}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              ))}
              {filtered.length === 0 ? <div className="map-empty"><h3>{items.length === 0 ? "这里还没有学习记录" : "没有匹配的主题"}</h3><p className="muted">{items.length === 0 ? "请检查 Notion 数据源是否已有记录，并已共享给应用连接。" : "换一个关键词，或清除类型和状态筛选。"}</p>{items.length > 0 ? <button className="secondary" onClick={() => { setQuery(""); setCategory(""); setStatus(""); }}>清除筛选</button> : null}</div> : null}
            </div>
            {selectedId ? <RecordDetail key={selectedId} api={api} id={selectedId} source={source} busy={busy} onClose={closeDetail} onContinue={onContinue} /> : null}
          </div>
        </>
      ) : null}
    </>
  );
}

function RecordDetail({ api, id, source, busy, onClose, onContinue }: Pick<Props, "api" | "busy" | "onContinue"> & { id: string; source: LearningSource; onClose: () => void }) {
  const [record, setRecord] = useState<LearningRecordDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => { panel.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.learningRecord(id, source, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setRecord(result); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, id, source, attempt]);

  return (
    <aside className="record-detail" id="learning-record-detail" ref={panel} tabIndex={-1} aria-label="学习记录详情">
      <div className="detail-top"><span className="eyebrow">记录详情</span><button className="text-button" onClick={onClose} aria-label="关闭学习记录详情">关闭 <span aria-hidden="true">×</span></button></div>
      {loading ? <p role="status" className="empty">正在读取笔记…</p> : error ? <div className="detail-section"><p role="alert" className="error">{error}</p><button className="secondary" onClick={() => setAttempt((value) => value + 1)}>重试详情</button></div> : record ? (
        <>
          <h3>{record.topic_name}</h3>
          <div className="detail-tags"><span className="status-chip">{record.learning_status || "状态未记录"}</span><span className="source-badge">{record.source === "notion" ? "Notion" : "虚构演示"}</span>{record.category ? <span className="muted small">{record.category}</span> : null}</div>
          <dl className="record-facts">
            <div><dt>当前进度</dt><dd>{record.current_progress || "未记录"}</dd></div>
            <div className="next-step-detail"><dt>下一步</dt><dd>{record.next_step || "未记录"}</dd></div>
            <div><dt>掌握记录</dt><dd>{record.proficiency || "未知"}</dd></div>
            <div><dt>项目实践</dt><dd>{record.project_practice === true ? "已有实践记录" : record.project_practice === false ? "未记录实践" : "未知"}</dd></div>
            {record.priority ? <div><dt>原记录优先级</dt><dd>{record.priority}</dd></div> : null}
          </dl>
          <div className="record-links"><SourceLink url={record.source_url}>Notion 原文</SourceLink><SourceLink url={record.official_docs_url}>官方文档</SourceLink><SourceLink url={record.evidence_url}>实践成果</SourceLink></div>
          {record.topic_id === "fastapi" ? <button className="detail-continue" disabled={busy} onClick={() => onContinue(record)}>{busy ? "正在打开…" : "在助手中继续学习 FastAPI"}</button> : <p className="notice small">当前学习助手先支持 FastAPI。这个主题可先查看笔记与参考原文。</p>}
          <section className="detail-section" aria-label="学习笔记">
            <h4>学习笔记</h4>
            {!record.notes_complete ? <p className="notice small">笔记未完整读取，以下是本次可用内容。完整笔记请查看原文。</p> : null}
            {record.warnings.some((warning) => warning !== "demo_data_not_personal_progress") ? <p className="muted small">这条记录有读取提示，部分字段可能缺失，请结合原文核对。</p> : null}
            <div className="notes-content">{record.notes_markdown.trim() || "还没有可展示的笔记。"}</div>
          </section>
          <div className="detail-times"><p>原文更新：<time dateTime={record.source_updated_at || undefined}>{sourceTime(record.source_updated_at)}</time></p><p>本次读取：<time dateTime={record.fetched_at}>{sourceTime(record.fetched_at)}</time></p></div>
        </>
      ) : null}
    </aside>
  );
}
