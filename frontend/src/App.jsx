import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Sparkles,
  Upload,
  Search,
  Terminal,
  ArrowRight,
  Loader2,
  Database,
  Layers,
  ShieldCheck,
  Cpu,
  Info,
  Settings2,
  X,
  ChevronDown,
  ChevronUp,
  FileText,
} from "lucide-react";

import { apiFetch, sleep } from "./services/api";

function uuidv4() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// human-friendly labels for trace steps
function stepLabel(name) {
  const n = String(name || "").toLowerCase();
  if (n.includes("retrieve_context") || n.includes("retrieve") || n === "retrieval") return "Retrieve evidence";
  if (n.includes("generate_answer") || n.includes("generate") || n === "generation") return "Generate grounded answer";
  if (n.includes("chunk") || n.includes("embed")) return "Chunk & embed";
  return name || "step";
}

function statusPill(status) {
  const s = String(status || "").toLowerCase();
  if (["ok", "success", "succeeded"].includes(s)) return "bg-green-100 text-green-700";
  if (["failed", "error"].includes(s)) return "bg-red-100 text-red-700";
  return "bg-slate-100 text-slate-600";
}

const I18N = {
  EN: {
    onboarding_title: "AI-native RAG backend",
    onboarding_desc:
      "Production-grade RAG with idempotency, observability, and source-grounded answers.",
    studio_config: "Studio Configuration",
    output_language: "OUTPUT LANGUAGE",
    backend: "Backend",
    checking: "checking…",
    online: "online",
    offline: "offline",
    enter_workspace: "Enter Workspace",
    knowledge_ingestion: "Knowledge Ingestion",
    ingestion_hint: "Paste text or upload a file — we’ll turn it into a searchable knowledge base.",
    textarea_ph: "Support docs, HR policies, API specs...",
    index_base: "Index Base",
    upload_file: "Upload file",
    file_loaded: "📎 File loaded into editor",
    unsupported_file: "⚠️ Unsupported file type (use .txt/.md/.csv/.json/.pdf)",
    generating_embeddings: "Generating Embeddings...",
    ask_ph: "Ask the studio…",
    sources_used: "SOURCES USED",
    verified: "VERIFIED",
    trace: "TRACE",
    replay: "↻ REPLAY",
    intro: "Intro",
    environment: "Environment",
    idle: "Idle",
    backend_connected: "✅ Backend connected",
    backend_unavailable: "⚠️ Backend unavailable",
    uploading_doc: "📄 Uploading document",
    upload_ok: "✅ Document uploaded",
    upload_failed: "❌ Upload failed",
    ask_failed: "❌ Ask failed",
    trace_loaded: "🧩 Trace loaded",
    trace_error: "⚠️ Trace error",
    replay_info: "🔁 Replay with same Idempotency-Key",
    no_run_yet: "No run_id yet. Ask a question first.",
    load_trace_steps: "Load trace steps",
    trace_tip: "Tip: show HR how retrieval + generation are logged as AgentSteps.",
    trace_empty: "Trace is empty.",
    input: "Input",
    output: "Output",
    loading_trace: "Loading trace…",
    uploading: "Uploading…",
    grounding: "Grounding response…",
  },
  RU: {
    onboarding_title: "ProductOps Copilot",
    onboarding_desc:
      "RAG-система корпоративного уровня: источники, трассировка шагов и безопасные ретраи (idempotency).",
    studio_config: "Настройки",
    output_language: "ЯЗЫК ОТВЕТА",
    backend: "Система",
    checking: "проверяю…",
    online: "online",
    offline: "offline",
    enter_workspace: "Начать работу",
    knowledge_ingestion: "База знаний",
    ingestion_hint: "Вставьте ваш текст или документ — и он станет базой знаний.",
    textarea_ph: "Инструкции, регламенты, спецификации...",
    index_base: "Обработать данные",
    upload_file: "Выбрать файл",
    file_loaded: "📎 Файл загружен в редактор",
    unsupported_file: "⚠️ Неподдерживаемый тип (используй .txt/.md/.csv/.json/.pdf)",
    generating_embeddings: "Считаю эмбеддинги…",
    ask_ph: "Задайте вопрос…",
    sources_used: "ИСТОЧНИКОВ",
    verified: "ПРОВЕРЕНО",
    trace: "TRACE",
    replay: "↻ ПОВТОР",
    intro: "Интро",
    environment: "Среда",
    idle: "Ожидание",
    backend_connected: "✅ Бэкенд подключен",
    backend_unavailable: "⚠️ Бэкенд недоступен",
    uploading_doc: "📄 Загружаю документ",
    upload_ok: "✅ Документ загружен",
    upload_failed: "❌ Ошибка загрузки",
    ask_failed: "❌ Ошибка запроса",
    trace_loaded: "🧩 Trace загружен",
    trace_error: "⚠️ Ошибка trace",
    replay_info: "🔁 Повтор с тем же Idempotency-Key",
    no_run_yet: "run_id ещё нет. Сначала задай вопрос.",
    load_trace_steps: "Загрузить шаги trace",
    trace_tip: "Подсказка: покажи HR, как retrieval + generation логируются в AgentSteps.",
    trace_empty: "Trace пуст.",
    input: "Вход",
    output: "Выход",
    loading_trace: "Загружаю trace…",
    uploading: "Загружаю…",
    grounding: "Готовлю grounded-ответ…",
  },
};

export default function ProductOpsStudio() {
  const [showOnboarding, setShowOnboarding] = useState(true);
  const [lang, setLang] = useState("EN"); // EN | RU

  const t = (key) => I18N?.[lang]?.[key] ?? I18N?.EN?.[key] ?? key;

  const [healthOk, setHealthOk] = useState(null);

  const [docText, setDocText] = useState("");
  const [pickedFile, setPickedFile] = useState(null);
  const [pdfUrl, setPdfUrl] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [docId, setDocId] = useState(null);
  const [docStatus, setDocStatus] = useState(null);

  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);

  const [answer, setAnswer] = useState(null); // { text, sources, run_id }
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const [idemKey, setIdemKey] = useState(null);
  const [runId, setRunId] = useState(null);

  const [showDebug, setShowDebug] = useState(false);
  const [traceLoading, setTraceLoading] = useState(false);
  const [steps, setSteps] = useState(null);

  const [messages, setMessages] = useState([]);
  const messagesRef = useRef(null);

  const fileInputRef = useRef(null);

  const canAsk = useMemo(() => !!docId && docStatus === "embedded", [docId, docStatus]);

  // autoscroll
  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTo({ top: messagesRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [messages, uploading, asking, traceLoading]);

  // health on mount
  useEffect(() => {
    (async () => {
      try {
        await apiFetch("/api/health/");
        setHealthOk(true);
        setMessages([{ type: "system", content: t("backend_connected") }]);
      } catch (e) {
        setHealthOk(false);
        setMessages([{ type: "system", content: `${t("backend_unavailable")}: ${e.message}` }]);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // poll doc status until embedded/failed
  useEffect(() => {
    if (!docId) return;
    let stopped = false;

    (async () => {
      while (!stopped) {
        try {
          const d = await apiFetch(`/api/kb/documents/${docId}/`);
          const st = d?.status ?? d?.state ?? d?.processing_status ?? null;
          setDocStatus(st);

          if (st === "embedded") {
            setMessages((p) => [...p, { type: "system", content: `✅ Document ${docId} embedded` }]);
            return;
          }
          if (st === "failed" || st === "error") {
            setMessages((p) => [...p, { type: "system", content: `❌ Document ${docId} failed: ${st}` }]);
            return;
          }
        } catch {
          // keep polling but slower
        }
        await sleep(1200);
      }
    })();

    return () => {
      stopped = true;
    };
  }, [docId]);

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
  }, [pdfUrl]);

  function formatKb(bytes) {
    if (!Number.isFinite(bytes)) return "";
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function clearPickedFile() {
    try {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    } catch {
      // ignore
    }
    setPdfUrl(null);
    setPickedFile(null);
    setDocText("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function onPickFile(e) {
      const file = e.target.files?.[0];
      if (!file) return;

      const ok = /\.(txt|md|csv|json|pdf)$/i.test(file.name);
      if (!ok) {
        setMessages((p) => [...p, { type: "system", content: t("unsupported_file") }]);
        e.target.value = "";
        return;
      }

      // cleanup previous pdf preview (if any)
      if (pdfUrl) {
        try {
          URL.revokeObjectURL(pdfUrl);
        } catch {
          // ignore
        }
        setPdfUrl(null);
      }

      setPickedFile(file);

      // Preview text in textarea (nice UX); backend still receives the original file.
      const isPdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
      if (isPdf) {
        const url = URL.createObjectURL(file);
        setPdfUrl(url);
        setDocText("");
        setMessages((p) => [
          ...p,
          { type: "system", content: `${t("file_loaded")}: ${file.name} (${formatKb(file.size)})` },
        ]);
        e.target.value = "";
        return;
      }
      try {
        const text = await file.text();
        setDocText(text);
        setMessages((p) => [
          ...p,
          { type: "system", content: `${t("file_loaded")}: ${file.name} (${Math.round(text.length / 1000)}k chars)` },
        ]);
      } catch {
        setDocText("");
        setMessages((p) => [
          ...p,
          { type: "system", content: `${t("file_loaded")}: ${file.name}` },
        ]);
      }

      e.target.value = "";
    }


  async function handleUpload() {
      // Allow: either pasted text OR picked file
      if (!pickedFile && !docText.trim()) return;

      setUploading(true);
      setAnswer(null);
      setSourcesOpen(false);
      setRunId(null);
      setSteps(null);
      setIdemKey(null);

      const uploadLabel = pickedFile
        ? `${t("uploading_doc")} (${pickedFile.name})`
        : `${t("uploading_doc")} (${docText.length} chars)`;

      setMessages((p) => [...p, { type: "user", content: uploadLabel }]);

      try {
        let up;

        if (pickedFile) {
          const fd = new FormData();
          fd.append("file", pickedFile);
          fd.append("language", lang);

          up = await apiFetch("/api/kb/upload_file/", {
            method: "POST",
            body: fd,
          });
        } else {
          up = await apiFetch("/api/kb/upload_text/", {
            method: "POST",
            body: {
              content: docText,
              language: lang,
            },
          });
        }

        const id = up?.id ?? up?.document_id ?? up?.doc_id;
        const st = up?.status ?? up?.state ?? "uploaded";

        setDocId(id || null);
        setDocStatus(st);

        setMessages((p) => [
          ...p,
          { type: "bot", content: `${t("upload_ok")}\n📊 ID: ${id}\n⏳ Processing (Celery)…` },
        ]);

        if (pdfUrl) {
          try {
            URL.revokeObjectURL(pdfUrl);
          } catch {
            // ignore
          }
        }
        setPdfUrl(null);
        setDocText("");
        setPickedFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
      } catch (e) {
        setMessages((p) => [...p, { type: "bot", content: `${t("upload_failed")}: ${e.message}` }]);
      } finally {
        setUploading(false);
      }
    }

  async function handleAsk() {
    if (!question.trim() || !canAsk) return;

    const q = question.trim();
    const key = uuidv4();

    setAsking(true);
    setAnswer(null);
    setSourcesOpen(false);
    setRunId(null);
    setSteps(null);

    setIdemKey(key);
    setMessages((p) => [...p, { type: "user", content: q }]);

    try {
      const data = await apiFetch("/api/ask/", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: {
          question: q,
          document_id: docId,
          language: lang,
          retriever: "hybrid",
          answer_mode: "deterministic",
        },
      });

      const text = data?.answer ?? data?.result ?? data?.output ?? "";
      const sources = data?.sources ?? data?.citations ?? [];
      const rid = data?.run_id ?? data?.id ?? null;

      setAnswer({ text, sources: Array.isArray(sources) ? sources : [], run_id: rid });
      setRunId(rid);

      setMessages((p) => [
        ...p,
        { type: "bot", content: text || "(empty answer)" },
        ...(Array.isArray(sources) ? [{ type: "system", content: `📚 Sources: ${sources.length}` }] : []),
        ...(rid ? [{ type: "system", content: `🔎 run_id: ${rid}` }] : []),
      ]);

      setQuestion("");
    } catch (e) {
      setMessages((p) => [...p, { type: "system", content: `${t("ask_failed")}: ${e.message}` }]);
    } finally {
      setAsking(false);
    }
  }

  async function loadTrace() {
    if (!runId) return;
    setTraceLoading(true);
    try {
      const data = await apiFetch(`/api/runs/${runId}/steps/`);
      const arr = Array.isArray(data) ? data : data?.results ?? data?.steps ?? [];
      setSteps(Array.isArray(arr) ? arr : []);
      setMessages((p) => [...p, { type: "system", content: `${t("trace_loaded")} (${runId})` }]);
    } catch (e) {
      setMessages((p) => [...p, { type: "system", content: `${t("trace_error")}: ${e.message}` }]);
    } finally {
      setTraceLoading(false);
    }
  }

  async function replaySameKey() {
    if (!idemKey || !answer) return;
    setMessages((p) => [...p, { type: "system", content: `${t("replay_info")}: ${idemKey}` }]);

    try {
      const lastUser = [...messages].reverse().find((m) => m.type === "user")?.content || "";
      const data = await apiFetch("/api/ask/", {
        method: "POST",
        headers: { "Idempotency-Key": idemKey },
        body: {
          question: lastUser,
          document_id: docId,
          language: lang,
          retriever: "hybrid",
          answer_mode: "deterministic",
        },
      });

      const text = data?.answer ?? data?.result ?? data?.output ?? "";
      setMessages((p) => [...p, { type: "bot", content: `REPLAY ✅\n${text}` }]);
    } catch (e) {
      setMessages((p) => [...p, { type: "system", content: `Replay result: ${e.message}` }]);
    }
  }

  if (showOnboarding) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-6 selection:bg-cyan-100">
        <div className="w-full max-w-4xl bg-white rounded-[40px] shadow-2xl border border-slate-200/60 overflow-hidden flex flex-col md:flex-row">
          <div className="md:w-[45%] p-12 bg-slate-900 text-white flex flex-col justify-between relative overflow-hidden">
            <div className="absolute top-0 right-0 w-64 h-64 bg-cyan-500/10 blur-[100px] rounded-full -mr-32 -mt-32" />
            <div className="relative z-10">
              <div className="flex items-center gap-3 mb-12">
                <div className="w-10 h-10 bg-cyan-500 rounded-2xl flex items-center justify-center shadow-lg shadow-cyan-500/20">
                  <Sparkles size={20} className="text-white" />
                </div>
                <span className="font-bold tracking-tight text-lg">ProductOps Studio</span>
              </div>
              <h1 className="text-4xl font-extrabold leading-[1.1] mb-6">{t("onboarding_title")}</h1>
              <p className="text-slate-400 text-sm leading-relaxed mb-8">{t("onboarding_desc")}</p>
            </div>
            <div className="space-y-4 relative z-10">
              <div className="flex items-center gap-3 text-xs font-semibold text-slate-400">
                <ShieldCheck size={16} className="text-cyan-400" /> Safe Retries (Idempotency)
              </div>
              <div className="flex items-center gap-3 text-xs font-semibold text-slate-400">
                <Layers size={16} className="text-cyan-400" /> Full Observability (Runs & Steps)
              </div>
            </div>
          </div>

          <div className="md:w-[55%] p-12 flex flex-col justify-center">
            <h2 className="text-xl font-bold mb-8 text-slate-900">{t("studio_config")}</h2>

            <div className="space-y-8 mb-12">
              <div className="space-y-3">
                <label className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">
                  {t("output_language")}
                </label>
                <div className="flex p-1 bg-slate-100 rounded-2xl">
                  <button
                    onClick={() => setLang("EN")}
                    className={`flex-1 py-3 rounded-xl text-xs font-bold transition ${
                      lang === "EN" ? "bg-white shadow-sm text-slate-900" : "text-slate-400 hover:text-slate-600"
                    }`}
                  >
                    English
                  </button>
                  <button
                    onClick={() => setLang("RU")}
                    className={`flex-1 py-3 rounded-xl text-xs font-bold transition ${
                      lang === "RU" ? "bg-white shadow-sm text-slate-900" : "text-slate-400 hover:text-slate-600"
                    }`}
                  >
                    Русский
                  </button>
                </div>

                <div className="mt-3 text-xs text-slate-500">
                  {t("backend")}:{" "}
                  <span className={`font-bold ${healthOk ? "text-green-600" : "text-slate-400"}`}>
                    {healthOk === null ? t("checking") : healthOk ? t("online") : t("offline")}
                  </span>
                </div>
              </div>
            </div>

            <button
              onClick={() => setShowOnboarding(false)}
              className="w-full py-4 rounded-2xl bg-slate-900 text-white font-bold text-sm hover:bg-slate-800 transition-all shadow-xl shadow-slate-200 flex items-center justify-center gap-2 group"
            >
              {t("enter_workspace")}
              <ArrowRight size={18} className="group-hover:translate-x-1 transition-transform" />
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex overflow-hidden font-sans">
      {/* MINIMAL SIDE STATUS BAR */}
      <aside className="w-20 bg-white border-r border-slate-200/60 flex flex-col items-center py-8 gap-10">
        <div className="w-10 h-10 bg-slate-900 rounded-2xl flex items-center justify-center text-white shadow-lg">
          <Sparkles size={20} />
        </div>

        <div className="flex-1 flex flex-col gap-6">
          <div
            className={`p-3 rounded-2xl transition-colors ${docId ? "text-cyan-600 bg-cyan-50" : "text-slate-300"}`}
            title="Storage"
          >
            <Database size={20} />
          </div>
          <div
            className={`p-3 rounded-2xl transition-colors ${
              docStatus === "embedded" ? "text-cyan-600 bg-cyan-50" : "text-slate-300"
            }`}
            title="Processor"
          >
            <Cpu size={20} />
          </div>
          <div
            className={`p-3 rounded-2xl transition-colors ${answer ? "text-cyan-600 bg-cyan-50" : "text-slate-300"}`}
            title="Answer"
          >
            <Search size={20} />
          </div>
        </div>

        <button
          onClick={() => setShowDebug(!showDebug)}
          className={`p-3 rounded-2xl transition-colors ${
            showDebug ? "bg-slate-900 text-white" : "text-slate-400 hover:bg-slate-100"
          }`}
          title="Trace inspector"
        >
          <Terminal size={20} />
        </button>
      </aside>

      {/* MAIN */}
      <main className="flex-1 flex flex-col relative bg-[#F8FAFC]">
        {/* TOP TOOLBAR */}
        <header className="h-20 border-b border-slate-200/60 bg-white/80 backdrop-blur-md px-10 flex items-center justify-between sticky top-0 z-20">
          <div className="flex items-center gap-4">
            <span className="text-[11px] font-black uppercase tracking-[0.2em] text-slate-400">{t("environment")}</span>
            <div className="h-4 w-px bg-slate-200" />
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  docId ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.4)]" : "bg-slate-300"
                }`}
              />
              <span className="text-xs font-bold">{docId ? `doc_id: ${docId}` : t("idle")}</span>
              {docStatus ? <span className="text-xs text-slate-400">• {docStatus}</span> : null}
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="flex p-1 bg-slate-100 rounded-2xl">
              <button
                onClick={() => setLang("EN")}
                className={`px-3 py-2 rounded-xl text-xs font-bold transition ${
                  lang === "EN" ? "bg-white shadow-sm text-slate-900" : "text-slate-400 hover:text-slate-600"
                }`}
              >
                EN
              </button>
              <button
                onClick={() => setLang("RU")}
                className={`px-3 py-2 rounded-xl text-xs font-bold transition ${
                  lang === "RU" ? "bg-white shadow-sm text-slate-900" : "text-slate-400 hover:text-slate-600"
                }`}
              >
                RU
              </button>
            </div>

            <button
              onClick={() => setShowOnboarding(true)}
              className="text-xs font-bold text-slate-400 hover:text-slate-900 transition flex items-center gap-2"
            >
              <Settings2 size={14} /> {t("intro")}
            </button>
          </div>
        </header>

        {/* CANVAS */}
        <div className="flex-1 overflow-hidden flex flex-col max-w-5xl mx-auto w-full p-8 lg:p-12">
          <div ref={messagesRef} className="flex-1 overflow-y-auto space-y-10 pb-20 pr-4 custom-scrollbar">
            {/* INITIAL UPLOAD */}
            {!docId && (
              <div className="h-full flex flex-col items-center justify-center animate-in fade-in zoom-in duration-700">
                <div className="w-full max-w-xl space-y-6">
                  <div className="text-center mb-8">
                    <h2 className="text-2xl font-bold mb-2">{t("knowledge_ingestion")}</h2>
                    <p className="text-sm text-slate-400">{t("ingestion_hint")}</p>
                  </div>

                  <div className="relative group">
                    <textarea
                      value={docText}
                      onChange={(e) => setDocText(e.target.value)}
                      placeholder={t("textarea_ph")}
                      className="w-full h-64 p-8 rounded-[32px] bg-white border border-slate-200/60 shadow-2xl shadow-slate-200/50 focus:outline-none focus:ring-4 focus:ring-cyan-500/5 transition-all text-sm leading-relaxed resize-none whitespace-pre-wrap break-words"
                    />

                    {pdfUrl && (
                      <div className="mt-4 bg-white border border-slate-200/60 rounded-[24px] overflow-hidden shadow-sm">
                        <div className="flex items-center justify-between px-6 py-4">
                          <div className="flex flex-col">
                            <div className="text-slate-900 font-bold">PDF preview</div>
                            {pickedFile ? (
                              <div className="text-xs text-slate-400 mt-1">
                                {pickedFile.name}
                                {pickedFile.size ? ` • ${formatKb(pickedFile.size)}` : ""}
                              </div>
                            ) : null}
                          </div>
                          <div className="flex items-center gap-3">
                            <button
                              type="button"
                              onClick={clearPickedFile}
                              className="text-xs font-bold text-slate-400 hover:text-slate-900 transition"
                              title="Remove selected PDF"
                            >
                              Clear
                            </button>
                            <a
                              href={pdfUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs font-bold text-cyan-700 hover:text-cyan-900 transition"
                            >
                              Open
                            </a>
                          </div>
                        </div>
                        <div className="h-[520px] bg-slate-50">
                          <iframe
                            title="PDF Preview"
                            src={pdfUrl}
                            className="w-full h-full"
                          />
                        </div>
                      </div>
                    )}

                    {/* hidden file input */}
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".txt,.md,.csv,.json,.pdf,text/plain,text/markdown,application/json,text/csv,application/pdf"
                      className="hidden"
                      onChange={onPickFile}
                    />

                    <div className="absolute bottom-6 right-6 flex items-center gap-2">
                      <button
                        onClick={openFilePicker}
                        className="px-4 py-3 bg-white text-slate-700 rounded-2xl font-bold text-xs flex items-center gap-2 border border-slate-200/60 hover:bg-slate-50 transition-all"
                        title={t("upload_file")}
                      >
                        <FileText size={16} />
                        {t("upload_file")}
                      </button>

                      <button
                        onClick={handleUpload}
                        disabled={uploading || (!pickedFile && !docText.trim())}
                        className="px-6 py-3 bg-slate-900 text-white rounded-2xl font-bold text-xs flex items-center gap-2 hover:scale-105 active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
                        {t("index_base")}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* MESSAGES */}
            {messages.map((m, i) => (
              <div
                key={i}
                className={`flex ${m.type === "user" ? "justify-end" : "justify-start"} animate-in slide-in-from-bottom-2 fade-in duration-500`}
              >
                <div
                  className={`
                    max-w-[80%] p-6 rounded-[24px] text-sm leading-[1.6]
                    ${
                      m.type === "user"
                        ? "bg-slate-900 text-white rounded-tr-none"
                        : "bg-white border border-slate-200/60 shadow-sm rounded-tl-none text-slate-800"
                    }
                  `}
                >
                  {m.content}
                </div>
              </div>
            ))}

            {/* LOADER */}
            {(asking || uploading || traceLoading) && (
              <div className="flex items-center gap-3 text-slate-400">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-bounce" />
                </div>
                <span className="text-[10px] font-black uppercase tracking-widest opacity-50">
                  {uploading ? t("uploading") : traceLoading ? t("loading_trace") : t("grounding")}
                </span>
              </div>
            )}

            {/* ANSWER SOURCES */}
            {answer && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="flex flex-wrap gap-2 mt-2">
                  <button
                    onClick={() => setSourcesOpen(!sourcesOpen)}
                    className="px-4 py-2 bg-cyan-50 text-cyan-700 rounded-full text-[10px] font-bold border border-cyan-100 flex items-center gap-2 hover:bg-cyan-100 transition-colors"
                  >
                    <Info size={12} /> {answer.sources?.length || 0} {t("sources_used")}{" "}
                    {sourcesOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                  </button>

                  <button className="px-4 py-2 bg-slate-50 text-slate-400 rounded-full text-[10px] font-bold border border-slate-200 flex items-center gap-2 hover:bg-slate-100 transition-colors">
                    <ShieldCheck size={12} /> {t("verified")}
                  </button>

                  {runId && (
                    <button
                      onClick={loadTrace}
                      className="px-4 py-2 bg-slate-900 text-white rounded-full text-[10px] font-bold border border-slate-900 flex items-center gap-2 hover:bg-slate-800 transition-colors"
                      title="Load runs/steps trace"
                    >
                      <Terminal size={12} /> {t("trace")}
                    </button>
                  )}

                  {idemKey && (
                    <button
                      onClick={replaySameKey}
                      className="px-4 py-2 bg-white text-slate-700 rounded-full text-[10px] font-bold border border-slate-200 flex items-center gap-2 hover:bg-slate-50 transition-colors"
                      title="Replay same request with same Idempotency-Key"
                    >
                      {t("replay")}
                    </button>
                  )}
                </div>

                {sourcesOpen && (
                  <div className="mt-4 grid md:grid-cols-2 gap-4">
                    {(answer.sources || []).map((s, idx) => (
                      <div
                        key={idx}
                        className="p-4 bg-white border border-slate-200 rounded-2xl shadow-sm text-xs text-slate-500 leading-relaxed italic"
                      >
                        “{s.snippet || s.text || s.quote || JSON.stringify(s)}”
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* INPUT BAR */}
          {docId && (
            <div className="bg-white/40 backdrop-blur-xl border-t border-slate-200/60 p-6 -mx-8 lg:-mx-12 px-8 lg:px-12 sticky bottom-0">
              {!canAsk ? (
                <div className="flex items-center justify-center gap-3 py-4 text-cyan-600">
                  <Loader2 size={18} className="animate-spin" />
                  <span className="text-xs font-bold uppercase tracking-widest">{t("generating_embeddings")}</span>
                </div>
              ) : (
                <div className="max-w-3xl mx-auto relative group">
                  <input
                    type="text"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleAsk()}
                    placeholder={t("ask_ph")}
                    className="w-full py-5 px-8 pr-16 rounded-full border border-slate-200 bg-white shadow-2xl shadow-slate-200/50 focus:outline-none focus:ring-4 focus:ring-cyan-500/10 transition-all text-sm font-medium"
                    disabled={asking}
                  />
                  <button
                    onClick={handleAsk}
                    disabled={asking || !question.trim()}
                    className="absolute right-3 top-1/2 -translate-y-1/2 w-11 h-11 bg-slate-900 text-white rounded-full flex items-center justify-center hover:scale-110 active:scale-95 transition-all disabled:opacity-10"
                  >
                    <ArrowRight size={18} />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* DEBUG OVERLAY */}
        {showDebug && (
          <div className="absolute top-20 right-0 bottom-0 w-[420px] bg-white border-l border-slate-200 shadow-2xl z-40 animate-in slide-in-from-right duration-300 flex flex-col">
            <div className="p-6 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Terminal size={16} className="text-slate-400" />
                <h3 className="text-xs font-black uppercase tracking-widest">Trace Inspector</h3>
              </div>
              <button onClick={() => setShowDebug(false)} className="p-2 hover:bg-slate-50 rounded-lg text-slate-400">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {!runId ? (
                <div className="h-full flex flex-col items-center justify-center text-slate-300 text-center p-8">
                  <Layers size={32} className="mb-4 opacity-20" />
                  <p className="text-xs font-bold">{t("no_run_yet")}</p>
                </div>
              ) : !steps ? (
                <div className="space-y-3">
                  <div className="text-xs text-slate-500">
                    run_id: <span className="font-mono">{runId}</span>
                  </div>
                  <button
                    onClick={loadTrace}
                    className="w-full py-3 rounded-xl bg-slate-900 text-white text-xs font-bold hover:bg-slate-800 transition"
                  >
                    {t("load_trace_steps")}
                  </button>
                  <div className="text-[11px] text-slate-400">{t("trace_tip")}</div>
                </div>
              ) : steps.length === 0 ? (
                <div className="text-xs text-slate-400">{t("trace_empty")}</div>
              ) : (
                <div className="space-y-4">
                  <div className="text-xs text-slate-500">
                    run_id: <span className="font-mono">{runId}</span>
                  </div>

                  {steps.map((step, idx) => (
                    <div key={idx} className="relative pl-6 border-l-2 border-slate-100 pb-4 last:pb-0">
                      <div className="absolute -left-[9px] top-1 w-4 h-4 rounded-full bg-white border-2 border-slate-900" />
                      <div className="bg-slate-50 rounded-2xl p-4 border border-slate-200/60 hover:shadow-md transition-shadow">
                        <div className="flex justify-between items-start mb-2 gap-2">
                          <span className="font-bold text-xs text-slate-900">
                            {stepLabel(step?.name || step?.step_type)}
                          </span>
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${statusPill(step?.status)}`}>
                            {String(step?.status || "ok").toUpperCase()}
                          </span>
                        </div>

                        <div className="grid grid-cols-2 gap-3 text-[11px]">
                          <div className="text-slate-500">
                            <span className="block font-semibold uppercase text-[9px] mb-1">{t("input")}</span>
                            <div className="bg-white p-2 rounded border border-slate-200/60 truncate">
                              {JSON.stringify(step?.input_json ?? step?.input ?? {}, null, 0)}
                            </div>
                          </div>
                          <div className="text-slate-500">
                            <span className="block font-semibold uppercase text-[9px] mb-1">{t("output")}</span>
                            <div className="bg-white p-2 rounded border border-slate-200/60 truncate">
                              {JSON.stringify(step?.output_json ?? step?.output ?? {}, null, 0)}
                            </div>
                          </div>
                        </div>

                        {step?.created_at && (
                          <div className="mt-2 text-[10px] text-slate-400 font-mono">{step.created_at}</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        <style>{`
          .custom-scrollbar::-webkit-scrollbar { width: 4px; }
          .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
          .custom-scrollbar::-webkit-scrollbar-thumb { background: #E2E8F0; border-radius: 10px; }
          .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #CBD5E1; }
        `}</style>
      </main>
    </div>
  );

}
