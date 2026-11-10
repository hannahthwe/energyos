import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, Building2, History, Inbox, Plug, Sparkles, User } from "lucide-react";
import { api, type CaseDetail, type CaseSummary } from "@/lib/api";
import { Badge, Button, Card, CardBody, CardHeader, Spinner, statusTone } from "@/components/ui";
import { cn, fmtTime } from "@/lib/utils";

export default function Queue() {
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .cases()
      .then((c) => {
        setCases(c);
        setError(null);
        setSelected((cur) => cur ?? c[0]?.case_id ?? null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 4000); // new homeowner cases appear without a reload
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (selected) api.case(selected).then(setDetail);
  }, [selected]);

  if (error) return <Card className="p-6 text-sm text-red-700">Cannot reach the ConnectedCare API. Is the backend running on port 8000? {error}</Card>;
  if (!cases) return <Spinner />;

  return (
    <div className="grid gap-5 lg:grid-cols-[420px_1fr]">
      <Card className="h-fit">
        <CardHeader title={`Case queue (${cases.length})`} icon={<Inbox className="h-4 w-4 text-teal" />} />
        <ul className="divide-y divide-slate-100">
          {cases.map((c) => (
            <li key={c.case_id}>
              <button
                onClick={() => setSelected(c.case_id)}
                className={cn("w-full px-4 py-3 text-left transition hover:bg-slate-50", selected === c.case_id && "bg-teal-50/60")}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-slate-500">{c.case_id}</span>
                  <Badge tone={statusTone[c.status]}>{c.status}</Badge>
                  {c.escalation_required && c.status !== "Resolved" && (
                    <Badge tone="red">
                      <AlertTriangle className="h-3 w-3" /> escalation
                    </Badge>
                  )}
                  <span className="ml-auto text-[11px] text-slate-400">{c.created_at.slice(0, 16).replace("T", " ")}</span>
                </div>
                <div className="mt-1 text-sm font-medium">{c.title}</div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {c.customer_name} · {c.origin === "homeowner_app" ? "via Helio app" : "CRM import"}
                  {c.scenario !== "-" && ` · scenario ${c.scenario}`}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      {detail && <CasePreview d={detail} />}
    </div>
  );
}

function CasePreview({ d }: { d: CaseDetail }) {
  return (
    <div className="space-y-5">
      <Card>
        <CardBody className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <span className="font-mono">{d.case.case_id}</span>
              <Badge tone={statusTone[d.case.status]}>{d.case.status}</Badge>
              <span>opened {fmtTime(d.case.created_at)}</span>
            </div>
            <h1 className="mt-1 text-lg font-semibold">{d.case.title}</h1>
            <p className="mt-1 whitespace-pre-line text-sm text-slate-600">{d.case.description}</p>
          </div>
          {d.diagnosis && (
            <Link to={`/support/cases/${d.case.case_id}`}>
              <Button variant="teal">
                Open AI investigation <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          )}
        </CardBody>
        <div className="border-t border-slate-100 bg-gradient-to-r from-teal-50 to-white px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-teal-700">
            <Sparkles className="h-3.5 w-3.5" /> AI diagnostic summary
          </div>
          <p className="mt-1 text-sm">{d.case.diagnostic_summary}</p>
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader title="Customer (CRM)" icon={<User className="h-4 w-4 text-teal" />} right={<Badge tone="navy">{d.customer.crm_ref}</Badge>} />
          <CardBody className="grid grid-cols-2 gap-3 text-sm">
            <Field k="Name" v={d.customer.name} />
            <Field k="Customer ID" v={d.customer.customer_id} mono />
            <Field k="Email" v={d.customer.email} />
            <Field k="Contact preference" v={d.customer.support_contact_preference.replace("_", "-")} />
            <Field k="Tenant" v={d.customer.tenant_id} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Installation" icon={<Building2 className="h-4 w-4 text-teal" />} right={<Badge tone="navy">{d.installation.crm_ref}</Badge>} />
          <CardBody className="grid grid-cols-2 gap-3 text-sm">
            <Field k="Installation ID" v={d.installation.installation_id} mono />
            <Field k="Status" v={d.installation.installation_status} />
            <Field k="Address" v={d.installation.address} />
            <Field k="Timezone" v={d.installation.timezone} />
            <Field k="Installed" v={d.installation.installed_on} />
            <Field k="Installer" v={d.installation.installer} />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Registered devices" icon={<Plug className="h-4 w-4 text-teal" />} />
        <DeviceTable devices={d.devices} />
      </Card>

      <Card>
        <CardHeader title="Previous support interactions" icon={<History className="h-4 w-4 text-teal" />} />
        <ul className="divide-y divide-slate-100 text-sm">
          {d.previous_interactions.map((p) => (
            <li key={p.interaction_id} className="px-4 py-3">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span className="font-mono">{p.crm_ref}</span> · {p.opened} · {p.channel}
                <Badge tone="slate">{p.status}</Badge>
              </div>
              <div className="font-medium">{p.subject}</div>
              <div className="text-slate-600">{p.resolution}</div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function Field({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{k}</div>
      <div className={cn("text-navy", mono && "font-mono text-xs")}>{v}</div>
    </div>
  );
}

export function DeviceTable({ devices }: { devices: CaseDetail["devices"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2">Device</th>
            <th className="px-4 py-2">Type</th>
            <th className="px-4 py-2">Manufacturer / model</th>
            <th className="px-4 py-2">Firmware</th>
            <th className="px-4 py-2">Connectivity</th>
            <th className="px-4 py-2">Verified source IDs</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {devices.map((d) => (
            <tr key={d.device_id}>
              <td className="px-4 py-2 font-mono text-xs">{d.device_id}</td>
              <td className="px-4 py-2">{d.device_type.replace("_", " ")}</td>
              <td className="px-4 py-2">
                {d.manufacturer} {d.model}
              </td>
              <td className="px-4 py-2 font-mono text-xs">{d.firmware_version}</td>
              <td className="px-4 py-2">
                <Badge tone={d.connectivity_status === "online" ? "teal" : "red"}>{d.connectivity_status}</Badge>
              </td>
              <td className="px-4 py-2">
                <div className="flex flex-wrap gap-1">
                  {Object.entries(d.source_ids).map(([k, v]) => (
                    <Badge key={k} tone="slate" className="font-mono font-normal">
                      {k.replace("_backend", "").replace("_integration", "")}: {v}
                    </Badge>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
