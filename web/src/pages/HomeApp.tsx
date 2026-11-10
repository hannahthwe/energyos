// Web version of the homeowner iOS app, so the demo works in a browser without
// Xcode. It only calls /api/home/* as the homeowner role; the backend decides
// what the homeowner may see.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, BatteryCharging, Home, LifeBuoy, Send, ShieldCheck, Zap } from "lucide-react";
import { api, type DemoState, type Evidence } from "@/lib/api";
import { Button, Spinner, SyntheticLabel } from "@/components/ui";
import { cn } from "@/lib/utils";

interface Overview {
  customer_first_name: string;
  ev: { soc_pct: number; reserve_pct: number; plugged_in: boolean; usable_for_home_kwh: number };
  v2h: { available: boolean; reason: string; next_window: string; last_night_delivered: boolean };
  charger: { status: string; model: string };
  home: { load_kw: number; solar_kw: number };
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

const SUGGESTIONS = ["Why didn't my EV discharge last night?", "Is my charger working?", "What should I do next?"];

export default function HomeApp() {
  const [demo, setDemo] = useState<DemoState | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [approved, setApproved] = useState(false);
  const [created, setCreated] = useState<Record<string, string>>({});
  const end = useRef<HTMLDivElement>(null);

  const load = () => {
    api.demo().then(setDemo).catch(() => setDemo(null));
    home<Overview>("/api/home/overview").then(setOv).catch(() => setOv(null));
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

  const changeScenario = async (code: string) => {
    await api.setScenario(code);
    setTurns([]);
    load();
  };

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
    <div className="min-h-screen bg-slate-200 py-6">
      <div className="mx-auto mb-3 flex max-w-[420px] flex-wrap items-center justify-between gap-2 px-4 text-xs text-slate-600">
        <span>Homeowner app (web preview of the iOS app)</span>
        {demo && (
          <select className="rounded border border-slate-300 bg-white px-2 py-1" value={demo.scenario} onChange={(e) => changeScenario(e.target.value)}>
            {demo.scenarios.map((s) => (
              <option key={s.code} value={s.code}>
                Scenario {s.code}: {s.name}
              </option>
            ))}
          </select>
        )}
        <Link to="/support" className="font-medium text-teal-700 underline">Open support console →</Link>
      </div>

      <div className="relative mx-auto flex h-[780px] max-w-[400px] flex-col overflow-hidden rounded-[2.2rem] border-[10px] border-navy bg-slate-50 shadow-xl">
        <header className="flex items-center gap-2 bg-navy px-4 py-3 text-white">
          <div className="grid h-7 w-7 place-items-center rounded-lg bg-teal"><Activity className="h-4 w-4" /></div>
          <div className="leading-tight">
            <div className="text-sm font-semibold">HelioHEMS Home</div>
            <div className="text-[11px] text-slate-300">{ov ? `Hi ${ov.customer_first_name}` : "Connecting…"}</div>
          </div>
          <div className="ml-auto"><SyntheticLabel /></div>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-3">
          {ov && (
            <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
              <Tile icon={<BatteryCharging className="h-4 w-4" />} label="EV battery" value={`${Math.round(ov.ev.soc_pct)}%`} sub={`reserve ${Math.round(ov.ev.reserve_pct)}%`} />
              <Tile icon={<Zap className="h-4 w-4" />} label="Car → home" value={ov.v2h.available ? "Ready" : "Paused"} sub={ov.v2h.next_window} warn={!ov.v2h.available} />
              <Tile icon={<Home className="h-4 w-4" />} label="Home use" value={`${ov.home.load_kw.toFixed(1)} kW`} sub={`charger ${ov.charger.status}`} />
            </div>
          )}

          <div className="rounded-xl bg-white p-3 text-xs text-slate-600 shadow-sm">
            Ask the assistant about your car, charger or home energy. Answers come from your home's own records.
            <div className="mt-2 flex flex-wrap gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} disabled={busy} onClick={() => ask(s)} className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-600 hover:border-teal hover:text-teal-700">
                  {s}
                </button>
              ))}
            </div>
          </div>

          {turns.map((t, i) =>
            t.kind === "q" ? (
              <div key={i} className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-teal px-3 py-2 text-sm text-white">{t.text}</div>
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
            className="flex-1 rounded-full border border-slate-200 px-3 py-2 text-sm outline-none focus:border-teal"
          />
          <Button type="submit" variant="teal" disabled={busy || !q.trim()} aria-label="Send">
            <Send className="h-4 w-4" />
          </Button>
        </form>

        {preview && (
          <div className="absolute inset-0 z-10 flex flex-col bg-white">
            <div className="flex items-center gap-2 border-b p-3 text-sm font-semibold text-navy">
              <ShieldCheck className="h-4 w-4 text-teal" /> Share with HelioHEMS support?
            </div>
            <div className="flex-1 space-y-3 overflow-y-auto p-3 text-xs text-slate-700">
              <p className="font-medium">{preview.summary}</p>
              <p>These records from your home will be shared:</p>
              <ul className="space-y-1">
                {preview.evidence_to_share.map((e) => (
                  <li key={e.evidence_id} className="rounded bg-slate-50 p-2">
                    <div>{e.summary}</div>
                    <div className="text-[10px] text-slate-400">{e.source_label} · {e.evidence_id}</div>
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
              <Button variant="teal" className="flex-1" disabled={!approved} onClick={share}>Share and request support</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({ icon, label, value, sub, warn }: { icon: React.ReactNode; label: string; value: string; sub: string; warn?: boolean }) {
  return (
    <div className="rounded-xl bg-white p-2 shadow-sm">
      <div className={cn("mx-auto mb-1 grid h-7 w-7 place-items-center rounded-full", warn ? "bg-amber-100 text-amber-700" : "bg-teal/10 text-teal-700")}>{icon}</div>
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
        <div className="rounded-lg bg-teal/10 p-2 text-teal-800">
          Support request {caseId} sent. <Link to={`/support/cases/${caseId}`} className="underline">View it in the support console</Link>
        </div>
      ) : (
        a.support_recommended && (
          <Button variant="teal" className="w-full" onClick={onSupport}>
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
