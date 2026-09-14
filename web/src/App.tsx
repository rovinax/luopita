import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, getAdminToken, setAdminToken, type IdentityOwner, type PlatformSnap, type PublicConfig, type SpeakerProfile, type VoiceExample } from "./api";
import { applyTheme, readTheme, type ThemeMode } from "./theme";

type Page = "overview" | "config" | "sessions" | "profiles";
type ConfigTab = "model" | "agent" | "platforms" | "identity" | "persona" | "examples" | "system";

const NAV: Array<{ id: Page; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "config", label: "配置" },
  { id: "sessions", label: "会话" },
  { id: "profiles", label: "画像" },
];

const CONFIG_TABS: Array<{ id: ConfigTab; label: string }> = [
  { id: "model", label: "模型" },
  { id: "agent", label: "Agent" },
  { id: "platforms", label: "平台" },
  { id: "identity", label: "身份" },
  { id: "persona", label: "人格" },
  { id: "examples", label: "样例" },
  { id: "system", label: "系统" },
];

function CatHead({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <path fill="currentColor" d="M7 12 4 3l9 7h6l9-7-3 9c2.8 3 4 7.2 4 11 0 6.1-4.5 11-13 11S3 29.1 3 23c0-3.8 1.2-8 4-11Z" />
      <circle cx="12.2" cy="18.5" r="1.5" fill="var(--bg)" />
      <circle cx="19.8" cy="18.5" r="1.5" fill="var(--bg)" />
    </svg>
  );
}

function SittingCat({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 40" aria-hidden="true">
      <path
        fill="currentColor"
        d="M14 16 20 4l6 12h12l6-12 6 12c6 0 8 6 8 12 0 8-8 12-28 12S6 36 6 28c0-6 2-12 8-12Z"
      />
      <circle cx="24" cy="24" r="2" fill="var(--bg)" />
      <circle cx="40" cy="24" r="2" fill="var(--bg)" />
    </svg>
  );
}

function ThemeSwitch() {
  const [mode, setMode] = useState<ThemeMode>(() => readTheme());

  useEffect(() => {
    applyTheme(mode);
  }, [mode]);

  return (
    <div className="theme-switch" role="radiogroup" aria-label="主题">
      {(
        [
          ["system", "系统"],
          ["light", "浅色"],
          ["dark", "深色"],
        ] as Array<[ThemeMode, string]>
      ).map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={mode === id}
          className={mode === id ? "active" : ""}
          onClick={() => setMode(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [configTab, setConfigTab] = useState<ConfigTab>("model");
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
      <header className="bar">
        <p className="brand">
          <CatHead />
          Luopita
        </p>
        <nav className="nav" aria-label="主导航">
          {NAV.map((item) => (
            <button
              key={item.id}
              className={page === item.id ? "active" : ""}
              aria-current={page === item.id ? "page" : undefined}
              onClick={() => setPage(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <ThemeSwitch />
      </header>
      <main className="main">
        {error ? <p className="flash bad">{error}</p> : null}
        {needsAuth ? (
          <section>
            <div className="page-head">
              <h1>管理口令</h1>
              <p className="hint">服务端已开启管理认证</p>
            </div>
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
              <button className="btn" type="submit">
                进入
              </button>
            </form>
          </section>
        ) : null}
        {!needsAuth && page === "overview" && <Overview config={config} platforms={platforms} health={health} />}
        {!needsAuth && page === "config" && config && (
          <section>
            <div className="page-head">
              <h1>配置</h1>
              <p className="hint">改完即时生效，密钥留空则保持原值</p>
            </div>
            <div className="tabs" role="tablist" aria-label="配置分组">
              {CONFIG_TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={configTab === item.id}
                  className={configTab === item.id ? "active" : ""}
                  onClick={() => setConfigTab(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {configTab === "model" && <ModelForm config={config} onSaved={refresh} />}
            {configTab === "agent" && <AgentForm config={config} onSaved={refresh} />}
            {configTab === "platforms" && <PlatformsForm config={config} platforms={platforms} onSaved={refresh} />}
            {configTab === "identity" && <IdentityForm config={config} onSaved={refresh} />}
            {configTab === "persona" && <PersonaForm config={config} onSaved={refresh} />}
            {configTab === "examples" && <ExamplesForm />}
            {configTab === "system" && <SystemForm config={config} health={health} onSaved={refresh} />}
          </section>
        )}
        {!needsAuth && page === "sessions" && <SessionsPanel />}
        {!needsAuth && page === "profiles" && <ProfilesPanel />}
      </main>
    </div>
  );
}

function SaveRow({ busy, msg }: { busy?: boolean; msg: string }) {
  return (
    <div className="row">
      <button className="btn" disabled={busy} type="submit">
        保存
      </button>
      {msg ? <span className={msg.startsWith("已") ? "flash" : "flash bad"}>{msg}</span> : null}
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
    return <p className="hint">正在读取运行状态…</p>;
  }
  const ident = config.identity;
  return (
    <section>
      <div className="page-head">
        <h1>总览</h1>
        <p className="hint">当前运行快照</p>
      </div>
      <div className="grid">
        <article className="card">
          <h2>运行时</h2>
          <div className="stat">
            <span>模型</span>
            <span>
              {health.provider} / {config.llm.model}
            </span>
          </div>
          <div className="stat">
            <span>数据库</span>
            <span>
              {health.database} · {health.db_ok ? "正常" : "异常"}
            </span>
          </div>
          <div className="stat">
            <span>Redis</span>
            <span>
              {health.redis || "-"} · {health.redis_ok ? "正常" : "异常"}
            </span>
          </div>
          <div className="stat">
            <span>管理认证</span>
            <span>{config.admin_auth_required ? "已开启" : "未开启"}</span>
          </div>
        </article>
        <article className="card">
          <h2>平台</h2>
          {platforms.map((p) => (
            <div className="stat" key={p.name}>
              <span>{p.name}</span>
              <span>{p.enabled ? "开" : "关"}</span>
            </div>
          ))}
        </article>
        <article className="card">
          <h2>群聊</h2>
          <div className="stat">
            <span>点名才回</span>
            <span>{ident?.group_require_at ? "是" : "否"}</span>
          </div>
          <div className="stat">
            <span>接话窗口</span>
            <span>{ident?.group_engage_sec ?? 90} 秒</span>
          </div>
          <div className="stat">
            <span>短期上下文</span>
            <span>{config.agent.short_term_messages ?? 16} 条</span>
          </div>
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
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await api.saveConfig({
        llm: { provider, model, vision_model: visionModel, base_url: baseUrl, api_key: apiKey },
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
    <form onSubmit={submit}>
      <label>
        Provider
        <input value={provider} onChange={(e) => setProvider(e.target.value)} />
      </label>
      <label>
        对话模型
        <input value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <label>
        视觉模型
        <input value={visionModel} onChange={(e) => setVisionModel(e.target.value)} placeholder="deepseek-flash" />
      </label>
      <label>
        Base URL
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </label>
      <label>
        API Key
        <span className="note">当前 {config.llm.api_key || "未设置"}，留空不改</span>
        <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="新密钥" />
      </label>
      <SaveRow busy={busy} msg={msg} />
    </form>
  );
}

function AgentForm({ config, onSaved }: { config: PublicConfig; onSaved: () => Promise<void> }) {
  const [timeout, setTimeoutSec] = useState(config.agent.timeout_sec);
  const [workdir, setWorkdir] = useState(config.agent.workdir);
  const [shortTerm, setShortTerm] = useState(config.agent.short_term_messages ?? 16);
  const [allow, setAllow] = useState(config.agent.command_allowlist.join("\n"));
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await api.saveConfig({
        agent: {
          timeout_sec: Number(timeout),
          workdir,
          short_term_messages: Math.max(2, Number(shortTerm) || 16),
          command_allowlist: allow
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
        },
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
    <form onSubmit={submit}>
      <label>
        命令超时（秒）
        <input type="number" min={1} value={timeout} onChange={(e) => setTimeoutSec(Number(e.target.value))} />
      </label>
      <label>
        工作目录
        <input value={workdir} onChange={(e) => setWorkdir(e.target.value)} />
      </label>
      <label>
        短期上下文（条）
        <span className="note">进入模型的近期消息上限</span>
        <input type="number" min={2} max={200} value={shortTerm} onChange={(e) => setShortTerm(Number(e.target.value))} />
      </label>
      <label>
        命令白名单（一行一个）
        <textarea value={allow} onChange={(e) => setAllow(e.target.value)} />
      </label>
      <SaveRow busy={busy} msg={msg} />
    </form>
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
    setMsg("已保存");
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 18 }}>
        <span className={`pill ${napcatOn ? "on" : ""}`}>
          <span className={napcatOn ? "dot on" : "dot"} />
          NapCat
        </span>
        <button className="btn ghost" type="button" onClick={() => api.togglePlatform("napcat").then(onSaved)}>
          {napcatOn ? "关闭" : "启用"}
        </button>
        <span className={`pill ${tuiOn ? "on" : ""}`}>
          <span className={tuiOn ? "dot on" : "dot"} />
          TUI
        </span>
        <button className="btn ghost" type="button" onClick={() => api.togglePlatform("tui").then(onSaved)}>
          {tuiOn ? "关闭" : "启用"}
        </button>
      </div>
      <p className="hint" style={{ marginBottom: 16 }}>
        TUI 客户端：`uv run python tui/ui.py`，默认 ws://127.0.0.1:{config.port}/ws/tui
      </p>
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
          Access Token
          <span className="note">当前 {config.napcat.access_token || "未设置"}，留空不改</span>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="新 token" />
        </label>
        <SaveRow msg={msg} />
      </form>
    </>
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
  const [engageSec, setEngageSec] = useState(initial.group_engage_sec ?? 90);
  const [engageReplies, setEngageReplies] = useState(initial.group_engage_replies ?? 2);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const next = config.identity ?? { owners: [], group_require_at: true };
    setOwners(next.owners.length ? next.owners : [{ platform: "napcat", user_id: "", nickname: "" }]);
    setRequireAt(next.group_require_at);
    setKeywords((next.wake_keywords || ["小lu", "luopita"]).join("\n"));
    setTechChance(Math.round((next.group_tech_chance ?? 0.35) * 100));
    setChattyChance(Math.round((next.group_chatty_chance ?? 0.22) * 100));
    setCooldown(next.group_chime_cooldown_sec ?? 35);
    setEngageSec(next.group_engage_sec ?? 90);
    setEngageReplies(next.group_engage_replies ?? 2);
  }, [config.identity]);

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
        group_engage_sec: Math.max(0, Number(engageSec) || 0),
        group_engage_replies: Math.max(0, Number(engageReplies) || 0),
      });
      await onSaved();
      setMsg("已写入 identity.yaml");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save}>
      <p className="hint">保存在 `config/identity.yaml`，打开本页会重新读文件。</p>
      <fieldset>
        <legend>群聊策略</legend>
        <label className="check">
          <input type="checkbox" checked={requireAt} onChange={(e) => setRequireAt(e.target.checked)} />
          <span>
            群聊需要 @ 或唤醒名才必回
            <span className="note"> 关闭后也可能按概率插话。私聊始终回复。</span>
          </span>
        </label>
        <label>
          唤醒名字（一行一个）
          <textarea value={keywords} onChange={(e) => setKeywords(e.target.value)} />
        </label>
        <div className="row owner-row">
          <label>
            技术问题插话（%）
            <input type="number" min={0} max={100} value={techChance} onChange={(e) => setTechChance(Number(e.target.value))} />
          </label>
          <label>
            闲聊搭话（%）
            <input type="number" min={0} max={100} value={chattyChance} onChange={(e) => setChattyChance(Number(e.target.value))} />
          </label>
          <label>
            搭话冷却（秒）
            <input type="number" min={0} value={cooldown} onChange={(e) => setCooldown(Number(e.target.value))} />
          </label>
        </div>
        <div className="row owner-row">
          <label>
            接话窗口（秒）
            <span className="note">被点名后继续接同一人的时长</span>
            <input type="number" min={0} value={engageSec} onChange={(e) => setEngageSec(Number(e.target.value))} />
          </label>
          <label>
            接话条数
            <span className="note">窗口内最多再回几句</span>
            <input
              type="number"
              min={0}
              value={engageReplies}
              onChange={(e) => setEngageReplies(Number(e.target.value))}
            />
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>主人</legend>
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
      </fieldset>
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
        {msg ? <span className={msg.startsWith("已") ? "flash" : "flash bad"}>{msg}</span> : null}
      </div>
    </form>
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

  useEffect(() => {
    setName(config.persona.name);
    setOwnerAddress(config.persona.owner_address || "");
    setVoice(config.persona.voice || "");
    setTaboos(config.persona.taboos || "");
    setRelationship(config.persona.relationship || "");
    setPrompt(config.persona.system_prompt);
  }, [config.persona]);

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
    setMsg("已写入 person.yaml");
  }

  return (
    <form onSubmit={save}>
      <p className="hint">保存在 `config/person.yaml`。具体怎么打字去「样例」页写对话，这里只留身份和禁忌。</p>
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
        额外说明
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      <SaveRow msg={msg} />
    </form>
  );
}

const SCENE_LABELS: Record<string, string> = {
  tech: "技术",
  chat: "闲聊",
  image: "看图",
  silence: "沉默",
};
const MODE_LABELS: Record<string, string> = {
  direct: "被叫到",
  chime: "插话",
  bare_wake: "点名",
};
const RELATION_LABELS: Record<string, string> = {
  owner: "主人",
  peer: "群友",
};
const FAMILIARITY_LABELS: Record<string, string> = {
  stranger: "生",
  peer: "熟",
  familiar: "很熟",
};

function blankExample(): VoiceExample {
  return { id: "", scene: "chat", mode: "direct", relation: "peer", input: "", good: "", bad: "" };
}

function ExamplesForm() {
  const [examples, setExamples] = useState<VoiceExample[]>([]);
  const [scenes, setScenes] = useState<string[]>(["tech", "chat", "image", "silence"]);
  const [modes, setModes] = useState<string[]>(["direct", "chime", "bare_wake"]);
  const [relations, setRelations] = useState<string[]>(["owner", "peer"]);
  const [open, setOpen] = useState(0);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);

  async function load() {
    const data = await api.examples();
    setExamples(data.examples);
    setScenes(data.scenes);
    setModes(data.modes);
    setRelations(data.relations);
    setReady(true);
  }

  useEffect(() => {
    load().catch((err: Error) => setMsg(err.message));
  }, []);

  function patch(index: number, partial: Partial<VoiceExample>) {
    setExamples((current) => current.map((item, i) => (i === index ? { ...item, ...partial } : item)));
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      const data = await api.saveExamples(examples);
      setExamples(data.examples);
      setMsg("已写入 voice_examples.yaml");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="examples-form" onSubmit={save}>
      <p className="hint">按场景写对话气泡。good 是她会说的，bad 是客服腔。保存后下一轮立刻生效，不会当成发生过的事。</p>
      {!ready ? <p className="hint">正在读取样例…</p> : null}
      {ready && examples.length === 0 ? <p className="hint">还没有样例，先加一条。</p> : null}
      {examples.map((item, index) => (
        <article className="example-card" key={`${item.id || "new"}-${index}`}>
          <div className="example-head">
            <button type="button" className="ghost-link" onClick={() => setOpen(open === index ? -1 : index)}>
              {SCENE_LABELS[item.scene] || item.scene} · {MODE_LABELS[item.mode] || item.mode} ·{" "}
              {RELATION_LABELS[item.relation] || item.relation}
              {item.input ? ` · ${item.input}` : ""}
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => {
                setExamples((current) => current.filter((_, i) => i !== index));
                if (open >= examples.length - 1) setOpen(Math.max(0, index - 1));
              }}
            >
              删除
            </button>
          </div>
          {open === index ? (
            <>
              <label>
                ID
                <input value={item.id} onChange={(e) => patch(index, { id: e.target.value })} placeholder="可留空，保存时自动生成" />
              </label>
              <div className="fields-3">
                <label>
                  场景
                  <select value={item.scene} onChange={(e) => patch(index, { scene: e.target.value })}>
                    {scenes.map((value) => (
                      <option key={value} value={value}>
                        {SCENE_LABELS[value] || value}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  模式
                  <select value={item.mode} onChange={(e) => patch(index, { mode: e.target.value })}>
                    {modes.map((value) => (
                      <option key={value} value={value}>
                        {MODE_LABELS[value] || value}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  对象
                  <select value={item.relation} onChange={(e) => patch(index, { relation: e.target.value })}>
                    {relations.map((value) => (
                      <option key={value} value={value}>
                        {RELATION_LABELS[value] || value}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label>
                用户会说
                <input value={item.input} onChange={(e) => patch(index, { input: e.target.value })} />
              </label>
              <label>
                小Lu（一层意思一行）
                <textarea className="tall" value={item.good} onChange={(e) => patch(index, { good: e.target.value })} />
              </label>
              <label>
                不要像
                <textarea className="tall" value={item.bad} onChange={(e) => patch(index, { bad: e.target.value })} />
              </label>
            </>
          ) : null}
        </article>
      ))}
      <div className="row">
        <button
          type="button"
          className="btn ghost"
          onClick={() => {
            setExamples((current) => [...current, blankExample()]);
            setOpen(examples.length);
          }}
        >
          加一条
        </button>
      </div>
      <SaveRow busy={busy} msg={msg} />
    </form>
  );
}

function profileKey(item: SpeakerProfile) {
  return `${item.platform}:${item.chat_id}:${item.user_id}`;
}

function ProfilesPanel() {
  const [profiles, setProfiles] = useState<SpeakerProfile[]>([]);
  const [active, setActive] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const data = await api.profiles();
    setProfiles(data.profiles);
    setError("");
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  const selected = profiles.find((item) => profileKey(item) === active);

  async function remove() {
    if (!selected) return;
    await api.deleteProfile(selected.platform, selected.chat_id, selected.user_id);
    setActive("");
    await load();
  }

  return (
    <section>
      <div className="page-head">
        <h1>画像</h1>
        <p className="hint">只看当前说话人卡片。从和 bot 的分片对话慢更新，不是整群花名册。</p>
      </div>
      {error ? <p className="flash bad">{error}</p> : null}
      <div className="sessions-layout">
        <article className="card">
          <h3>群友</h3>
          <div className="session-list">
            {profiles.length === 0 ? (
              <div className="empty">
                <SittingCat />
                还没有画像
              </div>
            ) : null}
            {profiles.map((item) => {
              const id = profileKey(item);
              return (
                <button
                  key={id}
                  className={active === id ? "session active" : "session"}
                  type="button"
                  onClick={() => setActive(id)}
                >
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <div>{item.display_name || item.user_id}</div>
                    <span className="pill">{FAMILIARITY_LABELS[item.card.familiarity] || item.card.familiarity}</span>
                  </div>
                  <div className="preview">
                    {item.platform} · {item.chat_id || "（无群）"} · {item.user_id}
                  </div>
                </button>
              );
            })}
          </div>
        </article>
        <article className="card">
          <h3>卡片</h3>
          {!selected ? (
            <div className="empty">
              <SittingCat />
              选一个群友看她怎么被记住
            </div>
          ) : (
            <>
              <div className="stat">
                <span>称呼</span>
                <span>{selected.card.address || selected.display_name || "-"}</span>
              </div>
              <div className="stat">
                <span>熟度</span>
                <span>{FAMILIARITY_LABELS[selected.card.familiarity] || selected.card.familiarity}</span>
              </div>
              <div className="stat">
                <span>怎么回</span>
                <span>{selected.card.reply_pref || "-"}</span>
              </div>
              <div className="stat">
                <span>栈</span>
                <span>{selected.card.stack.length ? selected.card.stack.join("、") : "-"}</span>
              </div>
              <div className="stat">
                <span>雷点</span>
                <span>{selected.card.taboos || "-"}</span>
              </div>
              <div className="stat">
                <span>近期</span>
                <span>{selected.card.recent || "-"}</span>
              </div>
              <div className="stat">
                <span>证据</span>
                <span>{selected.card.evidence || "-"}</span>
              </div>
              <div className="stat">
                <span>更新</span>
                <span>{selected.updated_at || "-"}</span>
              </div>
              {selected.prompt ? <pre className="profile-prompt">{selected.prompt}</pre> : <p className="hint">这张卡还太空，进 prompt 会被省略。</p>}
              <div className="row" style={{ marginTop: 12 }}>
                <button type="button" className="btn ghost" onClick={() => remove()}>
                  清除此画像
                </button>
                <button type="button" className="btn ghost" onClick={() => load()}>
                  刷新
                </button>
              </div>
            </>
          )}
        </article>
      </div>
    </section>
  );
}

function SystemForm({
  config,
  health,
  onSaved,
}: {
  config: PublicConfig;
  health: { provider: string; database: string; db_ok: boolean; redis?: string; redis_ok?: boolean } | null;
  onSaved: () => Promise<void>;
}) {
  const [logLevel, setLogLevel] = useState(config.log_level || "INFO");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      await api.saveConfig({ log_level: logLevel });
      await onSaved();
      setMsg("已保存");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <article className="card" style={{ marginBottom: 18, maxWidth: 760 }}>
        <h2>只读</h2>
        <div className="stat">
          <span>监听</span>
          <span>
            {config.host}:{config.port}
          </span>
        </div>
        <div className="stat">
          <span>数据库</span>
          <span>
            {health?.database || config.database || "-"} · {health?.db_ok ? "正常" : "异常"}
          </span>
        </div>
        <div className="stat">
          <span>Redis</span>
          <span>
            {health?.redis || config.redis || "-"} · {health?.redis_ok ? "正常" : "异常"}
          </span>
        </div>
        <div className="stat">
          <span>管理认证</span>
          <span>{config.admin_auth_required ? "已开启" : "未开启"}</span>
        </div>
      </article>
      <form onSubmit={save}>
        <label>
          日志级别
          <select value={logLevel} onChange={(e) => setLogLevel(e.target.value)}>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <SaveRow busy={busy} msg={msg} />
      </form>
    </>
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
      <div className="page-head">
        <h1>会话</h1>
        <p className="hint">查看历史，或在这里发一条测试消息</p>
      </div>
      <div className="sessions-layout">
        <article className="card">
          <h3>会话列表</h3>
          <div className="session-list">
            {list.length === 0 ? (
              <div className="empty">
                <SittingCat />
                还没有会话
              </div>
            ) : null}
            {list.map((item) => {
              const id = String(item.session_id);
              return (
                <button
                  key={id}
                  className={active === id ? "session active" : "session"}
                  type="button"
                  onClick={() => openSession(id)}
                >
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <div className="mono">{id}</div>
                    <span className="pill">{item.role === "owner" ? "主人" : "用户"}</span>
                  </div>
                  {item.preview ? <div className="preview">{String(item.preview)}</div> : null}
                </button>
              );
            })}
          </div>
        </article>
        <article className="card">
          <h3>对话</h3>
          <div className="messages">
            {messages.length === 0 ? (
              <div className="empty">
                <SittingCat />
                {active ? "这条线程还是空的" : "选一个会话，或直接打招呼"}
              </div>
            ) : null}
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
