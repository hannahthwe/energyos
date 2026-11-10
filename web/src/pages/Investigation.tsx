import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Bot, CheckCircle2, CircleDashed, Clock, Cpu, Database, FileText, GitMerge,
  Lightbulb, ListChecks, MessageSquare, Plug, Search, Send, ShieldAlert, Sparkles, StickyNote, Wrench, XCircle, Zap,
} from "lucide-react";
import {
  api, type AgentResult, type AskResult, type CaseDetail, type CaseStatus, type Cited, type DataModel, type Escalation,
} from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Spinner, statusTone, visLabel, visTone } from "@/components/ui";
import { DeviceTable, Field } from "@/pages/Queue";
import { cn, fmtTime } from "@/lib/utils";

const AGENT_ICON: Record<string, JSX.Element> = {
  technical_diagnostics: <Wrench className="h-4 w-4" />,
  energy_behaviour: <Zap className="h-4 w-4" />,
  security_correlation: <ShieldAlert className="h-4 w-4" />,
  support_resolution: <FileText className="h-4 w-4" />,
};
const SOURCE_TONE: Record<string, "navy" | "teal" | "violet" | "amber" | "slate"> = {
  hems_backend: "navy", charger_integration: "violet", config_audit: "amber", crm: "teal", knowledge_base: "slate",
};
const SOURCE_SHORT: Record<string, string> = {
  hems_backend: "HEMS", charger_integration: "Charger API", config_audit: "Audit", crm: "CRM", knowledge_base: "KB",
};
const QUESTIONS = [
  "Why did the discharge fail?",
  "Which system rejected the command?",
  "Was the SOC reserve violated?",
  "Was there a configuration change?",
  "What evidence is missing?",
  "What should support do next?",
];

export default function Investigation() {
  const { caseId = "" } = useParams();
  const [d, setD] = useState<CaseDetail | null>(null);
  const [dm, setDm] = useState<DataModel | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api.case(caseId).then(setD).catch((e) => setErr(String(e)));
    api.dataModel().then(setDm);
  }, [caseId]);

  useEffect(() => {
    load();
    window.addEventListener("cc:scenario", load);
    return () => window.removeEventListener("cc:scenario", load);
  }, [load]);

  if (err) return <Card className="p-6 text-sm text-red-700">{err}</Card>;
  if (!d) return <Spinner />;
  const diag = d.diagnosis;

  return (
    <div className="space-y-5">
      <TopBar d={d} reload={load} />
      {!diag ? (
        <Card className="p-6 text-sm text-slate-600">This case was imported from CRM and has no AI investigation.</Card>
      ) : (
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
          <div className="min-w-0 space-y-5">
            <CustomerStrip d={d} />
            <Findings d={d} />
            <div>
              <SectionTitle icon={<Bot className="h-4 w-4" />} title="Specialist agents" hint="Live module results from the orchestrated run" />
              <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
                {diag.agents.map((a) => (
                  <AgentCard key={a.key} a={a} />
                ))}
              </div>
            </div>
            <div className="grid gap-5 2xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <Timeline d={d} />
              <PowerChart d={d} />
            </div>
            {dm && <DataModelPanel dm={dm} />}
            <EvidenceTable d={d} />
          </div>
          <div className="space-y-5 xl:sticky xl:top-20">
            <Copilot d={d} onAsked={load} />
            <Workflow d={d} reload={load} />
          </div>
        </div>
      )}
    </div>
  );
}

function SectionTitle({ icon, title, hint }: { icon: JSX.Element; title: string; hint?: string }) {
  return (
    <div className="mb-2 flex items-baseline gap-2">
      <span className="flex items-center gap-2 text-sm font-semibold text-navy">
        <span className="text-teal">{icon}</span>
        {title}
      </span>
      {hint && <span className="text-xs text-slate-400">{hint}</span>}
    </div>
  );
}

function TopBar({ d, reload }: { d: CaseDetail; reload: () => void }) {
  const set = async (s: CaseStatus) => {
    await api.status(d.case.case_id, s);
    reload();
  };
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Link to="/support" className="flex items-center gap-1 text-sm text-slate-500 hover:text-navy">
        <ArrowLeft className="h-4 w-4" /> Queue
      </Link>
      <span className="font-mono text-sm text-slate-500">{d.case.case_id}</span>
      <h1 className="text-lg font-semibold">{d.case.title}</h1>
      {d.case.escalation_required && (
        <Badge tone="red">
          <AlertTriangle className="h-3 w-3" /> escalation required
        </Badge>
      )}
      {d.diagnosis && (
        <Badge tone={d.diagnosis.mode === "claude" ? "teal" : "slate"}>
          {d.diagnosis.mode === "claude" ? <Bot className="h-3 w-3" /> : <Cpu className="h-3 w-3" />}
          {d.diagnosis.mode === "claude" ? "Claude orchestrated" : "Deterministic orchestrator"}
        </Badge>
      )}
      {!d.case.live_data_matches && d.case.origin === "homeowner_app" && (
        <Badge tone="amber">Showing snapshot from handoff (live demo data is on another scenario)</Badge>
      )}
      <div className="ml-auto flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1">
        {(["New", "Investigating", "Escalated", "Resolved"] as CaseStatus[]).map((s) => (
          <button
            key={s}
            onClick={() => set(s)}
            className={cn("rounded-md px-2.5 py-1 text-xs font-medium", d.case.status === s ? "bg-navy text-white" : "text-slate-600 hover:bg-slate-100")}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function CustomerStrip({ d }: { d: CaseDetail }) {
  const hems = d.devices.find((x) => x.device_type === "hems");
  const chg = d.devices.find((x) => x.device_type === "ev_charger");
  const ev = d.devices.find((x) => x.device_type === "ev");
  return (
    <Card>
      <CardBody className="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6">
        <Field k="Customer" v={`${d.customer.name} · ${d.customer.customer_id}`} />
        <Field k="Installation" v={`${d.installation.installation_id} · ${d.installation.timezone}`} />
        {hems && <Field k="HEMS" v={`${hems.manufacturer} ${hems.model} · fw ${hems.firmware_version}`} />}
        {chg && <Field k="Charger" v={`${chg.manufacturer} ${chg.model} · fw ${chg.firmware_version}`} />}
        {ev && <Field k="Vehicle" v={`${ev.manufacturer} ${ev.model}`} />}
        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-400">Connectivity</div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {d.devices.map((x) => (
              <Badge key={x.device_id} tone={x.connectivity_status === "online" ? "teal" : "red"}>
                {x.device_type.replace("ev_charger", "charger")}: {x.connectivity_status}
              </Badge>
            ))}
          </div>
        </div>
      </CardBody>
      <details className="border-t border-slate-100">
        <summary className="cursor-pointer px-4 py-2 text-xs font-medium text-slate-500 hover:text-navy">Registered devices and verified source identifiers</summary>
        <DeviceTable devices={d.devices} />
      </details>
    </Card>
  );
}

function EvChips({ ids, d }: { ids: string[]; d: CaseDetail }) {
  const by = Object.fromEntries((d.diagnosis?.evidence || []).map((e) => [e.evidence_id, e]));
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {ids.map((i) => {
        const e = by[i];
        return (
          <a
            key={i}
            href={`#ev-${i}`}
            title={e ? `${e.source_label}: ${e.summary}` : i}
            className={cn(
              "rounded border px-1.5 py-px font-mono text-[10px] hover:bg-slate-100",
              e?.visibility_level === "restricted" ? "border-red-200 text-red-700" : "border-slate-200 text-slate-500",
            )}
          >
            {i.replace("EVD-", "")}
          </a>
        );
      })}
    </span>
  );
}

function CitedList({ items, d }: { items: Cited[]; d: CaseDetail }) {
  return (
    <ul className="space-y-1.5 text-sm">
      {items.map((f, i) => (
        <li key={i} className="leading-relaxed">
          {f.text}
          <EvChips ids={f.evidence_ids} d={d} />
        </li>
      ))}
    </ul>
  );
}

function Findings({ d }: { d: CaseDetail }) {
  const g = d.diagnosis!;
  const conf = { high: "teal", medium: "amber", low: "red" }[g.confidence.level] as "teal" | "amber" | "red";
  return (
    <Card className="overflow-hidden">
      <div className="bg-gradient-to-r from-navy to-navy-700 px-5 py-4 text-white">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-teal-100">
          <Sparkles className="h-3.5 w-3.5" /> AI diagnosis · incident {fmtTime(g.incident_utc)}
        </div>
        <p className="mt-1 text-base font-medium">{g.headline}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
          <Badge tone={conf}>confidence: {g.confidence.level}</Badge>
          <span>{g.confidence.explanation}</span>
        </div>
      </div>
      <div className="grid gap-px bg-slate-100 md:grid-cols-2">
        <Block icon={<CheckCircle2 className="h-4 w-4" />} title="Observed facts" tone="text-teal-700">
          <CitedList items={g.observed_facts} d={d} />
        </Block>
        <Block icon={<Lightbulb className="h-4 w-4" />} title="Interpretation" tone="text-navy">
          <CitedList items={g.interpretation} d={d} />
        </Block>
        <Block icon={<CircleDashed className="h-4 w-4" />} title="Missing information" tone="text-amber-700">
          <ul className="list-disc space-y-1 pl-4 text-sm">
            {g.missing_information.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </Block>
        <Block icon={<ListChecks className="h-4 w-4" />} title="Recommended actions" tone="text-navy">
          <ol className="list-decimal space-y-1 pl-4 text-sm">
            {g.recommended_actions.support.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ol>
          <div className={cn("mt-3 rounded-lg px-3 py-2 text-sm", g.escalation.required ? "bg-red-50 text-red-800" : "bg-teal-50 text-teal-800")}>
            <span className="font-semibold">Escalation: {g.escalation.required ? `required → ${g.escalation.target}` : "not required"}.</span> {g.escalation.reason}
          </div>
        </Block>
      </div>
    </Card>
  );
}

function Block({ icon, title, tone, children }: { icon: JSX.Element; title: string; tone: string; children: React.ReactNode }) {
  return (
    <div className="bg-white p-4">
      <div className={cn("mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide", tone)}>
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function AgentCard({ a }: { a: AgentResult }) {
  const tone = { finding: "red", no_issue: "teal", inconclusive: "amber", context: "navy", not_run: "slate" }[a.status] as
    "red" | "teal" | "amber" | "navy" | "slate";
  const label = { finding: "finding", no_issue: "no issue found", inconclusive: "inconclusive", context: "context retrieved", not_run: "not used" }[a.status];
  return (
    <Card className={cn("flex flex-col", !a.used && "opacity-70")}>
      <div className="flex items-start gap-2 border-b border-slate-100 px-4 py-3">
        <div className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", a.used ? "bg-teal-50 text-teal-700" : "bg-slate-100 text-slate-400")}>
          {AGENT_ICON[a.key]}
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold leading-tight">{a.name}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge tone={a.used ? "teal" : "slate"}>{a.used ? "used" : "not used"}</Badge>
            {a.used && <Badge tone={tone}>{label}</Badge>}
          </div>
        </div>
      </div>
      <div className="flex-1 space-y-3 px-4 py-3 text-sm">
        {!a.used ? (
          <p className="text-slate-500">{a.reason_not_used}</p>
        ) : (
          <>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Investigated</div>
              <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
                {a.investigated.map((x) => (
                  <li key={x} className="flex gap-1.5">
                    <Search className="mt-0.5 h-3 w-3 shrink-0 text-slate-400" />
                    {x}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Main finding</div>
              <p className="mt-1 leading-snug">{a.finding}</p>
            </div>
            {a.evidence_ids.length > 0 && (
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Evidence</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {a.evidence_ids.slice(0, 8).map((i) => (
                    <a key={i} href={`#ev-${i}`} className="rounded border border-slate-200 px-1.5 py-px font-mono text-[10px] text-slate-500 hover:bg-slate-100">
                      {i.replace("EVD-", "")}
                    </a>
                  ))}
                  {a.evidence_ids.length > 8 && <span className="text-[10px] text-slate-400">+{a.evidence_ids.length - 8}</span>}
                </div>
              </div>
            )}
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Missing information</div>
              {a.missing_information.length ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
                  {a.missing_information.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-xs text-slate-400">None reported</p>
              )}
            </div>
          </>
        )}
      </div>
      {a.used && (
        <div className="border-t border-slate-100 px-4 py-2 font-mono text-[10px] text-slate-400">tools: {a.tools_called.join(", ")}</div>
      )}
    </Card>
  );
}

function Timeline({ d }: { d: CaseDetail }) {
  const items = d.diagnosis!.timeline;
  return (
    <Card>
      <CardHeader title="Unified technical timeline" icon={<Clock className="h-4 w-4 text-teal" />} right={<span className="text-[11px] text-slate-400">UTC · Europe/London = UTC in Nov</span>} />
      <ol className="relative px-4 py-3">
        {items.map((t, i) => (
          <li key={i} className="relative flex gap-3 pb-3 last:pb-0">
            {i < items.length - 1 && <span className="absolute left-[54px] top-5 h-full w-px bg-slate-200" />}
            <span className="w-11 shrink-0 pt-0.5 text-right font-mono text-xs font-semibold text-navy">{t.time}</span>
            <span
              className={cn(
                "relative z-10 mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ring-4 ring-white",
                t.kind === "session" || t.kind === "config_change" ? "bg-red-500" : t.kind === "event" ? "bg-amber-500" : "bg-teal",
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="text-sm leading-snug">{t.label}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1">
                <Badge tone={SOURCE_TONE[t.source_system] || "slate"}>{SOURCE_SHORT[t.source_system] || t.source_system}</Badge>
                <a href={`#ev-${t.evidence_id}`} className="font-mono text-[10px] text-slate-400 hover:text-navy">
                  {t.evidence_id}
                </a>
                {t.visibility_level !== "homeowner_safe" && <Badge tone={visTone[t.visibility_level]}>{visLabel[t.visibility_level]}</Badge>}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function PowerChart({ d }: { d: CaseDetail }) {
  const data = d.diagnosis!.power_series || [];
  return (
    <Card>
      <CardHeader title="Expected vs measured charger power" icon={<Zap className="h-4 w-4 text-teal" />} right={<span className="text-[11px] text-slate-400">kW · negative = supplying the home</span>} />
      <CardBody className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ left: -18, right: 8, top: 8 }}>
            <CartesianGrid stroke="#eef2f7" />
            <XAxis dataKey="time" tick={{ fontSize: 11 }} />
            <YAxis domain={[-3.5, 0.5]} tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="stepAfter" dataKey="expected_kw" name="Scheduled (HEMS)" stroke="#0B1F3A" strokeDasharray="5 4" dot={false} />
            <Line type="monotone" dataKey="measured_kw" name="Measured (charger API)" stroke="#14B8A6" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </CardBody>
    </Card>
  );
}

function DataModelPanel({ dm }: { dm: DataModel }) {
  return (
    <Card>
      <CardHeader title="Data sources → unified data model" icon={<Database className="h-4 w-4 text-teal" />} right={<span className="text-[11px] text-slate-400">source-native schemas mapped through verified identifiers</span>} />
      <CardBody className="space-y-5">
        <div>
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <GitMerge className="h-3.5 w-3.5" /> Identity resolution chain
          </div>
          <div className="flex flex-wrap items-stretch gap-1.5">
            {dm.identity_chain.map((c, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-teal-700">{c.system}</div>
                  <div className="font-mono text-xs">{c.id}</div>
                  <div className="max-w-[170px] text-[11px] leading-tight text-slate-500">{c.label}</div>
                </div>
                {i < dm.identity_chain.length - 1 && <ArrowRight className="h-4 w-4 shrink-0 text-slate-300" />}
              </div>
            ))}
          </div>
        </div>

        <div className="grid gap-4 2xl:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Native → canonical (live records)</div>
            <div className="space-y-2">
              {dm.transforms.map((t, i) => (
                <div key={i} className="rounded-lg border border-slate-200 p-2.5 text-xs">
                  <Badge tone={SOURCE_TONE[t.source_system]}>{SOURCE_SHORT[t.source_system]}</Badge>
                  <div className="mt-1.5 font-mono text-[11px] text-slate-600">{t.native}</div>
                  <div className="my-1 flex items-center gap-1 text-[10px] text-slate-400">
                    <ArrowRight className="h-3 w-3" /> {t.rule}
                  </div>
                  <div className="font-mono text-[11px] text-teal-700">{t.canonical}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Source device mappings</div>
              <table className="w-full text-xs">
                <tbody className="divide-y divide-slate-100">
                  {dm.device_mappings.map((m, i) => (
                    <tr key={i}>
                      <td className="py-1 pr-2 font-mono">{m.device_id}</td>
                      <td className="py-1 pr-2">{SOURCE_SHORT[m.source_system]}</td>
                      <td className="py-1 pr-2 font-mono">{m.source_device_id}</td>
                      <td className="py-1 pr-2 text-slate-500">{m.mapping_method.replace(/_/g, " ")}</td>
                      <td className="py-1">{m.verified ? <CheckCircle2 className="h-3.5 w-3.5 text-teal" /> : <XCircle className="h-3.5 w-3.5 text-red-500" />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {dm.unmapped_records.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-900">
                <div className="font-semibold">Rejected identity matches</div>
                {dm.unmapped_records.map((u, i) => (
                  <div key={i} className="mt-1">
                    <span className="font-mono">{u.source_id}</span> ({SOURCE_SHORT[u.source_system]}): {u.reason}
                  </div>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
              {dm.sources.map((s) => (
                <div key={s.source_system} className="rounded-lg border border-slate-200 p-2">
                  <div className="font-semibold">{s.label}</div>
                  <div className="mt-0.5 text-[10px] text-slate-500">{s.key_fields}</div>
                  <div className="mt-1 text-[10px] text-slate-400">
                    {s.tables.map((t) => `${t.table} (${t.records})`).join(", ")}
                  </div>
                </div>
              ))}
            </div>
            <div className="text-[11px] text-slate-500">
              <span className="font-semibold">Conventions:</span> {Object.entries(dm.conventions).map(([k, v]) => `${k}: ${v}`).join(" · ")}
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function EvidenceTable({ d }: { d: CaseDetail }) {
  const shared = new Set(d.consent?.evidence_scope || []);
  return (
    <Card>
      <CardHeader
        title={`Evidence records (${d.diagnosis!.evidence.length})`}
        icon={<FileText className="h-4 w-4 text-teal" />}
        right={d.consent && <span className="text-[11px] text-slate-500">Homeowner consent {d.consent.consent_id} · {fmtTime(d.consent.approved_at)}</span>}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-left text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">Evidence ID</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Record</th>
              <th className="px-3 py-2">Time</th>
              <th className="px-3 py-2">Summary</th>
              <th className="px-3 py-2">Visibility</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {d.diagnosis!.evidence.map((e) => (
              <tr key={e.evidence_id} id={`ev-${e.evidence_id}`} className="target:bg-teal-50">
                <td className="px-3 py-1.5 font-mono">{e.evidence_id}</td>
                <td className="px-3 py-1.5">{e.source_label}</td>
                <td className="px-3 py-1.5 font-mono text-slate-500">{e.source_record_id}</td>
                <td className="px-3 py-1.5 whitespace-nowrap text-slate-500">{e.timestamp_utc ? e.timestamp_utc.slice(11, 16) : "—"}</td>
                <td className="px-3 py-1.5">{e.summary}</td>
                <td className="px-3 py-1.5">
                  <div className="flex gap-1">
                    <Badge tone={visTone[e.visibility_level]}>{visLabel[e.visibility_level]}</Badge>
                    {shared.has(e.evidence_id) && <Badge tone="teal">shared by homeowner</Badge>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Copilot({ d, onAsked }: { d: CaseDetail; onAsked: () => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [extra, setExtra] = useState<Record<string, AskResult>>({});
  const end = useRef<HTMLDivElement>(null);
  const thread = d.messages.filter(
    (m) => (m.message_type === "question" && m.sender_role === "support") || m.message_type === "copilot_answer" || (m.sender_role === "homeowner" && m.message_type === "question"),
  );

  useEffect(() => {
    // Newer Chromium returns a Promise from scrollIntoView; never return it as an effect cleanup.
    end.current?.scrollIntoView({ block: "nearest" });
  }, [thread.length]);

  const ask = async (question: string) => {
    if (!question.trim()) return;
    setBusy(true);
    setQ("");
    try {
      const r = await api.ask(d.case.case_id, question);
      setExtra((x) => ({ ...x, [r.answer]: r }));
      onAsked();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="flex max-h-[640px] flex-col">
      <CardHeader title="Support copilot" icon={<MessageSquare className="h-4 w-4 text-teal" />} right={<span className="text-[11px] text-slate-400">answers cite agent evidence</span>} />
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {thread.map((m) => {
          const meta = extra[m.message];
          if (m.sender_role === "homeowner")
            return (
              <div key={m.message_id} className="rounded-lg border border-dashed border-slate-200 px-3 py-2 text-xs text-slate-500">
                Homeowner asked in app: <span className="italic text-navy">“{m.message}”</span>
              </div>
            );
          if (m.message_type === "question")
            return (
              <div key={m.message_id} className="ml-8 rounded-lg bg-navy px-3 py-2 text-sm text-white">
                {m.message}
              </div>
            );
          return (
            <div key={m.message_id} className="mr-4 rounded-lg bg-teal-50 px-3 py-2 text-sm">
              <div className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-teal-700">
                <Sparkles className="h-3 w-3" /> Copilot
              </div>
              {m.message}
              {meta && meta.agents_used.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {meta.agents_used.map((a) => (
                    <Badge key={a} tone="navy">{a.replace(" Agent", "")}</Badge>
                  ))}
                  <Badge tone="slate">{meta.mode}</Badge>
                </div>
              )}
            </div>
          );
        })}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner className="text-teal" /> Orchestrator is consulting the agents…
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="border-t border-slate-100 p-3">
        <div className="mb-2 flex flex-wrap gap-1">
          {QUESTIONS.map((x) => (
            <button key={x} disabled={busy} onClick={() => ask(x)} className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-600 hover:border-teal hover:text-teal-700">
              {x}
            </button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            ask(q);
          }}
        >
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a follow-up question…" className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-teal" />
          <Button type="submit" variant="teal" disabled={busy || !q.trim()}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </Card>
  );
}

function Workflow({ d, reload }: { d: CaseDetail; reload: () => void }) {
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<string | null>(null);
  const [esc, setEsc] = useState<Escalation | null>(d.escalation);
  const [busy, setBusy] = useState<string | null>(null);
  const notes = d.messages.filter((m) => m.message_type === "note" || m.message_type === "status_change");

  const run = async (k: string, fn: () => Promise<void>) => {
    setBusy(k);
    try {
      await fn();
      reload();
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader title="Support workflow" icon={<Plug className="h-4 w-4 text-teal" />} right={<Badge tone={statusTone[d.case.status]}>{d.case.status}</Badge>} />
      <CardBody className="space-y-4">
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" disabled={!!busy} onClick={() => run("draft", async () => setDraft((await api.draft(d.case.case_id)).draft))}>
            {busy === "draft" ? <Spinner /> : <FileText className="h-4 w-4" />} Draft response
          </Button>
          <Button variant="secondary" disabled={!!busy} onClick={() => run("esc", async () => setEsc(await api.escalate(d.case.case_id)))}>
            {busy === "esc" ? <Spinner /> : <AlertTriangle className="h-4 w-4" />} Prepare escalation
          </Button>
        </div>

        {draft && (
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="mb-1 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Customer response draft <Badge tone="teal">homeowner-safe facts only</Badge>
            </div>
            <textarea className="h-44 w-full resize-y rounded border border-slate-200 bg-white p-2 text-sm" defaultValue={draft} />
          </div>
        )}

        {esc && (
          <div className="rounded-lg border border-red-200 bg-red-50/50 p-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-red-800">Escalation → {esc.target}</span>
              {d.case.status !== "Escalated" && (
                <Button className="px-2 py-0.5 text-xs" onClick={() => run("st", () => api.status(d.case.case_id, "Escalated").then(() => undefined))}>
                  Mark escalated
                </Button>
              )}
            </div>
            <p className="mt-1 text-slate-700">{esc.summary}</p>
            {esc.device.charger_ref && (
              <p className="mt-1 font-mono text-[11px] text-slate-600">
                {esc.device.charger_model} · fw {esc.device.charger_firmware} · {esc.device.charger_ref} {esc.session_refs.length > 0 && `· session ${esc.session_refs.join(", ")}`}
              </p>
            )}
            <div className="mt-2 font-semibold text-slate-700">Requested information</div>
            <ul className="list-disc pl-4 text-slate-700">
              {esc.requested_information.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <p className="mt-2 text-slate-500">
              {esc.evidence.length} evidence records attached. {esc.excluded}
            </p>
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            <StickyNote className="h-3.5 w-3.5" /> Internal notes
          </div>
          <ul className="mb-2 max-h-40 space-y-1.5 overflow-y-auto">
            {notes.map((n) => (
              <li key={n.message_id} className="rounded bg-slate-50 px-2 py-1.5 text-xs">
                <span className="text-slate-400">{n.created_at.slice(11, 16)} · {n.sender_role}</span> {n.message}
              </li>
            ))}
          </ul>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) run("note", () => api.note(d.case.case_id, note).then(() => setNote("")));
            }}
          >
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add an internal note" className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-teal" />
            <Button type="submit" disabled={!note.trim() || !!busy}>Add</Button>
          </form>
        </div>
      </CardBody>
    </Card>
  );
}
