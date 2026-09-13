import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, getAdminToken, setAdminToken, type IdentityOwner, type PlatformSnap, type PublicConfig } from "./api";

type Page = "overview" | "model" | "platforms" | "identity" | "persona" | "sessions";

const NAV: Array<{ id: Page; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "model", label: "模型与 Agent" },
  { id: "platforms", label: "平台" },
  { id: "identity", label: "身份" },
  { id: "persona", label: "人格" },
  { id: "sessions", label: "会话" },
];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [platforms, setPlatforms] = useState<PlatformSnap[]>([]);
  const [health, setHealth] = useState<{
    provider: string;
    database: string;
    db_ok: boolean;
    redis?: string;
    redis_ok?: boolean;
  } | null>(null);
  const [error, setError] = useState("");
  const [tokenDraft, setTokenDraft] = useState(getAdminToken());
  const [needsAuth, setNeedsAuth] = useState(false);

  async function refresh() {
    const [cfg, hp] = await Promise.all([api.config(), api.health()]);
    setConfig(cfg.config);
    setPlatforms(cfg.platforms);
    setHealth({
      provider: hp.provider,
      database: hp.database,
      db_ok: hp.db_ok,
      redis: hp.redis,
      redis_ok: hp.redis_ok,
    });
    setNeedsAuth(false);
    setError("");
  }

  useEffect(() => {
    refresh().catch((err: Error) => {
      const msg = err.message || "";
      if (msg.includes("401") || msg.toLowerCase().includes("admin token")) {
        setNeedsAuth(true);
        setError("需要管理口令才能打开控制台。");
      } else {
        setError(msg);
      }
    });
  }, []);

  function saveToken(event: FormEvent) {
    event.preventDefault();
    setAdminToken(tokenDraft);
    refresh().catch((err: Error) => setError(err.message));
  }

  return (
    <div className="shell">
      <aside className="rail">
        <p className="brand">
          Luo<span>pita</span>
        </p>
        <p className="tagline">机器人控制台</p>
        <nav className="nav">
          {NAV.map((item) => (
            <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
      </aside>
      <main className="main">
        {error ? <p className="flash bad">{error}</p> : null}
        {needsAuth ? (
          <section>
            <h1>管理口令</h1>
            <p className="lead">服务端已开启 `LUOPITA_ADMIN_TOKEN`，请填写后进入控制台。</p>
            <form className="stack" onSubmit={saveToken}>
              <label>
                Admin Token
                <input
                  type="password"
                  value={tokenDraft}
                  onChange={(e) => setTokenDraft(e.target.value)}
                  placeholder="Bearer token"
                  autoComplete="current-password"
                />
              </label>
              <button type="submit">进入</button>
            </form>
          </section>
        ) : null}
        {!needsAuth && page === "overview" && <Overview config={config} platforms={platforms} health={health} />}
        {!needsAuth && page === "model" && config && <ModelForm config={config} onSaved={refresh} />}
        {!needsAuth && page === "platforms" && config && (
          <PlatformsForm config={config} platforms={platforms} onSaved={refresh} />
        )}
        {!needsAuth && page === "identity" && config && <IdentityForm config={config} onSaved={refresh} />}
        {!needsAuth && page === "persona" && config && <PersonaForm config={config} onSaved={refresh} />}
        {!needsAuth && page === "sessions" && <SessionsPanel />}
      </main>
    </div>
  );
}

function Overview({
  config,
  platforms,
  health,
}: {
  config: PublicConfig | null;
  platforms: PlatformSnap[];
  health: { provider: string; database: string; db_ok: boolean; redis?: string; redis_ok?: boolean } | null;
}) {
  if (!config || !health) {
    return <p className="lead">正在读取运行状态…</p>;
  }
  return (
    <section>
      <h1>总览</h1>
      <p className="lead">适配器、模型与数据库都从这一页看现状。</p>
      <div className="grid">
        <article className="card">
          <h2>运行时</h2>
          <p>provider: {health.provider}</p>
          <p>
            database: {health.database} ({health.db_ok ? "ok" : "down"})
          </p>
          <p>
            redis: {health.redis || "-"} ({health.redis_ok ? "ok" : "down"})
          </p>
          <p>admin auth: {config.admin_auth_required ? "on" : "off"}</p>
        </article>
        <article className="card">
          <h2>平台</h2>
          <ul>
            {platforms.map((p) => (
              <li key={p.name}>
                {p.name}: {p.enabled ? "开" : "关"}
              </li>
            ))}
          </ul>
        </article>
      </div>
    </section>
  );
}

function ModelForm({ config, onSaved }: { config: PublicConfig; onSaved: () => Promise<void> }) {
  const [provider, setProvider] = useState(config.llm.provider);
  const [model, setModel] = useState(config.llm.model);
  const [visionModel, setVisionModel] = useState(config.llm.vision_model || "deepseek-flash");
  const [baseUrl, setBaseUrl] = useState(config.llm.base_url);
  const [apiKey, setApiKey] = useState("");
  const [timeout, setTimeoutSec] = useState(config.agent.timeout_sec);
  const [workdir, setWorkdir] = useState(config.agent.workdir);
  const [allow, setAllow] = useState(config.agent.command_allowlist.join("\n"));
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await api.saveConfig({
        llm: { provider, model, vision_model: visionModel, base_url: baseUrl, api_key: apiKey },
        agent: {
          timeout_sec: Number(timeout),
          workdir,
          command_allowlist: allow
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
      });
      setApiKey("");
      await onSaved();
      setMsg("已保存");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>模型与 Agent</h1>
      <p className="lead">密钥只写不回显。留空表示保持现有值。</p>
      <form onSubmit={submit}>
        <label>
          Provider
          <input value={provider} onChange={(e) => setProvider(e.target.value)} />
        </label>
        <label>
          Model
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
        <label>
          Vision Model
          <input value={visionModel} onChange={(e) => setVisionModel(e.target.value)} placeholder="deepseek-flash" />
        </label>
        <label>
          Base URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </label>
        <label>
          API Key（当前 {config.llm.api_key || "未设置"}）
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="新密钥" />
        </label>
        <label>
          命令超时（秒）
          <input type="number" value={timeout} onChange={(e) => setTimeoutSec(Number(e.target.value))} />
        </label>
        <label>
          工作目录
          <input value={workdir} onChange={(e) => setWorkdir(e.target.value)} />
        </label>
        <label>
          命令白名单（一行一个）
          <textarea value={allow} onChange={(e) => setAllow(e.target.value)} />
        </label>
        <div className="row">
          <button className="btn" disabled={busy} type="submit">
            保存
          </button>
          {msg ? <span className={msg === "已保存" ? "flash" : "flash bad"}>{msg}</span> : null}
        </div>
      </form>
    </section>
  );
}

function PlatformsForm({
  config,
  platforms,
  onSaved,
}: {
  config: PublicConfig;
  platforms: PlatformSnap[];
  onSaved: () => Promise<void>;
}) {
  const [botUrl, setBotUrl] = useState(config.napcat.bot_url);
  const [botId, setBotId] = useState(config.napcat.bot_id);
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState("");

  const napcatOn = platforms.find((p) => p.name === "napcat")?.enabled ?? config.napcat.enabled;
  const tuiOn = platforms.find((p) => p.name === "tui")?.enabled ?? config.tui.enabled;

  async function save(e: FormEvent) {
    e.preventDefault();
    await api.saveConfig({
      napcat: { bot_url: botUrl, bot_id: botId, access_token: token },
    });
    setToken("");
    await onSaved();
    setMsg("已保存 NapCat 连接信息");
  }

  return (
    <section>
      <h1>平台</h1>
      <p className="lead">NapCat 用 HTTP 上报到 /webhooks/napcat。TUI 连接 ws://host:port/ws/tui。</p>
      <div className="row" style={{ marginBottom: 18 }}>
        <span className="pill">
          <span className={napcatOn ? "dot on" : "dot"} />
          napcat
        </span>
        <button className="btn ghost" type="button" onClick={() => api.togglePlatform("napcat").then(onSaved)}>
          {napcatOn ? "关闭 NapCat" : "启用 NapCat"}
        </button>
        <span className="pill">
          <span className={tuiOn ? "dot on" : "dot"} />
          tui
        </span>
        <button className="btn ghost" type="button" onClick={() => api.togglePlatform("tui").then(onSaved)}>
          {tuiOn ? "关闭 TUI" : "启用 TUI"}
        </button>
      </div>
      <form onSubmit={save}>
        <label>
          NapCat BOT URL
          <input value={botUrl} onChange={(e) => setBotUrl(e.target.value)} />
        </label>
        <label>
          Bot ID
          <input value={botId} onChange={(e) => setBotId(e.target.value)} />
        </label>
        <label>
          Access Token（当前 {config.napcat.access_token || "未设置"}）
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="新 token" />
        </label>
        <div className="row">
          <button className="btn" type="submit">
            保存
          </button>
          {msg ? <span className="flash">{msg}</span> : null}
        </div>
      </form>
      <article className="card" style={{ marginTop: 20 }}>
        <h3>TUI 连接</h3>
        <p className="mono">uv run python tui/ui.py</p>
        <p className="lead">默认 WebSocket：ws://127.0.0.1:5170/ws/tui?chat_id=local</p>
      </article>
    </section>
  );
}

function IdentityForm({ config, onSaved }: { config: PublicConfig; onSaved: () => Promise<void> }) {
  const initial = config.identity ?? { owners: [], group_require_at: true };
  const [owners, setOwners] = useState<IdentityOwner[]>(
    initial.owners.length ? initial.owners : [{ platform: "napcat", user_id: "", nickname: "" }],
  );
  const [requireAt, setRequireAt] = useState(initial.group_require_at);
  const [keywords, setKeywords] = useState((initial.wake_keywords || ["小lu", "luopita"]).join("\n"));
  const [techChance, setTechChance] = useState(Math.round((initial.group_tech_chance ?? 0.35) * 100));
  const [chattyChance, setChattyChance] = useState(Math.round((initial.group_chatty_chance ?? 0.22) * 100));
  const [cooldown, setCooldown] = useState(initial.group_chime_cooldown_sec ?? 35);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  function updateOwner(index: number, patch: Partial<IdentityOwner>) {
    setOwners((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function isBuiltin(owner: IdentityOwner) {
    return (
      (owner.platform === "admin" && owner.user_id === "admin") ||
      (owner.platform === "tui" && owner.user_id === "tui")
    );
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await api.saveIdentity({
        owners: owners.filter((row) => row.user_id.trim()),
        group_require_at: requireAt,
        wake_keywords: keywords
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        group_tech_chance: Math.max(0, Math.min(100, Number(techChance))) / 100,
        group_chatty_chance: Math.max(0, Math.min(100, Number(chattyChance))) / 100,
        group_chime_cooldown_sec: Number(cooldown) || 0,
      });
      await onSaved();
      setMsg("已保存");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>身份</h1>
      <p className="lead">QQ 用号码认定，不认群名片。控制台和 TUI 会始终保留为主人，避免把自己锁在门外。</p>
      <form onSubmit={save}>
        <label className="row" style={{ maxWidth: "none" }}>
          <input type="checkbox" checked={requireAt} onChange={(e) => setRequireAt(e.target.checked)} />
          群聊没 @、没叫到名字就随机插话（主人也一样）；私聊、被 @ 或叫到唤醒名必回
        </label>
        <label>
          唤醒名字（一行一个，提到就必回，和 @ 一样）
          <textarea value={keywords} onChange={(e) => setKeywords(e.target.value)} />
        </label>
        <div className="row owner-row">
          <label>
            技术问题插话概率（%）
            <input type="number" min={0} max={100} value={techChance} onChange={(e) => setTechChance(Number(e.target.value))} />
          </label>
          <label>
            闲聊搭话概率（%）
            <input type="number" min={0} max={100} value={chattyChance} onChange={(e) => setChattyChance(Number(e.target.value))} />
          </label>
          <label>
            搭话冷却（秒）
            <input type="number" min={0} value={cooldown} onChange={(e) => setCooldown(Number(e.target.value))} />
          </label>
        </div>
        {owners.map((owner, index) => (
          <div className="row owner-row" key={`${owner.platform}-${index}`}>
            <label>
              平台
              <select
                value={owner.platform}
                disabled={isBuiltin(owner)}
                onChange={(e) => updateOwner(index, { platform: e.target.value })}
              >
                <option value="napcat">napcat</option>
                <option value="admin">admin</option>
                <option value="tui">tui</option>
              </select>
            </label>
            <label>
              用户 ID / QQ 号
              <input
                value={owner.user_id}
                disabled={isBuiltin(owner)}
                onChange={(e) => updateOwner(index, { user_id: e.target.value })}
              />
            </label>
            <label>
              称呼
              <input value={owner.nickname} onChange={(e) => updateOwner(index, { nickname: e.target.value })} />
            </label>
            <button
              className="btn ghost"
              type="button"
              disabled={isBuiltin(owner)}
              onClick={() => setOwners((rows) => rows.filter((_, i) => i !== index))}
            >
              删除
            </button>
          </div>
        ))}
        <div className="row">
          <button
            className="btn ghost"
            type="button"
            onClick={() => setOwners((rows) => [...rows, { platform: "napcat", user_id: "", nickname: "" }])}
          >
            添加主人
          </button>
          <button className="btn" disabled={busy} type="submit">
            保存
          </button>
          {msg ? <span className={msg === "已保存" ? "flash" : "flash bad"}>{msg}</span> : null}
        </div>
      </form>
    </section>
  );
}

function PersonaForm({ config, onSaved }: { config: PublicConfig; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(config.persona.name);
  const [ownerAddress, setOwnerAddress] = useState(config.persona.owner_address || "");
  const [voice, setVoice] = useState(config.persona.voice || "");
  const [taboos, setTaboos] = useState(config.persona.taboos || "");
  const [relationship, setRelationship] = useState(config.persona.relationship || "");
  const [prompt, setPrompt] = useState(config.persona.system_prompt);
  const [msg, setMsg] = useState("");

  async function save(e: FormEvent) {
    e.preventDefault();
    await api.savePersona({
      name,
      owner_address: ownerAddress,
      voice,
      taboos,
      relationship,
      system_prompt: prompt,
    });
    await onSaved();
    setMsg("人格已更新");
  }

  return (
    <section>
      <h1>人格</h1>
      <p className="lead">角色卡会按主人 / 路人切换语气。关系由你填写，不要让模型自己编身世。</p>
      <form onSubmit={save}>
        <label>
          名称
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          对主人怎么叫
          <input value={ownerAddress} onChange={(e) => setOwnerAddress(e.target.value)} placeholder="例如：rovina" />
        </label>
        <label>
          说话方式
          <textarea value={voice} onChange={(e) => setVoice(e.target.value)} />
        </label>
        <label>
          忌讳
          <textarea value={taboos} onChange={(e) => setTaboos(e.target.value)} />
        </label>
        <label>
          和主人的关系
          <textarea value={relationship} onChange={(e) => setRelationship(e.target.value)} />
        </label>
        <label>
          额外说明（可选）
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <div className="row">
          <button className="btn" type="submit">
            保存
          </button>
          {msg ? <span className="flash">{msg}</span> : null}
        </div>
      </form>
    </section>
  );
}

function SessionsPanel() {
  const [sessions, setSessions] = useState<Array<Record<string, unknown>>>([]);
  const [active, setActive] = useState<string>("");
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const data = await api.sessions();
    setSessions(data.sessions);
  };

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function openSession(id: string) {
    setActive(id);
    const data = await api.messages(id);
    setMessages(data.messages);
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    setBusy(true);
    try {
      const data = await api.chat(draft.trim(), active || undefined);
      setDraft("");
      setActive(data.session_id);
      await openSession(data.session_id);
      await load();
    } finally {
      setBusy(false);
    }
  }

  const list = useMemo(() => sessions, [sessions]);

  return (
    <section>
      <h1>会话</h1>
      <p className="lead">查看历史，或在这里发一条测试消息。</p>
      <div className="grid">
        <article className="card">
          <h3>会话列表</h3>
          {list.length === 0 ? <p className="lead">还没有会话</p> : null}
          {list.map((item) => {
            const id = String(item.session_id);
            return (
              <button key={id} className="session" type="button" onClick={() => openSession(id)}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div className="mono">{id}</div>
                  <span className={`pill ${item.role === "owner" ? "owner" : ""}`}>
                    {item.role === "owner" ? "主人" : "用户"}
                  </span>
                </div>
                <div className="lead">{String(item.preview || "")}</div>
              </button>
            );
          })}
        </article>
        <article className="card">
          <h3>对话</h3>
          <div className="messages">
            {messages.map((msg, idx) => (
              <div key={`${msg.role}-${idx}`} className={`bubble ${msg.role}`}>
                {msg.content}
              </div>
            ))}
          </div>
          <form onSubmit={send}>
            <label>
              测试消息
              <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="对机器人说一句" />
            </label>
            <button className="btn" disabled={busy} type="submit">
              发送
            </button>
          </form>
        </article>
      </div>
    </section>
  );
}
