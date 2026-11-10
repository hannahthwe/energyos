import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { Activity, Bot, Cpu } from "lucide-react";
import { api, type DemoState } from "@/lib/api";
import { Badge, SyntheticLabel } from "@/components/ui";

export function useDemo() {
  const [demo, setDemo] = useState<DemoState | null>(null);
  const reload = () => api.demo().then(setDemo).catch(() => setDemo(null));
  useEffect(() => {
    reload();
  }, []);
  return { demo, reload };
}

export default function Layout() {
  const { demo, reload } = useDemo();
  const [switching, setSwitching] = useState(false);

  const change = async (code: string) => {
    setSwitching(true);
    await api.setScenario(code);
    await reload();
    setSwitching(false);
    window.dispatchEvent(new Event("cc:scenario"));
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-navy-800 bg-navy text-white">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-4 px-5 py-3">
          <Link to="/support" className="flex items-center gap-2">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-teal">
              <Activity className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold">ConnectedCare AI</div>
              <div className="text-[11px] text-slate-300">HelioHEMS Support Console</div>
            </div>
          </Link>
          <div className="ml-auto flex flex-wrap items-center gap-3 text-xs">
            <Link to="/app" className="rounded-md bg-teal px-2 py-1 font-medium text-white hover:bg-teal-600">Homeowner app</Link>
            <SyntheticLabel />
            {demo && (
              <Badge tone={demo.ai_mode === "claude" ? "teal" : "slate"} className="bg-white/10 text-white ring-white/20">
                {demo.ai_mode === "claude" ? <Bot className="h-3 w-3" /> : <Cpu className="h-3 w-3" />}
                {demo.ai_mode === "claude" ? `Claude · ${demo.model}` : "Offline deterministic mode"}
              </Badge>
            )}
            {demo && (
              <label className="flex items-center gap-2 text-slate-300">
                Demo scenario
                <select
                  className="rounded-md border border-white/20 bg-navy-800 px-2 py-1 text-white"
                  value={demo.scenario}
                  disabled={switching}
                  onChange={(e) => change(e.target.value)}
                >
                  {demo.scenarios.map((s) => (
                    <option key={s.code} value={s.code}>
                      {s.code}: {s.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-5 py-5">
        <Outlet />
      </main>
    </div>
  );
}
