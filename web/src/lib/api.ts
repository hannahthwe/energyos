// Typed client for the ConnectedCare support API. Every call sends the demo
// support role; the backend enforces what that role may see.

export type Visibility = "homeowner_safe" | "support_internal" | "restricted";

export interface Evidence {
  evidence_id: string;
  source_system: string;
  source_label: string;
  source_record_id: string;
  timestamp_utc: string | null;
  evidence_type: string;
  summary: string;
  visibility_level: Visibility;
}

export interface Cited {
  text: string;
  evidence_ids: string[];
  agent?: string | null;
}

export interface AgentResult {
  key: string;
  name: string;
  responsibility: string;
  used: boolean;
  reason_not_used: string | null;
  tools_called: string[];
  investigated: string[];
  status: "finding" | "no_issue" | "inconclusive" | "context" | "not_run";
  finding: string;
  observations: Cited[];
  evidence_ids: string[];
  missing_information: string[];
  signals: Record<string, unknown>;
}

export interface TimelineItem {
  timestamp_utc: string;
  time: string;
  label: string;
  evidence_id: string;
  source_system: string;
  kind: string;
  visibility_level: Visibility;
}

export interface Diagnosis {
  run_id: string;
  question: string;
  mode: string;
  mode_note: string | null;
  scenario: { code: string; name: string };
  installation_id: string;
  incident_utc: string;
  likely_cause: string;
  headline: string;
  answer: string;
  observed_facts: Cited[];
  interpretation: Cited[];
  confidence: { level: string; explanation: string };
  missing_information: string[];
  recommended_actions: { support: string[]; homeowner: string[] };
  escalation: { required: boolean; target: string | null; reason: string };
  agents: AgentResult[];
  evidence: Evidence[];
  timeline: TimelineItem[];
  power_series?: { time: string; expected_kw: number; measured_kw: number }[];
}

export interface CaseSummary {
  case_id: string;
  title: string;
  status: CaseStatus;
  created_at: string;
  customer_id: string;
  customer_name: string;
  installation_id: string;
  escalation_required: boolean;
  diagnostic_summary: string;
  scenario: string;
  origin: string;
}

export type CaseStatus = "New" | "Investigating" | "Escalated" | "Resolved";

export interface Message {
  message_id: string;
  sender_role: string;
  message_type: string;
  message: string;
  created_at: string;
  visibility_level: Visibility;
}

export interface Device {
  device_id: string;
  device_type: string;
  manufacturer: string;
  model: string;
  firmware_version: string;
  connectivity_status: string;
  source_ids: Record<string, string>;
}

export interface CaseDetail {
  case: CaseSummary & { description: string; tenant_id: string; live_data_matches: boolean };
  customer: { customer_id: string; tenant_id: string; name: string; email: string; support_contact_preference: string; crm_ref: string };
  installation: { installation_id: string; timezone: string; installation_status: string; address: string; installed_on: string; installer: string; crm_ref: string };
  devices: Device[];
  previous_interactions: { interaction_id: string; crm_ref: string; subject: string; opened: string; status: string; resolution: string; channel: string }[];
  messages: Message[];
  evidence: { evidence_id: string; summary: string; visibility_level: Visibility; shared_by_homeowner: boolean }[];
  consent: { consent_id: string; approved_at: string; evidence_scope: string[] } | null;
  diagnosis: Diagnosis | null;
  escalation: Escalation | null;
}

export interface Escalation {
  case_id: string;
  target: string;
  external_partner: boolean;
  prepared_at: string;
  summary: string;
  device: { charger_model: string; charger_firmware: string; charger_ref: string };
  session_refs: string[];
  timeline: TimelineItem[];
  evidence: Evidence[];
  requested_information: string[];
  excluded: string;
}

export interface DataModel {
  sources: { source_system: string; label: string; key_fields: string; tables: { table: string; records: number }[] }[];
  canonical_entities: { entity: string; table: string; records: number }[];
  device_mappings: { device_id: string; source_system: string; source_device_id: string; mapping_method: string; verified: boolean; evidence_ref: string }[];
  identity_chain: { system: string; id: string; label: string }[];
  transforms: { source_system: string; native: string; canonical: string; rule: string }[];
  unmapped_records: { source_system: string; source_id: string; reason: string }[];
  conventions: Record<string, string>;
}

export interface DemoState {
  scenario: string;
  scenarios: { code: string; name: string; summary: string }[];
  ai_mode: "claude" | "offline";
  model: string | null;
}

export interface AskResult {
  question: string;
  answer: string;
  mode: string;
  mode_note: string | null;
  agents_used: string[];
  evidence_ids: string[];
  agents: AgentResult[] | null;
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Demo-Role": "support", ...(init.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const api = {
  demo: () => req<DemoState>("/api/demo"),
  setScenario: (scenario: string) => post("/api/demo/scenario", { scenario }),
  cases: () => req<CaseSummary[]>("/api/support/cases"),
  case: (id: string) => req<CaseDetail>(`/api/support/cases/${id}`),
  ask: (id: string, question: string) => post<AskResult>(`/api/support/cases/${id}/ask`, { question }),
  note: (id: string, text: string) => post<Message>(`/api/support/cases/${id}/notes`, { text }),
  draft: (id: string) => post<{ draft: string; based_on_evidence: string[] }>(`/api/support/cases/${id}/draft-response`),
  escalate: (id: string) => post<Escalation>(`/api/support/cases/${id}/escalation`),
  status: (id: string, status: CaseStatus) => req(`/api/support/cases/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  dataModel: () => req<DataModel>("/api/support/data-model"),
};
