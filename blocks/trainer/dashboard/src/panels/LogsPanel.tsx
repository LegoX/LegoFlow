import { useState, useEffect, useRef, useCallback } from "react";
import { Search, ArrowDown, Pause, Play, FileText } from "lucide-react";

interface Props {
  runId: string | null;
  refreshInterval: number;
}

interface LogChunk {
  lines: string[];
  total_lines: number;
  offset: number;
  file?: string;
}

interface LogFile {
  name: string;
  size: number;
}

export default function LogsPanel({ runId, refreshInterval }: Props) {
  const [lines, setLines] = useState<string[]>([]);
  const [totalLines, setTotalLines] = useState(0);
  const [search, setSearch] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(false);
  const [files, setFiles] = useState<LogFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>("");
  const containerRef = useRef<HTMLDivElement>(null);
  const lastOffsetRef = useRef(0);

  const fetchFiles = useCallback(async () => {
    try {
      const res = await fetch(`/api/log-files`);
      if (!res.ok) return;
      const data: LogFile[] = await res.json();
      setFiles(data);
    } catch {
      // ignore
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    if (!runId || paused) return;
    try {
      setLoading(true);
      const tail = 500;
      const params = new URLSearchParams({
        tail: String(tail),
        offset: String(lastOffsetRef.current),
      });
      if (selectedFile) params.set("file", selectedFile);
      const res = await fetch(
        `/api/runs/${runId}/logs?${params.toString()}`,
      );
      if (!res.ok) return;
      const data: LogChunk = await res.json();
      if (data.lines.length > 0) {
        if (lastOffsetRef.current === 0) {
          setLines(data.lines);
        } else {
          setLines((prev) => [...prev, ...data.lines].slice(-2000));
        }
        lastOffsetRef.current = data.offset + data.lines.length;
      }
      setTotalLines(data.total_lines);
      // If server picked a default file (no explicit selection yet), sync UI.
      if (!selectedFile && data.file) setSelectedFile(data.file);
    } catch {
      // ignore fetch errors
    } finally {
      setLoading(false);
    }
  }, [runId, paused, selectedFile]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles, runId]);

  // Switching run: drop the previous file so the server's per-run matcher
  // gets to pick a default. Then `data.file` from fetchLogs syncs the UI.
  useEffect(() => {
    setSelectedFile("");
  }, [runId]);

  // Reset buffer whenever the active run or file changes.
  useEffect(() => {
    lastOffsetRef.current = 0;
    setLines([]);
    fetchLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, selectedFile]);

  useEffect(() => {
    if (paused) return;
    const id = setInterval(fetchLogs, refreshInterval * 1000);
    return () => clearInterval(id);
  }, [fetchLogs, refreshInterval, paused]);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(atBottom);
  };

  const scrollToBottom = () => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
      setAutoScroll(true);
    }
  };

  const filtered = search
    ? lines.filter((l) => l.toLowerCase().includes(search.toLowerCase()))
    : lines;

  const highlightLine = (line: string): string => {
    if (line.includes("ERROR") || line.includes("error"))
      return "text-rose-400";
    if (line.includes("WARNING") || line.includes("warning"))
      return "text-amber-400";
    if (line.match(/^.*step:\d+/)) return "text-emerald-300";
    return "text-slate-400";
  };

  if (!runId) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-500">
        Select a run to view logs
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">Logs</h2>
          <p className="text-xs text-slate-500">
            {totalLines.toLocaleString()} lines total
            {search && ` / ${filtered.length} matched`}
            {selectedFile && ` · ${selectedFile}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <FileText
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500 pointer-events-none"
            />
            <select
              value={selectedFile}
              onChange={(e) => setSelectedFile(e.target.value)}
              className="bg-slate-800 text-slate-300 text-xs border border-slate-700 rounded-lg pl-8 pr-3 py-1.5 max-w-[28rem] focus:outline-none focus:border-indigo-500 appearance-none"
              title={
                files.length === 0
                  ? "No log files discovered"
                  : `${files.length} log file(s)`
              }
              disabled={files.length === 0}
            >
              {files.length === 0 && <option value="">(no log files)</option>}
              {files.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name} ({(f.size / 1024 / 1024).toFixed(1)} MB)
                </option>
              ))}
            </select>
          </div>
          <div className="relative">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500"
            />
            <input
              type="text"
              placeholder="Filter logs..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-slate-800 text-slate-300 text-xs border border-slate-700 rounded-lg pl-8 pr-3 py-1.5 w-56 focus:outline-none focus:border-indigo-500"
            />
          </div>
          <button
            onClick={() => setPaused(!paused)}
            className={`p-1.5 rounded-lg border transition-colors ${
              paused
                ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                : "border-slate-700 bg-slate-800 text-slate-400 hover:text-slate-200"
            }`}
            title={paused ? "Resume" : "Pause"}
          >
            {paused ? <Play size={14} /> : <Pause size={14} />}
          </button>
          <button
            onClick={scrollToBottom}
            className="p-1.5 rounded-lg border border-slate-700 bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
            title="Scroll to bottom"
          >
            <ArrowDown size={14} />
          </button>
        </div>
      </div>

      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 min-h-0 overflow-y-auto rounded-xl bg-slate-950 border border-slate-800/60 font-mono text-xs leading-5"
        style={{ maxHeight: "calc(100vh - 200px)" }}
      >
        {loading && lines.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-slate-500">
            Loading logs...
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-slate-500">
            {search ? "No matching lines" : "No log output yet"}
          </div>
        ) : (
          <table className="w-full">
            <tbody>
              {filtered.map((line, i) => (
                <tr
                  key={i}
                  className="hover:bg-slate-900/50 group"
                >
                  <td className="px-3 py-0 text-right text-slate-600 select-none w-12 align-top group-hover:text-slate-500">
                    {i + 1}
                  </td>
                  <td
                    className={`px-2 py-0 whitespace-pre-wrap break-all ${highlightLine(line)}`}
                  >
                    {line}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!autoScroll && (
        <button
          onClick={scrollToBottom}
          className="fixed bottom-6 right-6 px-3 py-1.5 rounded-lg bg-indigo-500 text-white text-xs shadow-lg hover:bg-indigo-400 transition-colors flex items-center gap-1.5"
        >
          <ArrowDown size={12} />
          New output
        </button>
      )}
    </div>
  );
}
