import { useState, useCallback, useRef, useEffect } from "react";

const REQUIRED_SLOTS = ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"];

const SLOT_LABELS = {
  game_type: "游戏类型",
  core_mechanic: "核心机制",
  theme: "主题风格",
  input_method: "操作方式",
  win_condition: "胜利条件",
  difficulty: "难度设定",
};

const SIMULATED_QUESTIONS = [
  { slotKey: "core_mechanic", prompt: "你希望游戏的核心玩法是什么？比如跳跃躲避、收集物品、还是战斗闯关？", label: "核心机制", skippable: true },
  { slotKey: "input_method", prompt: "玩家用什么方式操作？触屏滑动、虚拟按钮、还是重力感应？", label: "操作方式", skippable: true },
  { slotKey: "win_condition", prompt: "怎样算赢？跑到终点、得分最高、还是存活最久？", label: "胜利条件", skippable: true },
  { slotKey: "difficulty", prompt: "难度偏好？轻松休闲、适中挑战、还是硬核？", label: "难度设定", skippable: true },
];

const SIMULATED_ANSWERS = {
  core_mechanic: "跳跃躲避障碍物",
  input_method: "触屏左右滑动 + 点击跳跃",
  win_condition: "跑到终点，收集星星加分",
  difficulty: "适中，逐关递增",
};

function Badge({ children, color }) {
  const colors = {
    blue: "background:#dbeafe;color:#1e40af",
    green: "background:#dcfce7;color:#166534",
    yellow: "background:#fef9c3;color:#854d0e",
    red: "background:#fee2e2;color:#991b1b",
    gray: "background:#f3f4f6;color:#4b5563",
    purple: "background:#f3e8ff;color:#6b21a8",
    orange: "background:#ffedd5;color:#9a3412",
  };
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 12,
      fontSize: 11, fontWeight: 600, ...parseStyle(colors[color] || colors.gray)
    }}>
      {children}
    </span>
  );
}

function parseStyle(str) {
  const obj = {};
  str.split(";").forEach(p => {
    const [k, v] = p.split(":");
    if (k && v) obj[k.trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v.trim();
  });
  return obj;
}

function StatusBadge({ status }) {
  const map = { collecting: "yellow", ready: "green", generating: "purple", completed: "blue", abandoned: "gray" };
  const labelMap = { collecting: "收集中", ready: "就绪", generating: "生成中", completed: "已完成", abandoned: "已放弃" };
  return <Badge color={map[status] || "gray"}>{labelMap[status] || status}</Badge>;
}

function SlotGrid({ slots, missing }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
      {REQUIRED_SLOTS.map((key) => {
        const filled = slots[key];
        const isMissing = missing.includes(key);
        const bg = filled ? "#f0fdf4" : isMissing ? "#fef2f2" : "#f9fafb";
        const border = filled ? "#bbf7d0" : isMissing ? "#fecaca" : "#e5e7eb";
        return (
          <div key={key} style={{ borderRadius: 6, padding: "6px 8px", fontSize: 11, border: "1px solid " + border, background: bg }}>
            <div style={{ fontWeight: 600, color: "#374151" }}>{SLOT_LABELS[key]}</div>
            <div style={{ marginTop: 2, color: filled ? "#15803d" : "#9ca3af", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {filled ? String(filled) : "未填充"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProgressBar({ pct }) {
  const color = pct >= 0.67 ? "#22c55e" : "#f59e0b";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 8, background: "#e5e7eb", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ height: "100%", borderRadius: 4, background: color, width: Math.round(pct * 100) + "%", transition: "width 0.5s" }} />
      </div>
      <span style={{ fontSize: 11, fontFamily: "monospace", color: "#6b7280", width: 36, textAlign: "right" }}>{Math.round(pct * 100)}%</span>
    </div>
  );
}

function LogEntry({ entry }) {
  const iconMap = { api: "🌐", state: "🔄", ai: "🤖", user: "👤", error: "❌", success: "✅", info: "ℹ️" };
  const bgMap = {
    api: "#eff6ff", state: "#fefce8", ai: "#faf5ff", user: "#f0fdf4",
    error: "#fef2f2", success: "#ecfdf5", info: "#f9fafb",
  };
  return (
    <div style={{ borderRadius: 6, padding: "8px 10px", fontSize: 11, background: bgMap[entry.type] || "#f9fafb", border: "1px solid #e5e7eb", marginBottom: 6 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
        <span>{iconMap[entry.type] || "•"}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, color: "#1f2937" }}>{entry.title}</div>
          {entry.detail && <div style={{ color: "#6b7280", marginTop: 2, whiteSpace: "pre-wrap" }}>{entry.detail}</div>}
          {entry.code && (
            <pre style={{ marginTop: 4, padding: 6, background: "#fff", borderRadius: 4, color: "#4b5563", overflow: "auto", fontFamily: "monospace", fontSize: 10 }}>
              {entry.code}
            </pre>
          )}
        </div>
        <span style={{ color: "#9ca3af", fontFamily: "monospace", whiteSpace: "nowrap", fontSize: 10 }}>{entry.time}</span>
      </div>
    </div>
  );
}

function ChatBubble({ role, content, kind }) {
  const isUser = role === "user";
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", marginBottom: 6 }}>
      <div style={{
        maxWidth: "80%", borderRadius: 12, padding: "8px 12px", fontSize: 13,
        background: isUser ? "#3b82f6" : "#f3f4f6",
        color: isUser ? "#fff" : "#1f2937",
      }}>
        {kind && <div style={{ fontSize: 10, marginBottom: 2, opacity: 0.6 }}>{kind}</div>}
        {content}
      </div>
    </div>
  );
}

function Btn({ onClick, disabled, bg, children }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "6px 14px", fontSize: 12, fontWeight: 600, borderRadius: 8,
        border: "none", color: "#fff", background: disabled ? "#d1d5db" : bg,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.6 : 1,
      }}
    >
      {children}
    </button>
  );
}

export default function CreationSessionSimulator() {
  const [session, setSession] = useState(null);
  const [logs, setLogs] = useState([]);
  const [running, setRunning] = useState(false);
  const [conversation, setConversation] = useState([]);
  const [speed, setSpeed] = useState(1);
  const logEndRef = useRef(null);
  const chatEndRef = useRef(null);

  useEffect(() => {
    if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: "smooth" });
  }, [logs]);
  useEffect(() => {
    if (chatEndRef.current) chatEndRef.current.scrollIntoView({ behavior: "smooth" });
  }, [conversation]);

  const ts = () => {
    const d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, "0")).join(":");
  };

  const addLog = (entry) => setLogs(prev => [...prev, { ...entry, time: ts() }]);
  const wait = (ms) => new Promise(r => setTimeout(r, ms / speed));

  const doCreate = async () => {
    setRunning(true);
    setLogs([]);
    setConversation([]);

    addLog({ type: "user", title: "用户提交 Prompt", detail: "\"做一个像 Temple Run 的跑酷游戏\"" });
    await wait(400);

    addLog({ type: "api", title: "POST /creation-sessions", detail: "prompt: 做一个像 Temple Run 的跑酷游戏\nentryMode: create | generationTier: standard" });
    await wait(300);

    addLog({ type: "state", title: "Auto-abandon 旧会话 (Bug 2 修复)", detail: "1. abandon status IN ('collecting','ready')\n2. abandon status='generating' AND updatedAt < 10min ago" });
    await wait(400);

    addLog({ type: "ai", title: "→ AI Engine: analyze-turn (带重试, Bug 1 修复)", detail: "首轮分析，解析 prompt 提取 slot 信息\n若超时/5xx → 自动重试一次(500ms间隔)" });
    await wait(600);

    const initialSlots = { game_type: "跑酷 / endless runner", theme: "Temple Run 风格冒险" };
    const missing = ["core_mechanic", "input_method", "win_condition", "difficulty"];
    const fillPct = 2 / 6;

    addLog({
      type: "ai", title: "← AI Engine 返回",
      code: JSON.stringify({
        ready_to_generate: false,
        slot_fill_pct: 0.33,
        slots_updated: ["game_type", "theme"],
        missing_required: missing,
        current_question: { slot_key: "core_mechanic" }
      }, null, 2),
    });
    await wait(300);

    const newSession = {
      id: "cs_" + Math.random().toString(36).slice(2, 8),
      status: "collecting",
      revision: 1,
      slotState: initialSlots,
      missingRequired: missing,
      slotFillPct: fillPct,
      readyToGenerate: false,
      currentQuestion: SIMULATED_QUESTIONS[0],
      questionIndex: 0,
    };

    addLog({ type: "success", title: "会话创建成功", detail: "ID: " + newSession.id + " | status: collecting | fillPct: 33%" });

    setConversation([
      { role: "user", content: "做一个像 Temple Run 的跑酷游戏", kind: "prompt" },
      { role: "assistant", content: "好的！我来帮你设计一个 Temple Run 风格的跑酷游戏。已识别: 游戏类型=跑酷, 主题=冒险。\n\n" + SIMULATED_QUESTIONS[0].prompt, kind: "question" },
    ]);
    setSession(newSession);
    setRunning(false);
  };

  const doAnswer = async () => {
    if (!session || running || !session.currentQuestion) return;
    setRunning(true);

    const qi = session.questionIndex;
    const q = SIMULATED_QUESTIONS[qi];
    const answer = SIMULATED_ANSWERS[q.slotKey];

    addLog({ type: "user", title: "用户回答: " + SLOT_LABELS[q.slotKey], detail: "\"" + answer + "\"" });
    setConversation(prev => [...prev, { role: "user", content: answer, kind: "answer" }]);
    await wait(300);

    addLog({ type: "api", title: "POST /sessions/" + session.id + "/messages", detail: "revision: " + session.revision });
    await wait(200);

    addLog({ type: "ai", title: "→ AI Engine: analyze-turn (第" + (qi + 2) + "轮)" });
    await wait(500);

    const newSlots = { ...session.slotState, [q.slotKey]: answer };
    const filled = REQUIRED_SLOTS.filter(k => newSlots[k]).length;
    const newFillPct = filled / REQUIRED_SLOTS.length;
    const newMissing = REQUIRED_SLOTS.filter(k => !newSlots[k]);
    const aiReady = newFillPct >= 0.67;
    const nextQI = qi + 1;
    const hasNext = nextQI < SIMULATED_QUESTIONS.length;

    const prevReady = session.status === "ready";
    const nextStatus = aiReady ? "ready" : (prevReady ? "ready" : "collecting");
    const nextReady = aiReady || prevReady;

    addLog({
      type: "ai", title: "← AI Engine 返回",
      code: JSON.stringify({
        ready_to_generate: aiReady,
        slot_fill_pct: Number(newFillPct.toFixed(2)),
        slots_updated: [q.slotKey],
        missing_required: newMissing,
      }, null, 2),
    });
    await wait(200);

    if (prevReady && !aiReady) {
      addLog({ type: "info", title: "🔒 Bug 4: 单向锁定生效", detail: "AI 返回 ready=false, 但已是 ready → 保持 ready 不回退" });
      await wait(200);
    }

    addLog({ type: "state", title: "状态: " + session.status + " → " + nextStatus, detail: "fillPct: " + Math.round(newFillPct * 100) + "% | readyToGenerate: " + nextReady });

    const effectiveQ = nextStatus === "ready" ? null : (hasNext ? SIMULATED_QUESTIONS[nextQI] : null);

    if (nextStatus === "ready") {
      await wait(200);
      addLog({ type: "info", title: "🧹 Bug 3: currentQuestion 清除", detail: "status=ready → toSnapshot() 返回 currentQuestion=null" });
    }

    const replyText = aiReady
      ? "收到！" + SLOT_LABELS[q.slotKey] + "已记录。信息足够了，可以点击生成！"
      : "好的，" + SLOT_LABELS[q.slotKey] + "已记录。" + (hasNext ? "\n\n" + SIMULATED_QUESTIONS[nextQI].prompt : "");

    setConversation(prev => [...prev, { role: "assistant", content: replyText, kind: "question" }]);

    setSession({
      ...session,
      status: nextStatus,
      revision: session.revision + 1,
      slotState: newSlots,
      missingRequired: newMissing,
      slotFillPct: newFillPct,
      readyToGenerate: nextReady,
      currentQuestion: effectiveQ,
      questionIndex: nextQI,
    });
    setRunning(false);
  };

  const doSkip = async () => {
    if (!session || running || !session.currentQuestion) return;
    setRunning(true);
    const q = session.currentQuestion;
    const qi = session.questionIndex;

    addLog({ type: "user", title: "跳过问题: " + q.label });
    await wait(300);
    addLog({ type: "api", title: "POST /sessions/" + session.id + "/skip" });
    await wait(200);
    addLog({ type: "ai", title: "→ AI Engine: analyze-turn (advance_only=true)" });
    await wait(400);

    const nextQI = qi + 1;
    const hasNext = nextQI < SIMULATED_QUESTIONS.length;
    const nextQ = hasNext ? SIMULATED_QUESTIONS[nextQI] : null;
    const nextStatus = !hasNext ? "ready" : session.status;
    const effectiveQ = nextStatus === "ready" ? null : nextQ;

    addLog({ type: "state", title: "跳过 " + q.slotKey + " → 下一个问题", detail: hasNext ? "next: " + nextQ.slotKey : "无更多问题 → ready" });

    setConversation(prev => [
      ...prev,
      { role: "user", content: "[跳过: " + q.label + "]", kind: "skip" },
      { role: "assistant", content: nextQ ? "没问题。" + nextQ.prompt : "好的，可以生成了。", kind: "question" },
    ]);

    setSession(prev => ({
      ...prev,
      revision: prev.revision + 1,
      currentQuestion: effectiveQ,
      questionIndex: nextQI,
      status: nextStatus,
      readyToGenerate: nextStatus === "ready" || prev.readyToGenerate,
    }));
    setRunning(false);
  };

  const doGenerate = async () => {
    if (!session || running) return;
    setRunning(true);

    addLog({ type: "user", title: "用户点击: 生成游戏" });
    await wait(300);
    addLog({ type: "api", title: "POST /sessions/" + session.id + "/generate" });
    await wait(200);
    addLog({ type: "ai", title: "→ AI Engine: spec-from-slots", detail: "将 slot 编译为完整 spec" });
    await wait(500);
    addLog({ type: "state", title: "CAS 锁定: → generating", detail: "UPDATE status='generating' WHERE revision=" + session.revision });
    setSession(prev => ({ ...prev, status: "generating" }));
    await wait(400);
    addLog({ type: "api", title: "→ gameService.create()", detail: "提交异步生成任务" });
    await wait(600);
    addLog({ type: "success", title: "← 生成任务已排队", code: JSON.stringify({ gameId: "game_abc123", taskId: "task_xyz789" }, null, 2) });
    await wait(300);
    addLog({ type: "info", title: "🔧 Bug 5: status → completed", detail: "生成任务已提交，session 职责完成\n不再占用 active slot，可立即创建新会话" });

    setSession(prev => ({
      ...prev,
      status: "completed",
      generatedGameId: "game_abc123",
      generationTaskId: "task_xyz789",
      currentQuestion: null,
    }));
    setRunning(false);
  };

  const doSimulateTimeout = async () => {
    setRunning(true);
    addLog({ type: "user", title: "🧪 模拟: AI Engine 超时" });
    await wait(300);
    addLog({ type: "api", title: "POST /creation-sessions", detail: "prompt: 测试超时" });
    await wait(300);
    addLog({ type: "error", title: "← ECONNABORTED (超时 30s)" });
    await wait(300);
    addLog({ type: "info", title: "🔧 Bug 1: 错误分类", detail: "旧: throw BadRequestException(400) ← 误导前端\n新: ECONNABORTED → ServiceUnavailableException(503)" });
    await wait(300);
    addLog({ type: "info", title: "🔄 自动重试 (等待 500ms)" });
    await wait(500);
    addLog({ type: "error", title: "← 重试仍超时", detail: "throw ServiceUnavailableException(503)\n前端可展示: 服务暂时不可用" });
    setRunning(false);
  };

  const doReset = () => {
    setSession(null);
    setLogs([]);
    setConversation([]);
  };

  const canAnswer = session && session.status === "collecting" && session.currentQuestion && !running;
  const canSkip = canAnswer;
  const canGenerate = session && session.readyToGenerate && session.status !== "completed" && session.status !== "generating" && !running;

  const flowSteps = [
    { label: "collecting", active: session && session.status === "collecting" },
    { label: "ready 🔒", active: session && session.status === "ready" },
    { label: "generating", active: session && session.status === "generating" },
    { label: "completed ✅", active: session && session.status === "completed" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc", padding: 16, fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ textAlign: "center", marginBottom: 16 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: "#1e293b", margin: 0 }}>Creation Session 多轮会话模拟器</h1>
          <p style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>交互式演示完整流程 + 5 个 Bug 修复效果</p>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 8, marginBottom: 16 }}>
          <Btn onClick={doCreate} disabled={running} bg="#2563eb">1. 创建会话</Btn>
          {canAnswer && <Btn onClick={doAnswer} disabled={false} bg="#16a34a">2. 回答问题</Btn>}
          {canSkip && <Btn onClick={doSkip} disabled={false} bg="#ca8a04">⏭ 跳过</Btn>}
          {canGenerate && <Btn onClick={doGenerate} disabled={false} bg="#9333ea">3. 生成游戏</Btn>}
          <span style={{ width: 1, height: 28, background: "#d1d5db" }} />
          <Btn onClick={doSimulateTimeout} disabled={running} bg="#dc2626">⚡ 模拟超时</Btn>
          <Btn onClick={doReset} disabled={false} bg="#6b7280">🔄 重置</Btn>
          <select
            value={speed}
            onChange={e => setSpeed(Number(e.target.value))}
            style={{ padding: "4px 8px", fontSize: 12, borderRadius: 6, border: "1px solid #d1d5db" }}
          >
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
        </div>

        {running && (
          <div style={{ textAlign: "center", marginBottom: 12 }}>
            <span style={{ display: "inline-block", padding: "4px 12px", background: "#eff6ff", color: "#2563eb", borderRadius: 12, fontSize: 12 }}>
              ⏳ 处理中...
            </span>
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr 1fr", gap: 12 }}>
          {/* Left: State */}
          <div>
            <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 14, marginBottom: 10 }}>
              <h2 style={{ fontSize: 13, fontWeight: 600, color: "#475569", marginTop: 0, marginBottom: 10 }}>📊 Session 状态</h2>
              {session ? (
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12 }}>
                    <span style={{ color: "#94a3b8" }}>ID</span>
                    <span style={{ fontFamily: "monospace", color: "#475569" }}>{session.id}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12 }}>
                    <span style={{ color: "#94a3b8" }}>Status</span>
                    <StatusBadge status={session.status} />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12 }}>
                    <span style={{ color: "#94a3b8" }}>Revision</span>
                    <span style={{ fontFamily: "monospace" }}>{session.revision}</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, fontSize: 12 }}>
                    <span style={{ color: "#94a3b8" }}>readyToGenerate</span>
                    <Badge color={session.readyToGenerate ? "green" : "gray"}>{String(session.readyToGenerate)}</Badge>
                  </div>
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>Slot 填充率</div>
                    <ProgressBar pct={session.slotFillPct} />
                  </div>
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>currentQuestion</div>
                    {session.currentQuestion ? (
                      <div style={{ background: "#fefce8", border: "1px solid #fde68a", borderRadius: 6, padding: 8, fontSize: 11 }}>
                        <div style={{ fontWeight: 600, color: "#92400e" }}>{session.currentQuestion.label}</div>
                        <div style={{ color: "#a16207", marginTop: 2 }}>{session.currentQuestion.prompt}</div>
                      </div>
                    ) : (
                      <div style={{ background: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: 6, padding: 8, fontSize: 11, color: "#9ca3af" }}>
                        null {session.status === "ready" && <span style={{ color: "#16a34a" }}>(Bug 3: ready 时清除)</span>}
                      </div>
                    )}
                  </div>
                  {session.generatedGameId && (
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                      <span style={{ color: "#94a3b8" }}>Game ID</span>
                      <span style={{ fontFamily: "monospace", color: "#7c3aed" }}>{session.generatedGameId}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ textAlign: "center", color: "#9ca3af", fontSize: 13, padding: "30px 0" }}>点击 "创建会话" 开始</div>
              )}
            </div>

            {session && (
              <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 14, marginBottom: 10 }}>
                <h2 style={{ fontSize: 13, fontWeight: 600, color: "#475569", marginTop: 0, marginBottom: 10 }}>🎰 Slot 状态</h2>
                <SlotGrid slots={session.slotState} missing={session.missingRequired} />
              </div>
            )}

            <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 14 }}>
              <h2 style={{ fontSize: 13, fontWeight: 600, color: "#475569", marginTop: 0, marginBottom: 8 }}>🔧 Bug 修复</h2>
              <div style={{ fontSize: 11, lineHeight: 2 }}>
                <div><Badge color="red">1</Badge> 超时→503+重试</div>
                <div><Badge color="orange">2</Badge> generating 不占 slot</div>
                <div><Badge color="yellow">3</Badge> ready 时 question=null</div>
                <div><Badge color="green">4</Badge> ready 不回退</div>
                <div><Badge color="purple">5</Badge> 生成后→completed</div>
              </div>
            </div>
          </div>

          {/* Middle: Chat */}
          <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", display: "flex", flexDirection: "column", minHeight: 460 }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid #e2e8f0" }}>
              <h2 style={{ fontSize: 13, fontWeight: 600, color: "#475569", margin: 0 }}>💬 对话流</h2>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 10 }}>
              {conversation.length === 0 ? (
                <div style={{ textAlign: "center", color: "#9ca3af", fontSize: 13, padding: "60px 0" }}>对话将在此显示</div>
              ) : (
                conversation.map((msg, i) => <ChatBubble key={i} role={msg.role} content={msg.content} kind={msg.kind} />)
              )}
              <div ref={chatEndRef} />
            </div>
          </div>

          {/* Right: Log */}
          <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", display: "flex", flexDirection: "column", minHeight: 460 }}>
            <div style={{ padding: "10px 14px", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: 13, fontWeight: 600, color: "#475569", margin: 0 }}>📋 事件日志</h2>
              <span style={{ fontSize: 11, color: "#94a3b8" }}>{logs.length} 条</span>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 10 }}>
              {logs.length === 0 ? (
                <div style={{ textAlign: "center", color: "#9ca3af", fontSize: 13, padding: "60px 0" }}>日志将在此显示</div>
              ) : (
                logs.map((log, i) => <LogEntry key={i} entry={log} />)
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>

        {/* State flow */}
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #e2e8f0", padding: 14, marginTop: 12, textAlign: "center" }}>
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 6, flexWrap: "wrap", fontSize: 12 }}>
            {flowSteps.map((s, i) => (
              <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                {i > 0 && <span style={{ color: "#9ca3af" }}>→</span>}
                <span style={{
                  padding: "4px 12px", borderRadius: 14, fontWeight: 600, transition: "all 0.3s",
                  background: s.active ? "#3b82f6" : "#f1f5f9",
                  color: s.active ? "#fff" : "#64748b",
                }}>{s.label}</span>
              </span>
            ))}
            <span style={{ margin: "0 8px", color: "#d1d5db" }}>|</span>
            <span style={{ padding: "4px 12px", borderRadius: 14, background: "#f1f5f9", color: "#94a3b8", fontWeight: 600 }}>abandoned</span>
          </div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 8 }}>
            collecting→ready 单向(Bug4) | ready 时 question=null(Bug3) | generating→completed 自动(Bug5)
          </div>
        </div>
      </div>
    </div>
  );
}
