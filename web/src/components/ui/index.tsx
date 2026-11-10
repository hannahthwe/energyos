// Minimal shadcn/ui-style primitives (copied-in components, no runtime dependency).
import * as React from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border border-slate-200 bg-white shadow-sm", className)} {...p} />;
}

export function CardHeader({ title, icon, right, className }: { title: string; icon?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3", className)}>
      <div className="flex items-center gap-2 text-sm font-semibold text-navy">
        {icon}
        {title}
      </div>
      {right}
    </div>
  );
}

export function CardBody({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...p} />;
}

type BtnVariant = "primary" | "secondary" | "ghost" | "teal";
export function Button({ variant = "primary", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant }) {
  const v: Record<BtnVariant, string> = {
    primary: "bg-navy text-white hover:bg-navy-700",
    teal: "bg-teal text-white hover:bg-teal-600",
    secondary: "border border-slate-200 bg-white text-navy hover:bg-slate-50",
    ghost: "text-navy hover:bg-slate-100",
  };
  return (
    <button
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50", v[variant], className)}
      {...p}
    />
  );
}

type Tone = "slate" | "teal" | "amber" | "red" | "navy" | "violet";
export function Badge({ tone = "slate", className, ...p }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  const t: Record<Tone, string> = {
    slate: "bg-slate-100 text-slate-600",
    teal: "bg-teal-50 text-teal-700 ring-1 ring-teal-100",
    amber: "bg-amber-50 text-amber-700 ring-1 ring-amber-100",
    red: "bg-red-50 text-red-700 ring-1 ring-red-100",
    navy: "bg-navy-50 text-navy ring-1 ring-navy-100",
    violet: "bg-violet-50 text-violet-700 ring-1 ring-violet-100",
  };
  return <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold", t[tone], className)} {...p} />;
}

export function SyntheticLabel() {
  return <Badge tone="amber" className="uppercase tracking-wide">Synthetic demo data</Badge>;
}

export const statusTone: Record<string, Tone> = { New: "teal", Investigating: "amber", Escalated: "red", Resolved: "slate" };
export const visTone: Record<string, Tone> = { homeowner_safe: "teal", support_internal: "navy", restricted: "red" };
export const visLabel: Record<string, string> = { homeowner_safe: "homeowner-safe", support_internal: "internal", restricted: "restricted" };

export function Spinner({ className }: { className?: string }) {
  return <span className={cn("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent", className)} />;
}
