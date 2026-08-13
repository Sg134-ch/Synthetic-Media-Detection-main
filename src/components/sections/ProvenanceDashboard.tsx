"use client";

import { motion } from "framer-motion";
import { ShieldCheck, ShieldAlert, ShieldQuestion, Fingerprint, Info } from "lucide-react";
import BorderGlow from "@/components/reactbits/BorderGlow";
import DecryptedText from "@/components/reactbits/DecryptedText";

interface ProvenanceMetadata {
  ai_indicators?: string[];
  details?: string[];
}

interface ProvenanceDashboardProps {
  metadata?: ProvenanceMetadata | undefined;
}

type C2paStatus = "verified" | "suspicious" | "absent";

/**
 * The backend does not (yet) return a dedicated, structured C2PA field —
 * presence/validity is currently embedded as plain-language strings inside
 * metadata.ai_indicators / metadata.details (see metadata_analyzer.py).
 * This reads those strings rather than assuming a field that doesn't exist.
 */
function getC2paStatus(metadata?: ProvenanceMetadata): { status: C2paStatus; note?: string } {
  const allLines = [...(metadata?.ai_indicators ?? []), ...(metadata?.details ?? [])];
  const c2paLine = allLines.find((line) => line.toLowerCase().includes("c2pa"));

  if (!c2paLine) return { status: "absent" };

  const isSuspicious = c2paLine.includes("🚨") || c2paLine.toLowerCase().includes("suspicious");
  return {
    status: isSuspicious ? "suspicious" : "verified",
    note: c2paLine.replace("🚨", "").replace("📋", "").trim(),
  };
}

const STATUS_CONFIG: Record<C2paStatus, { label: string; color: string; icon: typeof ShieldCheck; bg: string }> = {
  verified: {
    label: "Content Credentials Found",
    color: "#22c55e",
    icon: ShieldCheck,
    bg: "rgba(34,197,94,0.08)",
  },
  suspicious: {
    label: "Content Credentials Flagged",
    color: "#eab308",
    icon: ShieldAlert,
    bg: "rgba(234,179,8,0.08)",
  },
  absent: {
    label: "No Content Credentials Found",
    color: "#4B5260",
    icon: ShieldQuestion,
    bg: "rgba(75,82,96,0.08)",
  },
};

export default function ProvenanceDashboard({ metadata }: ProvenanceDashboardProps) {
  const { status, note } = getC2paStatus(metadata);
  const config = STATUS_CONFIG[status];
  const Icon = config.icon;

  return (
    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="relative h-full">
      <BorderGlow
        animated={true}
        glowColor="186 100% 74%"
        backgroundColor="var(--bg)"
        borderRadius={0}
        glowRadius={30}
        glowIntensity={0.6}
        coneSpread={15}
        className="w-full h-full"
      >
        <div className="border border-border bg-surface overflow-hidden h-full">
          <div className="p-4 border-b border-border flex items-center gap-3">
            <div className="w-8 h-8 border border-border bg-background flex items-center justify-center shrink-0">
              <Fingerprint className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h4 className="text-sm text-primary font-medium">
                <DecryptedText text="Provenance & Watermark Verification" speed={60} maxIterations={15} animateOn="hover" />
              </h4>
              <p className="text-[10px] text-muted uppercase tracking-[0.1em]">
                Independent from the AI-detection verdict above
              </p>
            </div>
          </div>

          <div className="p-4 space-y-3">
            {/* ── C2PA Content Credentials — real, backend-verified signal ── */}
            <div
              className="flex items-start gap-3 p-3 border border-border"
              style={{ backgroundColor: config.bg }}
            >
              <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: config.color }} />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium" style={{ color: config.color }}>
                  {config.label}
                </p>
                <p className="text-[11px] text-muted mt-1 leading-relaxed">
                  {note ??
                    "No C2PA Content Credentials manifest was detected in this file. This does not by itself indicate manipulation — many authentic files are never signed."}
                </p>
              </div>
            </div>

            {/* ── SynthID — honestly marked as not implemented, not faked ── */}
            <div className="flex items-start gap-3 p-3 border border-border border-dashed opacity-70">
              <Info className="w-4 h-4 mt-0.5 shrink-0 text-muted" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-muted">Google SynthID Watermark — Not Yet Available</p>
                <p className="text-[11px] text-muted mt-1 leading-relaxed">
                  SynthID detection is not currently implemented in the backend. This panel will populate once
                  that capability is added.
                </p>
              </div>
            </div>

            <p className="text-[10px] text-muted leading-relaxed pt-1">
              Provenance signals are complementary to, not a substitute for, the AI-detection verdict above.
              A missing credential does not indicate a file is synthetic — only that it was never signed, or
              the signature was lost in transit (e.g. re-encoding, screenshots).
            </p>
          </div>
        </div>
      </BorderGlow>
    </motion.div>
  );
}