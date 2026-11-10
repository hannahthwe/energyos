import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const fmtTime = (iso?: string | null) => (iso ? iso.replace("T", " ").replace("Z", "").slice(0, 16) + " UTC" : "—");
