// Web version of the homeowner iOS app, so the demo works in a browser without
// Xcode. It only calls /api/home/* as the homeowner role; the backend decides
// what the homeowner may see.
import { useEffect, useRef, useState } from "react";
import { BatteryCharging, ChevronRight, Home, LifeBuoy, Lock, PiggyBank, Send, ShieldCheck, X, Zap, HeartHandshake } from "lucide-react";
import { type DataConsent, type Evidence } from "@/lib/api";
import { useDemoMode, useHiddenScenarioSwitch } from "@/components/Layout";
import { Button, Spinner } from "@/components/ui";
import { cn } from "@/lib/utils";

interface Overview {
  customer_first_name: string;
  ev: { soc_pct: number | null; reserve_pct: number | null; plugged_in: boolean; usable_for_home_kwh: number };
  v2h: { available: boolean; reason: string; next_window: string; last_night_delivered: boolean };
  charger: { status: string; model: string };
  home: { load_kw: number | null; solar_kw: number };
}

interface HomeAnswer {
  run_id: string;
  question: string;
  headline: string;
  what_happened: string;
  why: string[];
  unknown: string[];
  next_steps: string[];
  support_recommended: boolean;
  evidence: Evidence[];
}

interface Preview {
  run_id: string;
  summary: string;
  evidence_to_share: Evidence[];
  also_available_to_support: string;
}

interface Savings {
  window: string;
  contract_type: "fixed" | "dynamic" | null;
  actual_eur: number | null;
  cheapest_eur: number | null;
  saving_eur: number | null;
  energy_kwh: number;
  sessions: { date: string; start: string; end: string; actual_eur: number | null; cheapest_eur: number | null; cheapest_hours: string[]; departure: string | null }[];
  tip: string;
  unavailable_sources: string[];
}

type Turn = { kind: "q"; text: string } | { kind: "a"; data: HomeAnswer } | { kind: "err"; text: string };

async function home<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Role": "homeowner" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

const SUGGESTIONS = [
  "Why didn't my EV discharge last night?",
  "Why did my home battery pause last night?",
  "Why was my charging so expensive last night?",
  "What should I do next?",
];

const eur = (v: number | null | undefined) => (v == null ? "–" : `€${v.toFixed(2)}`);

export default function HomeApp() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [approved, setApproved] = useState(false);
  const [created, setCreated] = useState<Record<string, string>>({});
  const [savings, setSavings] = useState<Savings | null>(null);
  const [privacy, setPrivacy] = useState<DataConsent[] | null>(null);
  const demoMode = useDemoMode();
  const end = useRef<HTMLDivElement>(null);

  const load = () => {
    home<Overview>("/api/home/overview").then(setOv).catch(() => setOv(null));
    home<Savings>("/api/home/savings?window=7d").then(setSavings).catch(() => setSavings(null));
  };

  const openPrivacy = async () => setPrivacy(await home<DataConsent[]>("/api/home/consents"));
  const toggle = async (c: DataConsent) => {
    if (c.active && c.consent_id) await home(`/api/home/consents/${c.consent_id}/revoke`, {});
    else await home("/api/home/consents", { source_system: c.source_system });
    await openPrivacy();
    load();
  };
  useEffect(load, []);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, busy]);

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setQ("");
    setTurns((t) => [...t, { kind: "q", text }]);
    setBusy(true);
    try {
      const data = await home<HomeAnswer>("/api/home/chat", { question: text });
      setTurns((t) => [...t, { kind: "a", data }]);
    } catch (e) {
      setTurns((t) => [...t, { kind: "err", text: `Couldn't reach the assistant (${(e as Error).message}).` }]);
    } finally {
      setBusy(false);
    }
  };

  useHiddenScenarioSwitch(() => {
    setTurns([]);
    load();
  });


  const openConsent = async (run_id: string) => {
    setApproved(false);
    setPreview(await home<Preview>(`/api/home/support-request/${run_id}/preview`));
  };

  const share = async () => {
    if (!preview || !approved) return;
    const c = await home<{ case_id: string }>("/api/home/support-request", { run_id: preview.run_id, approved: true, note: "" });
    setCreated((m) => ({ ...m, [preview.run_id]: c.case_id }));
    setPreview(null);
  };

  return (
    <div className="min-h-[100dvh] bg-slate-200 sm:py-6">
      {/* Full screen on a phone; shown inside a phone frame on a laptop. */}
      <div className="relative mx-auto flex h-[100dvh] w-full flex-col overflow-hidden bg-cream sm:h-[780px] sm:max-w-[400px] sm:rounded-[2.2rem] sm:border-[10px] sm:border-navy sm:shadow-xl">
        <header className="flex items-center gap-2 bg-navy px-4 py-3 text-white">
          <div className="grid h-7 w-7 place-items-center rounded-full bg-sky-300"><HeartHandshake className="h-4 w-4" /></div>
          <div className="leading-tight">
            <div className="text-sm font-semibold">HelioHEMS Home</div>
            <div className="text-[11px] text-slate-300">{ov ? <>Hi {ov.customer_first_name} <span className="ml-0.5 inline-block h-1.5 w-1.5 rounded-full bg-peach align-middle" /></> : "Connecting…"}</div>
          </div>
          <button onClick={openPrivacy} className="ml-auto rounded-md p-1 text-slate-300 hover:bg-white/10 hover:text-white" aria-label="Data & privacy" title="Data & privacy">
            <Lock className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-3">
          {ov && (
            <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
              <Tile icon={<BatteryCharging className="h-4 w-4" />} label="EV battery" value={ov.ev.soc_pct == null ? "–" : `${Math.round(ov.ev.soc_pct)}%`} sub={ov.ev.reserve_pct == null ? "reserve –" : `reserve ${Math.round(ov.ev.reserve_pct)}%`} />
              <Tile icon={<Zap className="h-4 w-4" />} label="Car → home" value={ov.v2h.available ? "Ready" : "Paused"} sub={ov.v2h.next_window || "no window"} warn={!ov.v2h.available} />
              <Tile icon={<Home className="h-4 w-4" />} label="Home use" value={ov.home.load_kw == null ? "–" : `${ov.home.load_kw.toFixed(1)} kW`} sub={`charger ${ov.charger.status}`} />
            </div>
          )}

          {savings && <SavingsCard s={savings} onPrivacy={openPrivacy} />}

          <div className="rounded-xl bg-white p-3 text-xs text-slate-600 shadow-sm">
            Ask the assistant about your car, charger or home energy. Answers come from your home's own records.
            <div className="mt-2 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} disabled={busy} onClick={() => ask(s)} className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-600 hover:border-sky hover:text-sky-700">
                  {s}
                </button>
              ))}
            </div>
          </div>

          {turns.map((t, i) =>
            t.kind === "q" ? (
              <div key={i} className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-sky-100 px-3 py-2 text-sm text-navy">{t.text}</div>
            ) : t.kind === "err" ? (
              <div key={i} className="rounded-xl bg-red-50 p-3 text-xs text-red-700">{t.text}</div>
            ) : (
              <AnswerCard key={i} a={t.data} caseId={created[t.data.run_id]} onSupport={() => openConsent(t.data.run_id)} />
            ),
          )}
          {busy && (
            <div className="flex items-center gap-2 text-xs text-slate-500"><Spinner /> Checking your system…</div>
          )}
          <div ref={end} />
        </div>

        <form
          className="flex gap-2 border-t border-slate-200 bg-white p-2"
          onSubmit={(e) => {
            e.preventDefault();
            ask(q);
          }}
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Type your question…"
            className="flex-1 rounded-full border border-slate-200 px-3 py-2 text-sm outline-none focus:border-sky"
          />
          <Button type="submit" variant="sky" disabled={busy || !q.trim()} aria-label="Send">
            <Send className="h-4 w-4" />
          </Button>
        </form>

        {preview && (
          <div className="absolute inset-0 z-10 flex flex-col bg-white">
            <div className="flex items-center gap-2 border-b p-3 text-sm font-semibold text-navy">
              <ShieldCheck className="h-4 w-4 text-sky" /> Share with HelioHEMS support?
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs text-slate-700">
              <p className="font-medium">{preview.summary}</p>
              <p>These records from your home will be shared:</p>
              <ul className="space-y-1">
                {preview.evidence_to_share.map((e) => (
                  <li key={e.evidence_id} className="rounded bg-slate-50 p-2">
                    <div>{e.summary}</div>
                    <div className="text-[10px] text-slate-400">{e.source_label}{demoMode ? "" : ` · ${e.evidence_id}`}</div>
                  </li>
                ))}
              </ul>
              <p className="text-slate-500">{preview.also_available_to_support}</p>
              <label className="flex items-start gap-2 rounded-lg border p-2">
                <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} className="mt-0.5" />
                I agree to share this conversation and these records with HelioHEMS support.
              </label>
            </div>
            <div className="flex gap-2 border-t p-3">
              <Button variant="secondary" className="flex-1" onClick={() => setPreview(null)}>Cancel</Button>
              <Button variant="peach" className="flex-1" disabled={!approved} onClick={share}>Share and request support</Button>
            </div>
          </div>
        )}

        {privacy && <PrivacyScreen rows={privacy} onToggle={toggle} onClose={() => setPrivacy(null)} />}
      </div>
    </div>
  );
}

function SavingsCard({ s, onPrivacy }: { s: Savings; onPrivacy: () => void }) {
  const last = s.sessions[s.sessions.length - 1];
  return (
    <div className="rounded-xl bg-white p-3 text-xs text-slate-700 shadow-sm">
      <div className="mb-1 flex items-center gap-2 text-sm font-semibold text-navy">
        <PiggyBank className="h-4 w-4 text-sky" /> Savings · last 7 days
        {s.contract_type && <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">{s.contract_type} price</span>}
      </div>
      {s.contract_type && s.sessions.length > 0 ? (
        <>
          <div className="grid grid-cols-3 gap-2 text-center">
            <Stat label="You paid" value={eur(s.actual_eur)} />
            <Stat label="Cheapest possible" value={eur(s.cheapest_eur)} />
            <Stat label="Could save" value={eur(s.saving_eur)} accent />
          </div>
          {last && last.cheapest_hours.length > 0 && (s.saving_eur ?? 0) > 0 && (
            <p className="mt-2 text-[11px] text-slate-500">
              Last charge {last.start}–{last.end}: {eur(last.actual_eur)}. Cheaper hours before your {last.departure} departure: {last.cheapest_hours.join(", ")}.
            </p>
          )}
        </>
      ) : null}
      <p className="mt-2">{s.tip}</p>
      {s.unavailable_sources.length > 0 && (
        <button onClick={onPrivacy} className="mt-1 text-[11px] font-medium text-sky-700 underline">Open Data &amp; privacy</button>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={cn("rounded-lg p-2", accent ? "bg-sky/10" : "bg-slate-50")}>
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={cn("text-sm font-semibold", accent ? "text-sky-700" : "text-navy")}>{value}</div>
    </div>
  );
}

function PrivacyScreen({ rows, onToggle, onClose }: { rows: DataConsent[]; onToggle: (c: DataConsent) => void; onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-white">
      <div className="flex items-center gap-2 border-b p-3 text-sm font-semibold text-navy">
        <Lock className="h-4 w-4 text-sky" /> Data &amp; privacy
        <button onClick={onClose} className="ml-auto rounded p-1 text-slate-400 hover:bg-slate-100" aria-label="Close"><X className="h-4 w-4" /></button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-xs text-slate-700">
        <p className="text-slate-500">Choose which data the assistant may use. If you turn a source off, answers will say that data is unavailable instead of using it.</p>
        {rows.map((c) => (
          <div key={c.source_system} className="flex items-start gap-3 rounded-lg border border-slate-200 p-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-navy">{c.label}</div>
              <div className="text-[11px] text-slate-500">{c.scope}</div>
              <div className="mt-0.5 text-[10px] text-slate-400">
                {c.legal_basis.startsWith("EU Data Act") ? "EU Data Act: you chose HelioHEMS to receive this data" : c.legal_basis === "contract" ? "Needed for your HelioHEMS service contract" : c.legal_basis}
                {c.active && c.expires_at ? ` · renews by ${c.expires_at.slice(0, 10)}` : ""}
                {!c.active && c.revoked_at ? ` · turned off ${c.revoked_at.slice(0, 10)}` : ""}
              </div>
            </div>
            {c.toggleable ? (
              <button
                role="switch"
                aria-checked={c.active}
                aria-label={`${c.label} sharing`}
                onClick={() => onToggle(c)}
                className={cn("relative mt-1 h-5 w-9 shrink-0 rounded-full transition", c.active ? "bg-sky" : "bg-slate-300")}
              >
                <span className={cn("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition", c.active ? "left-[18px]" : "left-0.5")} />
              </button>
            ) : (
              <span className="mt-1 text-[10px] text-slate-400">always on</span>
            )}
          </div>
        ))}
      </div>
      <div className="border-t p-3">
        <Button variant="secondary" className="w-full" onClick={onClose}>Done <ChevronRight className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}

function Tile({ icon, label, value, sub, warn }: { icon: React.ReactNode; label: string; value: string; sub: string; warn?: boolean }) {
  return (
    <div className="rounded-xl bg-white p-2 shadow-sm">
      <div className={cn("mx-auto mb-1 grid h-7 w-7 place-items-center rounded-full", warn ? "bg-amber-100 text-amber-700" : "bg-sky/10 text-sky-700")}>{icon}</div>
      <div className="text-slate-500">{label}</div>
      <div className="text-sm font-semibold text-navy">{value}</div>
      <div className="text-[10px] text-slate-400">{sub}</div>
    </div>
  );
}

function AnswerCard({ a, caseId, onSupport }: { a: HomeAnswer; caseId?: string; onSupport: () => void }) {
  return (
    <div className="max-w-[95%] space-y-2 rounded-2xl rounded-bl-sm bg-white p-3 text-xs text-slate-700 shadow-sm">
      <div className="text-sm font-semibold text-navy">{a.headline}</div>
      <p>{a.what_happened}</p>
      <Section title="Why" items={a.why} />
      <Section title="What we don't know yet" items={a.unknown} />
      <Section title="What to do" items={a.next_steps} />
      {a.evidence.length > 0 && (
        <details className="text-[11px] text-slate-500">
          <summary className="cursor-pointer">Based on {a.evidence.length} records from your home</summary>
          <ul className="mt-1 space-y-0.5">
            {a.evidence.map((e) => <li key={e.evidence_id}>• {e.summary}</li>)}
          </ul>
        </details>
      )}
      {caseId ? (
        <div className="rounded-lg bg-sky/10 p-2 text-sky-800">
          Support request {caseId} sent. A HelioHEMS specialist will get back to you here.
        </div>
      ) : (
        a.support_recommended && (
          <Button variant="peach" className="w-full" onClick={onSupport}>
            <LifeBuoy className="h-4 w-4" /> Request support
          </Button>
        )
      )}
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="font-semibold text-slate-800">{title}</div>
      <ul className="ml-4 list-disc space-y-0.5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}
