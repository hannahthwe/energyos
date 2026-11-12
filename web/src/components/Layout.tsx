import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { Activity } from "lucide-react";
import { api, type DemoState } from "@/lib/api";

let demoOnce: Promise<DemoState> | null = null;

/** True when the backend runs with CONNECTEDCARE_DEMO_MODE=1 (presentation mode hides internals). */
export function useDemoMode(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    (demoOnce ||= api.demo()).then((d) => setOn(!!d.demo_mode)).catch(() => setOn(false));
  }, []);
  return on;
}

/**
 * Presenter-only scenario switch, invisible on screen: press Shift+Alt+A..E, or open any page with
 * ?scenario=D. Fires "cc:scenario" so open pages reload their data.
 */
export function useHiddenScenarioSwitch(onChange?: () => void) {
  useEffect(() => {
    const apply = async (code: string) => {
      await api.setScenario(code);
      onChange?.();
      window.dispatchEvent(new Event("cc:scenario"));
    };
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("scenario")?.toUpperCase();
    if (fromUrl && /^[A-E]$/.test(fromUrl)) {
      params.delete("scenario");
      const q = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (q ? `?${q}` : ""));
      apply(fromUrl);
    }
    const onKey = (e: KeyboardEvent) => {
      const m = /^Key([A-E])$/.exec(e.code);
      if (m && e.shiftKey && e.altKey && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        apply(m[1]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

export function useDemo() {
  const [demo, setDemo] = useState<DemoState | null>(null);
  const reload = () => api.demo().then(setDemo).catch(() => setDemo(null));
  useEffect(() => {
    reload();
  }, []);
  return { demo, reload };
}

export default function Layout() {
  useHiddenScenarioSwitch();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-navy-800 bg-navy text-white">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-4 px-5 py-3">
          <Link to="/support" className="flex items-center gap-2">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-sky">
              <Activity className="h-4 w-4" />
            </div>
            <div className="leading-tight">
              <div className="text-sm font-semibold">ConnectedCare AI</div>
              <div className="text-[11px] text-slate-300">HelioHEMS Support Console</div>
            </div>
          </Link>
          <div className="ml-auto flex items-center gap-2 text-xs text-slate-300">
            <div className="grid h-7 w-7 place-items-center rounded-full bg-white/10 text-[11px] font-semibold text-white">SA</div>
            Support agent
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-5 py-5">
        <Outlet />
      </main>
    </div>
  );
}
